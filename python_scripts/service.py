from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from collections.abc import Callable, Iterable
from pathlib import Path

from .config import DOTENV_PATH, hydrate_env, load_dotenv
from .account_provider_store import delete_account, load_accounts, public_account, save_accounts, upsert_account
from .db_store import get_all_keys, get_key, init_db, upsert_key, increment_model_usage, get_model_usage_stats, get_manual_order, save_manual_order, log_request, get_model_probe_results, save_model_probe_result, delete_model_probe_results_for_provider, delete_request_logs_for_provider
from .errors import classify_error, is_permanent_unavailable_category, remediation_suggestion
from .health_store import load_health, temporary_disabled_models, upsert_health, delete_health_for_provider
from .openai_relay import OpenAIRelay
from .preferred_model_store import load_preferred_model, save_preferred_model
from .provider_adapter import ProviderAdapter
from .provider_catalog import configured_provider_names, get_model_capabilities, get_provider, get_provider_model_hints, list_providers
from .provider_errors import ProviderError, ProviderHTTPError
from .provider_routing import AliasName, PUBLIC_MODEL_ALIASES, ResolvedModelRequest, choose_candidates, resolve_alias_candidates, resolve_model_request
from .provider_transport import Transport, UrlLibTransport
from .request_limiter import RequestLimiterGate
from .token_budgeting import resolve_token_budget, shrink_budget_after_limit_error
from .token_limit_store import load_token_limits, upsert_token_limit
from .token_policy import model_default_output_tokens, model_default_timeout_seconds, probe_output_tokens, response_token_budget, trim_prompt

JsonObject = dict[str, object]

logger = logging.getLogger(__name__)


def _looks_like_embedded_provider_error(content: str | None) -> bool:
    text = (content or '').lower()
    return any(
        token in text
        for token in (
            '[qoder error',
            'pricingurl',
            'requires a subscription',
            'subscription required',
            '"code":"112"',
            '"code": "112"',
            'code 112',
        )
    )


@dataclass(frozen=True)
class ProbeResult:
    provider: str
    model: str
    ok: bool
    actual_model: str | None = None
    content: str | None = None
    error: str | None = None
    category: str | None = None
    status: int | None = None
    suggestion: str | None = None


@dataclass(frozen=True)
class ResolvedOpenAIRequest:
    provider: str | None
    model: str
    alias: AliasName | None


@dataclass(frozen=True)
class OpenAIForwardResult:
    ok: bool
    provider: str
    model: str
    status: int
    headers: dict[str, str]
    body: bytes
    content: str | None = None
    error: str | None = None
    category: str | None = None
    suggestion: str | None = None
    stream_chunks: Iterable[bytes] | None = None


class ProxyService:
    def __init__(
        self,
        transport: Transport | None = None,
        health_path: str = '',
        preferred_model_path: str = '',
        token_limit_path: Path | None = None,
        health_ttl_seconds: int = 300,
        dotenv_path: str = '',
        request_timeout_seconds: int = 12,
        outbound_rpm: int = 60,
        debug_log: Callable[..., None] | None = None,
    ) -> None:
        self.dotenv_path = Path(dotenv_path) if dotenv_path else DOTENV_PATH
        hydrate_env(self.dotenv_path)
        
        self.db_url = os.environ.get('DATABASE_URL')
        if not self.db_url:
            raise ValueError("DATABASE_URL is not set in environment or .env file.")
            
        init_db(self.db_url)
        
        # Migrate existing keys from .env if they don't exist in the database
        env_values = load_dotenv(self.dotenv_path)
        db_values = get_all_keys(self.db_url)
        
        for key, value in env_values.items():
            if key.endswith('_API_KEY') and key not in db_values:
                upsert_key(self.db_url, key, value)
                
        # Ensure ADMIN_PASSWORD exists for web console
        self._ensure_admin_password()
        
        self.transport = transport if transport is not None else UrlLibTransport()
        self.health_path = Path(health_path) if health_path else None
        self.preferred_model_path = Path(preferred_model_path) if preferred_model_path else None
        self.token_limit_path = token_limit_path
        self.health_ttl_seconds = health_ttl_seconds if health_ttl_seconds is not None else 600
        self.request_timeout_seconds = request_timeout_seconds
        self.request_limiter = RequestLimiterGate(outbound_rpm, 60)
        self.debug_log = debug_log
        self._runtime_model_lock = threading.Lock()
        self._runtime_model_state: dict[str, object] = {
            'active': False,
            'status': 'idle',
            'provider': None,
            'model': None,
            'full_model': None,
            'started_at': None,
            'updated_at': None,
            'latency_ms': None,
            'error': None,
            'last_provider': None,
            'last_model': None,
            'last_full_model': None,
            'last_status': None,
            'last_latency_ms': None,
            'last_error': None,
            'last_finished_at': None,
            'last_success_provider': None,
            'last_success_model': None,
            'last_success_full_model': None,
            'last_success_latency_ms': None,
            'last_success_finished_at': None,
        }
        self._key_indices: dict[str, int] = {}
        self._key_select_lock = threading.Lock()
        self._account_login_sessions: dict[str, dict[str, object]] = {}
        self._account_cache_lock = threading.Lock()
        self._account_cache: dict[str, list[dict[str, object]]] = {}
        self._route_cache_lock = threading.Lock()
        self._route_cache_ttl_seconds = 3600.0
        self._manual_order_cache: tuple[float, list[str]] | None = None
        self._disabled_models_cache: tuple[float, list[str]] | None = None
        self._usable_model_keys_cache: tuple[float, set[str]] | None = None
        self.sync_custom_providers()

    def _route_cache_get(self, name: str) -> object | None:
        with self._route_cache_lock:
            cached = getattr(self, name)
            if not cached:
                return None
            expires_at, value = cached
            if time.time() >= expires_at:
                setattr(self, name, None)
                return None
            return value

    def _route_cache_set(self, name: str, value: object) -> None:
        with self._route_cache_lock:
            setattr(self, name, (time.time() + self._route_cache_ttl_seconds, value))

    def _clear_route_cache(self) -> None:
        with self._route_cache_lock:
            self._manual_order_cache = None
            self._disabled_models_cache = None
            self._usable_model_keys_cache = None

    def sync_custom_providers(self) -> None:
        try:
            from .provider_catalog import clear_custom_providers, register_custom_provider
            clear_custom_providers()
            groups = {}
            for cm in self.get_custom_models():
                display_name = str(cm.get('display_name') or cm.get('id')).strip()
                if display_name not in groups:
                    groups[display_name] = []
                groups[display_name].append(cm)
            for display_name, cms in groups.items():
                primary = cms[0]
                hints = []
                for cm in cms:
                    values = cm.get('models') if isinstance(cm.get('models'), list) else [cm.get('model')]
                    for val in values:
                        val_str = str(val or '').strip()
                        if val_str and val_str not in hints:
                            hints.append(val_str)
                if not hints and primary.get('model_hint'):
                    hints.append(str(primary['model_hint']).strip())
                register_custom_provider(
                    name=str(primary['id']),
                    base_url=str(primary['base_url']),
                    api_key_env=f"CUSTOM_KEY_{primary['id']}",
                    format='openai',
                    model_hints=hints,
                )
        except Exception:
            pass

    def available_providers(self) -> list[str]:
        providers = configured_provider_names(get_all_keys(self.db_url))
        disabled = self.get_disabled_providers()
        providers = [p for p in providers if p not in disabled and not p.startswith('custom-')]
        if self._active_account_provider_accounts('qoder') and 'qoder' not in disabled and 'qoder' not in providers:
            providers.append('qoder')
        
        custom_models = self.get_custom_models()
        custom_groups = {}
        for cm in custom_models:
            display_name = str(cm.get('display_name') or cm['id']).strip()
            if display_name not in custom_groups:
                custom_groups[display_name] = []
            custom_groups[display_name].append(cm)
            
        primary_customs = [group[0] for group in custom_groups.values()]
        for cm in primary_customs:
            if cm.get('enabled', True) is not False and cm.get('verified') is True:
                providers.append(cm['id'])
        return providers

    def mark_runtime_model_start(self, provider_name: str, model_id: str) -> None:
        now = time.time()
        full_model = f'{provider_name}/{model_id}'
        with self._runtime_model_lock:
            self._runtime_model_state.update({
                'active': True,
                'status': 'calling',
                'provider': provider_name,
                'model': model_id,
                'full_model': full_model,
                'started_at': now,
                'updated_at': now,
                'latency_ms': None,
                'error': None,
            })

    def mark_runtime_model_finish(self, provider_name: str, model_id: str, ok: bool, latency_ms: int | None = None, error: str | None = None) -> None:
        now = time.time()
        full_model = f'{provider_name}/{model_id}'
        status = 'success' if ok else 'failed'
        with self._runtime_model_lock:
            state = self._runtime_model_state
            is_current = state.get('provider') == provider_name and state.get('model') == model_id
            state.update({
                'last_provider': provider_name,
                'last_model': model_id,
                'last_full_model': full_model,
                'last_status': status,
                'last_latency_ms': latency_ms,
                'last_error': error,
                'last_finished_at': now,
            })
            if ok:
                state.update({
                    'last_success_provider': provider_name,
                    'last_success_model': model_id,
                    'last_success_full_model': full_model,
                    'last_success_latency_ms': latency_ms,
                    'last_success_finished_at': now,
                })
            if is_current:
                state.update({
                    'active': False,
                    'status': status,
                    'latency_ms': latency_ms,
                    'error': error,
                    'updated_at': now,
                })

    def runtime_model_status(self) -> dict[str, object]:
        with self._runtime_model_lock:
            return dict(self._runtime_model_state)

    def public_models(self) -> list[dict[str, str]]:
        return [dict(item) for item in PUBLIC_MODEL_ALIASES]

    def get_provider_keys(self, provider_name: str) -> list[dict[str, str]]:
        db_keys = get_all_keys(self.db_url)
        if provider_name == 'qoder':
            return [
                {
                    'id': str(account.get('id')),
                    'api_key': str(account.get('access_token') or ''),
                    'label': str(account.get('label') or account.get('email') or account.get('name') or account.get('id')),
                    'account': account,
                }
                for account in self._active_account_provider_accounts('qoder')
                if account.get('access_token')
            ]
        if provider_name.startswith('custom-'):
            custom_models = self.get_custom_models()
            target_cm = next((cm for cm in custom_models if cm['id'] == provider_name), None)
            if target_cm:
                display_name = str(target_cm.get('display_name') or target_cm['id']).strip()
                same_display_cms = [
                    cm for cm in custom_models
                    if str(cm.get('display_name') or cm['id']).strip() == display_name
                ]
                keys = []
                for idx, cm in enumerate(same_display_cms):
                    api_key = db_keys.get(f"CUSTOM_KEY_{cm['id']}", cm.get('api_key', ''))
                    if api_key:
                        keys.append({
                            "id": cm['id'],
                            "api_key": api_key,
                            "label": cm.get('label') or f"Account {idx + 1}",
                            "base_url": cm.get('base_url')
                        })
                return keys
            return []

        multi_keys_raw = db_keys.get(f"multi_keys_{provider_name}", "")
        if multi_keys_raw:
            try:
                keys = json.loads(multi_keys_raw)
                if isinstance(keys, list):
                    for idx, item in enumerate(keys):
                        if not isinstance(item, dict):
                            continue
                        if 'id' not in item:
                            item['id'] = f"key_{idx}"
                        if 'label' not in item:
                            item['label'] = f"Key {idx + 1}"
                    return keys
            except Exception:
                pass
        
        provider = get_provider(provider_name)
        legacy_key = db_keys.get(provider.api_key_env, "")
        if legacy_key:
            return [{"id": "default", "api_key": legacy_key, "label": "Default"}]
            
        return []

    def get_next_api_key(self, provider_name: str) -> str:
        keys = self.get_provider_keys(provider_name)
        if not keys:
            return ''
        with self._key_select_lock:
            idx = self._key_indices.get(provider_name, 0)
            if idx >= len(keys):
                idx = 0
            selected_key = keys[idx]['api_key']
            self._key_indices[provider_name] = (idx + 1) % len(keys)
            return selected_key

    def provider_adapter(self, provider_name: str, key_id: str | None = None) -> ProviderAdapter:
        if provider_name == 'qoder':
            from .qoder_provider import QoderProviderAdapter
            account = self._select_account_provider_account('qoder', key_id=key_id)
            return QoderProviderAdapter(
                account=account,
                transport=self.transport,
                request_timeout_seconds=min(max(self.request_timeout_seconds, 6), 8),
            )  # type: ignore[return-value]

        provider = get_provider(provider_name)
        base_url = None
        if key_id is not None:
            keys = self.get_provider_keys(provider_name)
            target = next((k for k in keys if k['id'] == key_id), None)
            if not target:
                raise ProviderError(f'Key ID {key_id} not found')
            api_key = target['api_key']
            base_url = target.get('base_url')
        else:
            keys = self.get_provider_keys(provider_name)
            if keys:
                with self._key_select_lock:
                    idx = self._key_indices.get(provider_name, 0)
                    if idx >= len(keys):
                        idx = 0
                    target = keys[idx]
                    self._key_indices[provider_name] = (idx + 1) % len(keys)
                api_key = target['api_key']
                base_url = target.get('base_url')
            else:
                api_key = ''
            
        if not api_key and not provider_name.startswith('custom-'):
            raise ProviderError(f'{provider_name} 没有配置 API Key')
            
        if provider_name.startswith('custom-') and base_url:
            from dataclasses import replace
            provider = replace(provider, base_url=base_url)
            
        return ProviderAdapter(
            provider=provider,
            api_key=api_key,
            transport=self.transport,
            request_timeout_seconds=self.request_timeout_seconds,
            request_limiter=self.request_limiter,
            debug_log=self.debug_log,
        )

    def openai_relay(self) -> OpenAIRelay:
        return OpenAIRelay(
            adapter_factory=self.provider_adapter,
            health_loader=lambda: load_health(self.health_path),
            health_updater=lambda provider, model, ok, reason=None, headers=None: upsert_health(provider, model, ok, reason, headers=headers, path=self.health_path),
            preferred_model_loader=lambda: load_preferred_model(self.preferred_model_path),
            health_ttl_seconds=self.health_ttl_seconds,
            configured_providers_loader=self.available_providers,
            debug_log=self.debug_log,
            usage_incrementer=lambda provider, model: increment_model_usage(self.db_url, provider, model),
            manual_order_loader=self.get_manual_order,
            disabled_models_loader=self.get_disabled_models,
            allowed_models_loader=self.usable_model_keys,
            route_order_loader=self.route_model_order,
            request_logger=lambda platform, model_id, status, input_tokens, output_tokens, latency_ms, error=None: log_request(self.db_url, platform, model_id, status, input_tokens, output_tokens, latency_ms, error),
            runtime_model_start=self.mark_runtime_model_start,
            runtime_model_finish=self.mark_runtime_model_finish,
        )

    def account_provider_accounts(self, provider_name: str) -> list[dict[str, object]]:
        with self._account_cache_lock:
            cached = self._account_cache.get(provider_name)
            if cached is not None:
                return [dict(account) for account in cached]
        accounts = [dict(account) for account in load_accounts(self.db_url, provider_name)]
        with self._account_cache_lock:
            self._account_cache[provider_name] = [dict(account) for account in accounts]
        return accounts

    def _set_account_provider_cache(self, provider_name: str, accounts: list[dict[str, object]]) -> None:
        with self._account_cache_lock:
            self._account_cache[provider_name] = [dict(account) for account in accounts]

    def _clear_account_provider_cache(self, provider_name: str) -> None:
        with self._account_cache_lock:
            self._account_cache.pop(provider_name, None)

    def _active_account_provider_accounts(self, provider_name: str) -> list[dict[str, object]]:
        return [
            account for account in self.account_provider_accounts(provider_name)
            if account.get('status', 'active') == 'active' and str(account.get('access_token') or '').strip()
        ]

    def _loaded_account_provider_accounts(self, provider_name: str) -> list[dict[str, object]]:
        return [
            account for account in self._active_account_provider_accounts(provider_name)
            if account.get('models_loaded') is True
        ]

    def _select_account_provider_account(self, provider_name: str, key_id: str | None = None) -> dict[str, object]:
        accounts = self._active_account_provider_accounts(provider_name)
        if key_id is not None:
            target = next((account for account in accounts if str(account.get('id')) == key_id), None)
            if target is None:
                raise ProviderError(f'{provider_name} account {key_id} not found')
            return target
        if not accounts:
            raise ProviderError(f'{provider_name} 没有可用账号')
        with self._key_select_lock:
            idx_key = f'account:{provider_name}'
            idx = self._key_indices.get(idx_key, 0)
            if idx >= len(accounts):
                idx = 0
            account = accounts[idx]
            self._key_indices[idx_key] = (idx + 1) % len(accounts)
        now_ts = int(time.time())
        previous_last_used = int(account.get('last_used_at') or 0)
        account['last_used_at'] = now_ts
        all_accounts = self.account_provider_accounts(provider_name)
        for index, current in enumerate(all_accounts):
            if str(current.get('id')) == str(account.get('id')):
                all_accounts[index] = account
                self._set_account_provider_cache(provider_name, all_accounts)
                if now_ts - previous_last_used >= 60:
                    threading.Thread(
                        target=save_accounts,
                        args=(self.db_url, provider_name, all_accounts),
                        daemon=True,
                    ).start()
                break
        return account

    def account_provider_statuses(self) -> dict[str, dict[str, object]]:
        return {
            'qoder': {
                'provider': 'qoder',
                'name': 'Qoder',
                'configured': bool(self._active_account_provider_accounts('qoder')),
                'models_loaded': bool(self._loaded_account_provider_accounts('qoder')),
                'accounts': [public_account(account) for account in self.account_provider_accounts('qoder')],
                'supports_login': True,
                'supports_round_robin': True,
            }
        }

    def start_account_provider_login(self, provider_name: str) -> dict[str, object]:
        if provider_name != 'qoder':
            raise ProviderError(f'unsupported account provider: {provider_name}')
        from .qoder_provider import start_qoder_login
        flow = start_qoder_login()
        state = secrets.token_hex(12)
        self._account_login_sessions[state] = {
            'provider': provider_name,
            'code_verifier': flow['code_verifier'],
            'nonce': flow['nonce'],
            'machine_id': flow['machine_id'],
            'created_at': int(time.time()),
        }
        return {
            'ok': True,
            'provider': provider_name,
            'state': state,
            'verification_url': flow['verification_url'],
            'expires_in': 300,
        }

    def poll_account_provider_login(self, provider_name: str, state: str) -> dict[str, object]:
        session = self._account_login_sessions.get(state)
        if not session or session.get('provider') != provider_name:
            raise ProviderError('login session not found')
        if int(time.time()) - int(session.get('created_at') or 0) > 600:
            self._account_login_sessions.pop(state, None)
            raise ProviderError('login session expired')
        if provider_name != 'qoder':
            raise ProviderError(f'unsupported account provider: {provider_name}')
        from .qoder_provider import poll_qoder_login
        result = poll_qoder_login(
            nonce=str(session.get('nonce') or ''),
            code_verifier=str(session.get('code_verifier') or ''),
            machine_id=str(session.get('machine_id') or ''),
        )
        if result.get('status') != 'ok':
            return {'ok': True, 'provider': provider_name, 'status': 'pending'}
        account = upsert_account(self.db_url, provider_name, dict(result.get('account') or {}))
        self._clear_account_provider_cache(provider_name)
        self._account_login_sessions.pop(state, None)
        return {
            'ok': True,
            'provider': provider_name,
            'status': 'ok',
            'account': public_account(account),
        }

    def delete_account_provider_account(self, provider_name: str, account_id: str) -> dict[str, object]:
        deleted = delete_account(self.db_url, provider_name, account_id)
        if not deleted:
            accounts = self.account_provider_accounts(provider_name)
            if len(accounts) == 1:
                fallback_id = str(public_account(accounts[0]).get('id') or '')
                deleted = delete_account(self.db_url, provider_name, fallback_id)
                if deleted:
                    account_id = fallback_id
        if not deleted:
            raise ProviderError('account not found')
        self._clear_account_provider_cache(provider_name)
        if provider_name == 'qoder':
            self._clear_provider_model_state('qoder')
        self._clear_route_cache()
        return {'ok': True, 'provider': provider_name, 'id': account_id}

    def validate_account_provider(self, provider_name: str) -> dict[str, object]:
        if provider_name != 'qoder':
            raise ProviderError(f'unsupported account provider: {provider_name}')
        try:
            models = self.list_models(provider_name)
        except Exception as exc:
            return {'ok': False, 'provider': provider_name, 'error': str(exc), 'models': []}
        accounts = self.account_provider_accounts(provider_name)
        changed = False
        for account in accounts:
            if account.get('status', 'active') == 'active' and str(account.get('access_token') or '').strip():
                account['models_loaded'] = True
                account['models_loaded_at'] = int(time.time())
                changed = True
        if changed:
            save_accounts(self.db_url, provider_name, accounts)
            self._set_account_provider_cache(provider_name, accounts)
        return {'ok': True, 'provider': provider_name, 'models': models, 'account_count': len(self._active_account_provider_accounts(provider_name))}

    def probe_account_provider_models(self, provider_name: str) -> dict[str, object]:
        validation = self.validate_account_provider(provider_name)
        if not validation.get('ok'):
            return validation
        models = [str(model) for model in validation.get('models', [])]
        results = []
        ok_count = 0
        for model in models[:12]:
            started = time.time()
            result = self.probe(provider_name, model, timeout=45)
            latency_ms = int((time.time() - started) * 1000)
            model_key = f'{provider_name}/{model}'
            self.record_model_probe_result(
                model_key,
                ok=result.ok,
                latency_ms=latency_ms,
                status=result.status,
                error=result.error or '',
            )
            if result.ok:
                ok_count += 1
            results.append({
                'model': model,
                'ok': result.ok,
                'latency_ms': latency_ms,
                'error': result.error or '',
            })
        return {'ok': ok_count > 0, 'provider': provider_name, 'models': models, 'results': results, 'success_count': ok_count}

    def get_usage_stats(self) -> list[dict[str, object]]:
        return get_model_usage_stats(self.db_url)

    def usable_model_keys(self) -> set[str]:
        cached = self._route_cache_get('_usable_model_keys_cache')
        if isinstance(cached, set):
            return set(cached)
        from .scoring import is_chat_candidate_model
        probe_results = get_model_probe_results(self.db_url)
        disabled_models = set(self.get_disabled_models())
        active_providers = set(self.available_providers())
        usable: set[str] = set()
        for key, probe in probe_results.items():
            if not isinstance(probe, dict) or probe.get('ok') is not True:
                continue
            if key in disabled_models or '/' not in key:
                continue
            provider, _ = key.split('/', 1)
            if provider not in active_providers:
                continue
            if not is_chat_candidate_model(provider, key.split('/', 1)[1]):
                continue
            usable.add(key)
        self._route_cache_set('_usable_model_keys_cache', set(usable))
        return usable

    def get_manual_order(self, bypass_cache: bool = False) -> list[str]:
        if not bypass_cache:
            cached = self._route_cache_get('_manual_order_cache')
            if isinstance(cached, list):
                return list(cached)
        order = get_manual_order(self.db_url, bypass_cache)
        if not bypass_cache:
            self._route_cache_set('_manual_order_cache', list(order))
        return order

    def save_manual_order(self, order: list[str]) -> None:
        save_manual_order(self.db_url, order)
        self._clear_route_cache()

    def preferred_model(self) -> str | None:
        return load_preferred_model(self.preferred_model_path)

    def save_preferred_model(self, provider_name: str, model_id: str) -> dict[str, object]:
        provider = get_provider(provider_name)
        provider_name = provider.name
        model_id = model_id.strip()
        if not model_id:
            raise ProviderError('model 不能为空')
        save_preferred_model(provider_name, model_id, path=self.preferred_model_path)
        return {'ok': True, 'provider': provider_name, 'model': model_id, 'requested_model': f'{provider_name}/{model_id}'}

    @staticmethod
    def _mask_key(value: str) -> str:
        if len(value) <= 8:
            return '***'
        return f'{value[:4]}***{value[-4:]}'

    def provider_key_statuses(self) -> dict[str, dict[str, object]]:
        disabled = self.get_disabled_providers()
        statuses: dict[str, dict[str, object]] = {}
        for provider in list_providers():
            keys = self.get_provider_keys(provider.name)
            statuses[provider.name] = {
                'configured': len(keys) > 0,
                'masked': self._mask_key(keys[0]['api_key']) if keys else '',
                'enabled': len(keys) > 0 and (provider.name not in disabled),
                'keys': [
                    {
                        'id': k['id'],
                        'masked': self._mask_key(k['api_key']),
                        'label': k.get('label') or 'Default',
                        'verified': k.get('verified') is True,
                        'verified_model': k.get('verified_model') or '',
                        'verified_at': k.get('verified_at'),
                    } for k in keys
                ],
                'models': [m for m in get_provider_model_hints(provider.name) if m.startswith('free-proxy/')],
            }
            if provider.name == 'qoder':
                active_accounts = self._active_account_provider_accounts('qoder')
                loaded_accounts = self._loaded_account_provider_accounts('qoder')
                statuses[provider.name]['account_provider'] = True
                statuses[provider.name]['name'] = 'Qoder'
                statuses[provider.name]['display_name'] = 'Qoder'
                statuses[provider.name]['configured'] = bool(active_accounts)
                statuses[provider.name]['enabled'] = bool(active_accounts)
                statuses[provider.name]['models_loaded'] = bool(loaded_accounts)
                statuses[provider.name]['accounts'] = [public_account(account) for account in self.account_provider_accounts('qoder')]
        return statuses

    def configure_provider_key(self, provider_name: str, api_key: str, label: str = '', key_id: str | None = None) -> dict[str, object]:
        provider = get_provider(provider_name)
        value = api_key.strip()
        if not value:
            raise ProviderError('api_key 不能为空')
            
        keys = self.get_provider_keys(provider_name)
        
        if key_id is not None:
            target = next((k for k in keys if k['id'] == key_id), None)
            if target:
                target['api_key'] = value
                if label.strip():
                    target['label'] = label.strip()
                target.pop('verified', None)
                target.pop('verified_model', None)
                target.pop('verified_at', None)
            else:
                raise ProviderError(f'Key ID {key_id} not found')
        else:
            new_id = secrets.token_hex(4)
            lbl = label.strip() or f"Key {len(keys) + 1}"
            keys.append({
                'id': new_id,
                'api_key': value,
                'label': lbl,
                'verified': False,
            })
            
        upsert_key(self.db_url, f"multi_keys_{provider_name}", json.dumps(keys))
        if keys:
            upsert_key(self.db_url, provider.api_key_env, keys[0]['api_key'])
            
        if hasattr(self, '_models_cache'):
            self._models_cache.pop(provider_name, None)
            
        return {'ok': True, 'provider': provider_name, 'masked': self._mask_key(value)}

    def mark_provider_key_verified(self, provider_name: str, key_id: str | None, verified_model: str) -> None:
        provider = get_provider(provider_name)
        keys = self.get_provider_keys(provider.name)
        if not keys:
            return
        target_id = key_id or keys[0].get('id') or 'default'
        changed = False
        for item in keys:
            if str(item.get('id') or '') == str(target_id):
                item['verified'] = True
                item['verified_model'] = verified_model
                item['verified_at'] = int(time.time())
                changed = True
                break
        if not changed:
            return
        upsert_key(self.db_url, f"multi_keys_{provider.name}", json.dumps(keys))
        upsert_key(self.db_url, provider.api_key_env, keys[0]['api_key'])

    def _clear_provider_model_state(self, provider_name: str) -> None:
        delete_model_probe_results_for_provider(self.db_url, provider_name)
        delete_request_logs_for_provider(self.db_url, provider_name)
        delete_health_for_provider(provider_name, self.health_path)
        disabled_models = [key for key in self._persistent_disabled_models() if not key.startswith(f'{provider_name}/')]
        upsert_key(self.db_url, 'disabled_models', json.dumps(disabled_models))
        manual_order = [key for key in self.get_manual_order() if not key.startswith(f'{provider_name}/')]
        save_manual_order(self.db_url, manual_order)
        if hasattr(self, '_models_cache'):
            self._models_cache.pop(provider_name, None)

    def reset_provider_model_state(self, provider_name: str) -> dict[str, object]:
        provider = get_provider(provider_name)
        self._clear_provider_model_state(provider.name)
        self._clear_route_cache()
        return {'ok': True, 'provider': provider.name}

    def delete_provider_key(self, provider_name: str, key_id: str | None = None) -> dict[str, object]:
        provider = get_provider(provider_name)
        if key_id is not None:
            keys = self.get_provider_keys(provider_name)
            filtered = [k for k in keys if k['id'] != key_id]
            if len(filtered) < len(keys):
                if filtered:
                    upsert_key(self.db_url, f"multi_keys_{provider_name}", json.dumps(filtered))
                    upsert_key(self.db_url, provider.api_key_env, filtered[0]['api_key'])
                else:
                    from .db_store import delete_key
                    delete_key(self.db_url, f"multi_keys_{provider_name}")
                    delete_key(self.db_url, provider.api_key_env)
                    self._clear_provider_model_state(provider.name)
            return {'ok': True, 'provider': provider_name}
        else:
            from .db_store import delete_key
            delete_key(self.db_url, f"multi_keys_{provider_name}")
            delete_key(self.db_url, provider.api_key_env)
            self._clear_provider_model_state(provider.name)
            return {'ok': True, 'provider': provider_name}

    def get_disabled_providers(self) -> list[str]:
        data_str = get_key(self.db_url, 'disabled_providers')
        if not data_str:
            return []
        try:
            import json
            return json.loads(data_str)
        except Exception:
            return []

    def toggle_provider(self, provider_name: str, enabled: bool) -> dict[str, object]:
        if provider_name.startswith('custom-'):
            models = self.get_custom_models()
            for m in models:
                if m['id'] == provider_name:
                    m['enabled'] = enabled
                    break
            upsert_key(self.db_url, 'custom_openai_models', json.dumps(models))
            self.sync_custom_providers()
            self._clear_route_cache()
            return {'ok': True}
        else:
            disabled = self.get_disabled_providers()
            if enabled:
                if provider_name in disabled:
                    disabled.remove(provider_name)
            else:
                if provider_name not in disabled:
                    disabled.append(provider_name)
            upsert_key(self.db_url, 'disabled_providers', json.dumps(disabled))
            self._clear_route_cache()
            return {'ok': True}

    def _persistent_disabled_models(self) -> list[str]:
        data_str = get_key(self.db_url, 'disabled_models')
        if not data_str:
            return []
        try:
            disabled = json.loads(data_str)
        except Exception:
            return []
        if not isinstance(disabled, list):
            return []
        return [str(item) for item in disabled]

    def get_disabled_models(self) -> list[str]:
        cached = self._route_cache_get('_disabled_models_cache')
        if isinstance(cached, list):
            return list(cached)
        temp_disabled = temporary_disabled_models(self.health_path)
        merged = self._persistent_disabled_models()
        for key in temp_disabled:
            if key not in merged:
                merged.append(key)
        self._route_cache_set('_disabled_models_cache', list(merged))
        return merged

    def toggle_model(self, model_key: str, enabled: bool) -> dict[str, object]:
        import json
        disabled = self._persistent_disabled_models()
        if enabled:
            if model_key in disabled:
                disabled.remove(model_key)
        else:
            if model_key not in disabled:
                disabled.append(model_key)
        upsert_key(self.db_url, 'disabled_models', json.dumps(disabled))
        self._clear_route_cache()
        return {'ok': True}

    def record_model_probe_result(
        self,
        model_key: str,
        *,
        ok: bool,
        latency_ms: int,
        status: int | None = None,
        error: str = '',
    ) -> None:
        save_model_probe_result(
            self.db_url,
            model_key,
            ok=ok,
            latency_ms=latency_ms,
            status=status,
            error=error,
        )
        if '/' not in model_key:
            return
        provider_name, model_id = model_key.split('/', 1)
        from .scoring import is_chat_candidate_model
        with self._route_cache_lock:
            cached = self._usable_model_keys_cache
            if cached is None:
                return
            _, cached_keys = cached
            usable = set(cached_keys)
            if ok and is_chat_candidate_model(provider_name, model_id):
                usable.add(model_key)
            else:
                usable.discard(model_key)
            self._usable_model_keys_cache = (time.time() + self._route_cache_ttl_seconds, usable)

    @staticmethod
    def daily_reset_timestamp() -> int:
        now_local = time.localtime()
        reset_time = time.mktime((
            now_local.tm_year,
            now_local.tm_mon,
            now_local.tm_mday,
            6,
            0,
            0,
            now_local.tm_wday,
            now_local.tm_yday,
            now_local.tm_isdst,
        ))
        if time.time() < reset_time:
            reset_time -= 24 * 60 * 60
        return int(reset_time)

    def automatic_retest_due_models(self) -> dict[str, object]:
        reset_ts = self.daily_reset_timestamp()
        stats = self.models_stats().get('models', [])
        due_items = [
            item
            for item in stats
            if isinstance(item, dict)
            and (
                item.get('probe_status') == 'recoverable'
                or item.get('analysis_status') == 'retest_required'
            )
        ]

        results: list[dict[str, object]] = []
        for item in due_items:
            provider_name = str(item.get('provider') or '')
            model_id = str(item.get('model') or '')
            if not provider_name or not model_id:
                continue
            model_key = f'{provider_name}/{model_id}'
            checked_at = item.get('probe_checked_at')
            if isinstance(checked_at, int) and checked_at >= reset_ts:
                continue

            started = time.time()
            result = self.probe(provider_name, model_id, timeout=45 if provider_name == 'qoder' else 6)
            latency_ms = int((time.time() - started) * 1000)
            status = 200 if result.ok else (result.status if result.status is not None else 500)
            self.record_model_probe_result(
                model_key,
                ok=result.ok,
                latency_ms=latency_ms,
                status=status,
                error=result.error or '',
            )
            results.append({
                'model_key': model_key,
                'ok': result.ok,
                'status': status,
                'category': result.category,
                'latency_ms': latency_ms,
            })

        return {'checked': len(results), 'results': results, 'reset_ts': reset_ts}

    def get_custom_models(self) -> list[dict[str, object]]:
        data_str = get_key(self.db_url, 'custom_openai_models')
        if not data_str:
            return []
        try:
            raw = json.loads(data_str)
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        grouped: list[dict[str, object]] = []
        by_key: dict[tuple[str, str], dict[str, object]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            base_url = str(item.get('base_url', '')).strip()
            if not base_url:
                continue
            api_key = str(item.get('api_key', '') or '')
            key = (base_url.rstrip('/'), api_key)
            group = by_key.get(key)
            if group is None:
                group = dict(item)
                group['id'] = str(item.get('id') or f"custom-{len(grouped) + 1}")
                group['base_url'] = base_url
                group['api_key'] = api_key
                group['models'] = []
                grouped.append(group)
                by_key[key] = group
            model_values = item.get('models') if isinstance(item.get('models'), list) else [item.get('model')]
            for model_value in model_values:
                model_id = str(model_value or '').strip()
                if model_id and model_id not in group['models']:
                    group['models'].append(model_id)
        for group in grouped:
            models = group.get('models') if isinstance(group.get('models'), list) else []
            group['model'] = models[0] if models else group.get('model_hint', '')
            group['display_name'] = str(group.get('display_name') or group.get('model') or group.get('id'))
            if 'verified' not in group:
                group['verified'] = bool(models)
            group['verify_error'] = str(group.get('verify_error') or '')
            group['model_hint'] = str(group.get('model_hint') or '')
        return grouped

    @staticmethod
    def _custom_models_url(base_url: str) -> str:
        clean = base_url.strip().rstrip('/')
        if clean.endswith('/chat/completions'):
            clean = clean[: -len('/chat/completions')]
        return f'{clean}/models'

    def discover_custom_models(self, base_url: str, api_key: str = '') -> list[str]:
        import urllib.request
        import urllib.error
        url = self._custom_models_url(base_url)
        headers = {'Accept': 'application/json'}
        if api_key.strip():
            headers['Authorization'] = f'Bearer {api_key.strip()}'
        req = urllib.request.Request(url, headers=headers, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode('utf-8', errors='replace')
        except Exception as exc:
            raise ProviderError(f'无法读取模型列表: {exc}') from exc
        try:
            data = json.loads(raw)
        except Exception as exc:
            raise ProviderError('模型列表返回的不是 JSON') from exc
        items = data.get('data') if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ProviderError('模型列表格式不正确')
        models: list[str] = []
        for item in items:
            raw_id = item.get('id') if isinstance(item, dict) else item
            if isinstance(raw_id, str) and raw_id.strip():
                model_id = raw_id.strip()
                if model_id not in models:
                    models.append(model_id)
        if not models:
            raise ProviderError('没有读取到任何模型')
        return models

    def add_custom_model(self, base_url: str, model: str = '', display_name: str = '', api_key: str = '') -> dict[str, object]:
        models = self.get_custom_models()
        model_hint = model.strip()
        discovered_models = []
        base_key = base_url.rstrip('/')
        target = None
        for item in models:
            if str(item.get('base_url', '')).rstrip('/') == base_key and str(item.get('api_key', '') or '') == api_key:
                target = item
                break
        custom_ids = {str(m['id']) for m in models if m.get('id')}
        new_id = None
        i = 1
        while True:
            candidate = f"custom-{i}"
            if candidate not in custom_ids:
                new_id = candidate
                break
            i += 1
        if target is None:
            target = {
                'id': new_id,
                'base_url': base_url,
                'models': [],
                'model_hint': model_hint,
                'verified': False,
                'verify_error': '',
                'display_name': display_name or base_url,
                'api_key': api_key,
                'enabled': True,
                'created_at': int(time.time()),
            }
            models.append(target)
        current_models = target.get('models') if isinstance(target.get('models'), list) else []
        if not current_models and model_hint:
            current_models.append(model_hint)
        added_models = []
        for discovered in discovered_models:
            if discovered not in current_models:
                current_models.append(discovered)
                added_models.append(discovered)
        target['models'] = current_models
        target['model'] = current_models[0] if current_models else model_hint
        target['model_hint'] = model_hint or str(target.get('model_hint') or '')
        target['verified'] = bool(current_models)
        target['verify_error'] = ''
        if display_name:
            target['display_name'] = display_name
        if False and not added_models:
            return {'ok': True, 'models': [], 'added': 0, 'message': '没有新增模型'}
        upsert_key(self.db_url, 'custom_openai_models', json.dumps(models))
        if api_key:
            upsert_key(self.db_url, f"CUSTOM_KEY_{target['id']}", api_key)
        self.sync_custom_providers()
        return {'ok': True, 'model': target, 'models': added_models, 'added': len(added_models), 'verified': target.get('verified') is True}

    def verify_custom_model(self, model_id: str) -> dict[str, object]:
        models = self.get_custom_models()
        target = None
        for item in models:
            if item.get('id') == model_id:
                target = item
                break
        if target is None:
            raise ProviderError('custom provider not found')
        try:
            discovered_models = self.discover_custom_models(str(target.get('base_url') or ''), str(target.get('api_key') or ''))
            hint = str(target.get('model_hint') or '').strip()
            if hint and hint not in discovered_models:
                discovered_models.insert(0, hint)
            target['models'] = discovered_models
            target['model'] = discovered_models[0] if discovered_models else ''
            target['verified'] = True
            target['verify_error'] = ''
            target['verified_at'] = int(time.time())
        except Exception as exc:
            target['verified'] = False
            target['verify_error'] = str(exc)
            target['verified_at'] = int(time.time())
            upsert_key(self.db_url, 'custom_openai_models', json.dumps(models))
            self.sync_custom_providers()
            raise
        upsert_key(self.db_url, 'custom_openai_models', json.dumps(models))
        self.sync_custom_providers()
        return {'ok': True, 'model': target, 'models': discovered_models, 'added': len(discovered_models), 'verified': True}

    def delete_custom_model(self, model_id: str) -> dict[str, object]:
        models = self.get_custom_models()
        filtered = [m for m in models if m['id'] != model_id]
        upsert_key(self.db_url, 'custom_openai_models', json.dumps(filtered))
        from .db_store import delete_key
        delete_key(self.db_url, f"CUSTOM_KEY_{model_id}")
        self._clear_provider_model_state(model_id)
        self.sync_custom_providers()
        return {'ok': True, 'id': model_id}

    def update_custom_model_key(self, model_id: str, api_key: str) -> dict[str, object]:
        models = self.get_custom_models()
        for m in models:
            if m['id'] == model_id:
                m['api_key'] = api_key
                break
        upsert_key(self.db_url, 'custom_openai_models', json.dumps(models))
        if api_key:
            upsert_key(self.db_url, f"CUSTOM_KEY_{model_id}", api_key)
        else:
            from .db_store import delete_key
            delete_key(self.db_url, f"CUSTOM_KEY_{model_id}")
        self.sync_custom_providers()
        return {'ok': True}

    def get_proxy_key(self) -> str | None:
        return get_key(self.db_url, 'PROXY_API_KEY')

    def generate_proxy_key(self) -> str:
        new_key = f'sk-fp-{secrets.token_hex(16)}'
        upsert_key(self.db_url, 'PROXY_API_KEY', new_key)
        return new_key

    def keep_database_alive(self) -> dict[str, object]:
        now_ts = int(time.time())
        upsert_key(self.db_url, 'db_keepalive_last_seen_at', str(now_ts))
        return {'ok': True, 'last_seen_at': now_ts}

    def _ensure_admin_password(self) -> None:
        admin_pwd = get_key(self.db_url, 'ADMIN_PASSWORD')
        if not admin_pwd:
            admin_pwd = f'admin-{secrets.token_hex(6)}'
            upsert_key(self.db_url, 'ADMIN_PASSWORD', admin_pwd)
            print('\n' + '=' * 60)
            print('Web 控制台管理员密码已生成！')
            print(f'你的登录密码是: {admin_pwd}')
            print('请务必妥善保管，你可以随时在数据库中重置它。')
            print('=' * 60 + '\n')
            
    def get_admin_password(self) -> str:
        return get_key(self.db_url, 'ADMIN_PASSWORD') or ''

    def verify_provider_key(self, provider_name: str, key_id: str | None = None) -> dict[str, object]:
        self._clear_provider_model_state(provider_name)

        def diagnose(exc: ProviderError) -> tuple[str, int | None, str]:
            if isinstance(exc, ProviderHTTPError):
                category = exc.category
                status = exc.status
            else:
                category = classify_error(0, str(exc)).category
                status = None
            suggestion = remediation_suggestion(category, provider_name)
            return category, status, suggestion

        try:
            models = self.list_models(provider_name, key_id=key_id)
        except ProviderError as exc:
            models = []
            first_error: ProviderError | None = exc
        else:
            first_error = None

        candidates: list[str] = []
        # 优先使用静态推荐模型，确保快速探测成功，避免动态列表中混入不可用或慢速模型（如语音模型）
        for model in get_provider_model_hints(provider_name) + models:
            if model and model not in candidates:
                candidates.append(model)

        def key_valid_response(category: str = 'not_callable', status: int | None = None, error: str = '') -> dict[str, object]:
            verified_model = candidates[0] if candidates else ''
            if verified_model:
                self.mark_provider_key_verified(provider_name, key_id, verified_model)
            return {
                'ok': True,
                'callable': False,
                'provider': provider_name,
                'error': error or 'Key valid; chat call is not currently available',
                'models': candidates,
                'category': category,
                'status': status,
                'verified_model': verified_model,
                'suggestion': remediation_suggestion(category, provider_name),
            }

        for candidate in candidates[:3]:
            result = self.probe(provider_name, candidate, key_id=key_id)
            if result.ok:
                verified_model = result.actual_model or candidate
                self.mark_provider_key_verified(provider_name, key_id, verified_model)
                return {
                    'ok': True,
                    'provider': provider_name,
                    'models': candidates,
                    'category': None,
                    'verified_model': verified_model,
                    'note': '已通过真实请求验证该 key 可调用模型',
                }

        if first_error is not None:
            category, status, suggestion = diagnose(first_error)
            return {
                'ok': False,
                'provider': provider_name,
                'error': str(first_error),
                'models': candidates,
                'category': category,
                'status': status,
                'suggestion': suggestion,
            }

        if candidates:
            failed = self.probe(provider_name, candidates[0], key_id=key_id)
            category = failed.category or classify_error(0, failed.error or '').category
            if first_error is None and models:
                return key_valid_response(category, failed.status, failed.error or '')
            return {
                'ok': False,
                'provider': provider_name,
                'error': failed.error or '模型可列出但不可调用',
                'models': candidates,
                'category': category,
                'status': failed.status,
                'suggestion': remediation_suggestion(category, provider_name),
            }

        category = 'unknown'
        return {
            'ok': False,
            'provider': provider_name,
            'error': '没有可用于验证的候选模型',
            'models': [],
            'category': category,
            'status': None,
            'suggestion': remediation_suggestion(category, provider_name),
        }

    def recommended_models(self, provider_name: str, requested_model: str | None = None) -> list[str]:
        try:
            listed = self.list_models(provider_name)
        except ProviderError:
            listed = []

        hints = listed + get_provider_model_hints(provider_name)
        health = load_health(self.health_path)
        return choose_candidates(
            provider=provider_name,
            requested_model=requested_model,
            health=health,
            hints=hints,
            now_ts=int(time.time()),
            ttl_seconds=self.health_ttl_seconds,
        )

    def list_models(self, provider_name: str, key_id: str | None = None) -> list[str]:
        return self.provider_adapter(provider_name, key_id=key_id).list_models()

    def probe(self, provider_name: str, model_id: str, timeout: int | None = None, key_id: str | None = None) -> ProbeResult:
        return self.chat(provider_name, model_id, prompt='ok', max_output_tokens=1, timeout=timeout, record_runtime=False, key_id=key_id)

    def chat(self, provider_name: str, model_id: str, prompt: str, max_output_tokens: int | None = None, timeout: int | None = None, record_runtime: bool = True, key_id: str | None = None) -> ProbeResult:
        adapter = self.provider_adapter(provider_name, key_id=key_id)
        trimmed = trim_prompt(provider_name, prompt)
        candidates = [model_id]

        output_tokens = max_output_tokens if max_output_tokens is not None else response_token_budget(provider_name)
        learned_limits = load_token_limits(self.token_limit_path)
        last_error: str | None = None
        last_category: str | None = None
        last_status: int | None = None

        for candidate in candidates:
            attempt_start = time.time()
            if record_runtime:
                self.mark_runtime_model_start(provider_name, candidate)
            budget = resolve_token_budget(
                provider=provider_name,
                model=candidate,
                prompt=trimmed,
                requested_output_tokens=output_tokens,
                learned_limits=learned_limits,
                model_metadata=None,
            )
            try:
                content = adapter.chat_text(candidate, budget.trimmed_prompt, max_tokens=budget.output_tokens_limit, timeout=timeout)
                if _looks_like_embedded_provider_error(content):
                    failure = classify_error(0, content)
                    last_error = content
                    last_category = failure.category
                    last_status = None
                    upsert_health(provider_name, candidate, False, reason=last_category, path=self.health_path)
                    if record_runtime:
                        self.mark_runtime_model_finish(provider_name, candidate, False, int((time.time() - attempt_start) * 1000), content)
                    continue
                upsert_health(provider_name, candidate, True, path=self.health_path)
                if record_runtime:
                    self.mark_runtime_model_finish(provider_name, candidate, True, int((time.time() - attempt_start) * 1000), None)
                return ProbeResult(provider=provider_name, model=model_id, ok=True, actual_model=candidate, content=content)
            except ProviderError as exc:
                last_error = str(exc)
                if isinstance(exc, ProviderHTTPError):
                    last_category = exc.category
                    last_status = exc.status
                else:
                    last_category = classify_error(0, last_error).category
                    last_status = None
                if last_category == 'token_limit':
                    learned = shrink_budget_after_limit_error(
                        provider=provider_name,
                        model=candidate,
                        prompt=budget.trimmed_prompt,
                        attempted_output_tokens=budget.output_tokens_limit,
                        error_message=last_error,
                    )
                    upsert_token_limit(
                        provider_name,
                        candidate,
                        input_tokens_limit=learned.input_tokens_limit,
                        output_tokens_limit=learned.output_tokens_limit,
                        source=learned.source,
                        path=self.token_limit_path,
                    )
                    refreshed_limits = load_token_limits(self.token_limit_path)
                    retry_budget = resolve_token_budget(
                        provider=provider_name,
                        model=candidate,
                        prompt=trimmed,
                        requested_output_tokens=output_tokens,
                        learned_limits=refreshed_limits,
                        model_metadata=None,
                    )
                    try:
                        retry_content = adapter.chat_text(candidate, retry_budget.trimmed_prompt, max_tokens=retry_budget.output_tokens_limit, timeout=timeout)
                        if _looks_like_embedded_provider_error(retry_content):
                            failure = classify_error(0, retry_content)
                            last_error = retry_content
                            last_category = failure.category
                            last_status = None
                            upsert_health(provider_name, candidate, False, reason=last_category, path=self.health_path)
                            if record_runtime:
                                self.mark_runtime_model_finish(provider_name, candidate, False, int((time.time() - attempt_start) * 1000), retry_content)
                            continue
                        upsert_health(provider_name, candidate, True, path=self.health_path)
                        if record_runtime:
                            self.mark_runtime_model_finish(provider_name, candidate, True, int((time.time() - attempt_start) * 1000), None)
                        return ProbeResult(provider=provider_name, model=model_id, ok=True, actual_model=candidate, content=retry_content)
                    except ProviderError as retry_exc:
                        last_error = str(retry_exc)
                        if isinstance(retry_exc, ProviderHTTPError):
                            last_category = retry_exc.category
                            last_status = retry_exc.status
                        else:
                            last_category = classify_error(0, last_error).category
                            last_status = None
                upsert_health(provider_name, candidate, False, reason=last_category, path=self.health_path)
                if record_runtime:
                    self.mark_runtime_model_finish(provider_name, candidate, False, int((time.time() - attempt_start) * 1000), last_error)

        final_category = last_category or classify_error(0, last_error or '').category
        return ProbeResult(
            provider=provider_name,
            model=model_id,
            ok=False,
            error=last_error or '探测失败',
            category=final_category,
            status=last_status,
            suggestion=remediation_suggestion(final_category, provider_name),
        )

    def summary(self) -> dict[str, object]:
        providers: list[dict[str, object]] = []
        for provider_name in self.available_providers():
            try:
                models = self.list_models(provider_name)
                providers.append({'provider': provider_name, 'models': models})
            except ProviderError as exc:
                providers.append({'provider': provider_name, 'error': str(exc), 'models': []})
        return {'providers': providers}
        
    def get_cached_provider_models(self, provider_name: str, refresh: bool = False) -> list[str]:
        now = time.time()
        if not hasattr(self, '_models_cache'):
            self._models_cache = {}
        if provider_name in self._models_cache and not refresh:
            models, expiry = self._models_cache[provider_name]
            if now < expiry:
                return models
        hints = get_provider_model_hints(provider_name)
        if not refresh:
            self._models_cache[provider_name] = (hints, now + 60)
            return hints
        try:
            adapter = self.provider_adapter(provider_name)
            models = adapter.list_models()
        except Exception:
            models = hints
        self._models_cache[provider_name] = (models, now + 300)
        return models

    def _model_analysis_stats(self, days: int = 7) -> dict[str, dict[str, object]]:
        from .db_store import get_adapter, SqliteAdapter
        import datetime

        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        since_value = since.strftime('%Y-%m-%d %H:%M:%S')
        adapter = get_adapter(self.db_url)
        try:
            rows = adapter.fetchall(
                """
                SELECT
                  platform,
                  model_id,
                  COUNT(*) as request_count,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                  AVG(latency_ms) as avg_latency_ms
                FROM requests
                WHERE created_at >= %s
                GROUP BY platform, model_id
                """,
                (since_value,),
            )
            recent_rows = adapter.fetchall(
                """
                SELECT platform, model_id, status, error
                FROM requests
                WHERE created_at >= %s
                ORDER BY created_at DESC
                """,
                (since_value,),
            )
        except Exception:
            return {}
        finally:
            adapter.close()

        latest_events: dict[str, tuple[str, str]] = {}
        for provider, model, status, error in recent_rows:
            key = f'{provider}/{model}'
            if key not in latest_events:
                latest_events[key] = (str(status or ''), str(error or ''))

        def classify_hide_reason(request_count: int, success_rate: float | None, recent_error: str) -> str:
            error_lower = recent_error.lower()
            failure = classify_error(0, recent_error)
            if failure.category == 'model_not_found':
                return '模型不存在或不可用'
            if failure.category == 'auth':
                return 'API Key 无效或权限不足'
            if failure.category == 'server':
                return '上游服务异常'
            if any(term in recent_error for term in ('额度不足', '余额不足')) or any(term in error_lower for term in ('quota', 'rate limit', 'insufficient_quota')):
                return '额度不足或限流'
            if any(term in recent_error for term in ('API Key 无效', '权限不足', '无效')) or any(term in error_lower for term in ('invalid key', 'unauthorized', 'forbidden', 'api key')):
                return 'API Key 无效或权限不足'
            if success_rate is not None and request_count >= 3 and success_rate < 50:
                return f'成功率过低 {success_rate:.1f}%'
            if success_rate == 0 and request_count >= 2:
                return '最近调用全部失败'
            return ''

        analysis: dict[str, dict[str, object]] = {}
        for provider, model, request_count, success_count, avg_latency in rows:
            key = f'{provider}/{model}'
            count = int(request_count or 0)
            successes = int(success_count or 0)
            success_rate = (successes / count * 100) if count else None
            latest_status, latest_error = latest_events.get(key, ('', ''))
            recent_error = '' if latest_status == 'success' else latest_error
            hide_reason = classify_hide_reason(count, success_rate, recent_error)
            analysis[key] = {
                'usage_count': count,
                'success_count': successes,
                'success_rate': round(success_rate, 1) if success_rate is not None else None,
                'avg_latency_ms': round(float(avg_latency or 0)),
                'recent_error': recent_error,
                'analysis_status': 'hidden' if hide_reason else 'ok',
                'hide_reason': hide_reason,
            }
        return analysis

    def models_stats(self) -> dict[str, object]:
        from .provider_routing import CandidateTarget, build_auto_candidates
        from .qoder_provider import qoder_model_display_name, qoder_model_key_display
        from .scoring import expected_reliability, synthetic_speed_score, synthetic_intelligence_score, headroom_factor, combine_score, BANDIT_PRESETS, get_model_limits, route_priority_sort_key, is_chat_candidate_model
        health = load_health(self.health_path)
        import concurrent.futures

        def future_result(future: concurrent.futures.Future, default: object, label: str) -> object:
            try:
                return future.result()
            except Exception:
                logger.exception('models_stats source failed: %s', label)
                return default

        def as_str_list(value: object) -> list[str]:
            if not isinstance(value, list):
                return []
            result: list[str] = []
            for item in value:
                text = str(item or '').strip()
                if text:
                    result.append(text)
            return result

        def as_dict(value: object) -> dict[str, object]:
            if not isinstance(value, dict):
                return {}
            return {str(key): item for key, item in value.items()}

        def normalize_custom_models(value: object) -> list[dict[str, object]]:
            models: list[dict[str, object]] = []
            if not isinstance(value, list):
                return models
            for item in value:
                if not isinstance(item, dict):
                    continue
                model = dict(item)
                model_id = str(model.get('id') or '').strip()
                if not model_id:
                    continue
                model['id'] = model_id
                models.append(model)
            return models

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            f_manual = executor.submit(self.get_manual_order)
            f_probe = executor.submit(get_model_probe_results, self.db_url)
            f_analysis = executor.submit(self._model_analysis_stats)
            f_keys = executor.submit(get_all_keys, self.db_url)
            f_custom = executor.submit(self.get_custom_models)
            
            manual_order = as_str_list(future_result(f_manual, [], 'manual_order'))
            probe_results = as_dict(future_result(f_probe, {}, 'probe_results'))
            analysis_stats = as_dict(future_result(f_analysis, {}, 'analysis_stats'))
            db_keys = {
                str(key): str(value)
                for key, value in as_dict(future_result(f_keys, {}, 'config_keys')).items()
            }
            custom_models = normalize_custom_models(future_result(f_custom, [], 'custom_models'))
            
        daily_reset_ts = self.daily_reset_timestamp()
        
        configured_names = configured_provider_names(db_keys)
        for provider_name in ('qoder',):
            if self._loaded_account_provider_accounts(provider_name) and provider_name not in configured_names:
                configured_names.append(provider_name)
        all_configured = list(configured_names)
        custom_provider_names = {
            str(cm['id']): str(cm.get('display_name') or cm.get('base_url') or cm['id'])
            for cm in custom_models
            if cm.get('id')
        }
        
        # Group custom models by display name to find primary ones
        custom_groups = {}
        for cm in custom_models:
            display_name = str(cm.get('display_name') or cm['id']).strip()
            if display_name not in custom_groups:
                custom_groups[display_name] = []
            custom_groups[display_name].append(cm)
            
        primary_customs = [group[0] for group in custom_groups.values()]
        
        for cm in primary_customs:
            if cm['id'] not in all_configured:
                all_configured.append(cm['id'])
                
        disabled = self.get_disabled_providers()
        active_set = set(configured_names) - set(disabled)
        for cm in primary_customs:
            if cm.get('enabled', True) is False and cm['id'] in active_set:
                active_set.remove(cm['id'])

        disabled_models = self.get_disabled_models()
        temp_disabled = temporary_disabled_models(self.health_path)

        known_model_keys: set[str] = set()
        for key in manual_order:
            if isinstance(key, str) and '/' in key:
                known_model_keys.add(key)
        for key in probe_results:
            if isinstance(key, str) and '/' in key:
                known_model_keys.add(key)
        for key in health:
            if isinstance(key, str) and '/' in key:
                known_model_keys.add(key)
        for key in disabled_models:
            if isinstance(key, str) and '/' in key:
                known_model_keys.add(key)

        import concurrent.futures
        
        provider_models = {}
        def fetch_p(p_name: str) -> list[str]:
            if p_name == 'qoder':
                return [k.split('/', 1)[1] for k in known_model_keys if k.startswith(f'{p_name}/')]
            elif p_name.startswith('custom-'):
                return []
            return self.get_cached_provider_models(p_name, refresh=False)

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(20, max(1, len(configured_names)))) as executor:
            futures = {executor.submit(fetch_p, p_name): p_name for p_name in configured_names}
            for future in concurrent.futures.as_completed(futures):
                try:
                    provider_models[futures[future]] = future.result()
                except Exception:
                    provider_models[futures[future]] = []
        for cm in primary_customs:
            if cm.get('enabled', True) is not False and cm.get('verified') is True:
                # Merge model hints from all custom models in its display group to have the union of all models
                display_name = str(cm.get('display_name') or cm['id']).strip()
                group_cms = custom_groups.get(display_name, [cm])
                union_models = []
                for g_cm in group_cms:
                    if g_cm.get('enabled', True) is not False and g_cm.get('verified') is True:
                        values = g_cm.get('models') if isinstance(g_cm.get('models'), list) else [g_cm.get('model')]
                        for val in values:
                            val_str = str(val or '').strip()
                            if val_str and val_str not in union_models:
                                union_models.append(val_str)
                for key in known_model_keys:
                    if key.startswith(f"{cm['id']}/"):
                        val_str = key.split('/', 1)[1]
                        if val_str and val_str not in union_models:
                            union_models.append(val_str)
                provider_models[str(cm['id'])] = union_models

        try:
            candidates = build_auto_candidates(
                requested_model=None,
                configured=all_configured,
                health=health,
                now_ts=int(time.time()),
                ttl_seconds=self.health_ttl_seconds,
                manual_order=manual_order,
                disabled_models=disabled_models,
                provider_models=provider_models,
            )
        except Exception:
            logger.exception('models_stats candidate build failed')
            candidates = []
            seen: set[tuple[str, str]] = set()
            for provider_name in all_configured:
                for model_id in provider_models.get(provider_name, []):
                    model_text = str(model_id or '').strip()
                    key = f'{provider_name}/{model_text}'
                    pair = (provider_name, model_text)
                    if not model_text or pair in seen or key in disabled_models:
                        continue
                    seen.add(pair)
                    candidates.append(CandidateTarget(provider_name, model_text, 'provider_default', len(candidates)))
        stats = []
        provider_key_counts: dict[str, int] = {}
        for i, c in enumerate(candidates):
            key = f"{c.provider}/{c.model}"
            entry = health.get(key, {})
            probe = probe_results.get(key, {})
            analysis = analysis_stats.get(key, {})
            if not isinstance(entry, dict):
                entry = {}
            if not isinstance(probe, dict):
                probe = {}
            if not isinstance(analysis, dict):
                analysis = {}
            chat_candidate = is_chat_candidate_model(c.provider, c.model)
            temporary_disabled = temp_disabled.get(key, {})
            is_temporarily_disabled = key in temp_disabled
            probe_ok = probe.get('ok') if isinstance(probe, dict) else None
            probe_error = str(probe.get('error') or '') if isinstance(probe, dict) else ''
            probe_status_code = probe.get('status') if isinstance(probe, dict) else None
            probe_latency_ms = probe.get('latency_ms') if isinstance(probe.get('latency_ms'), int) else None
            probe_category = ''
            if probe_ok is False:
                try:
                    probe_category = classify_error(int(probe_status_code or 0), probe_error).category
                except Exception:
                    probe_category = classify_error(0, probe_error).category
            health_reason = str(entry.get('reason') or '') if isinstance(entry, dict) else ''
            is_unavailable_model = (
                is_permanent_unavailable_category(health_reason)
                or is_permanent_unavailable_category(probe_category)
            )
            is_recoverable_model = (
                not is_unavailable_model
                and (
                    probe_ok is False
                    or (entry.get('ok') is False and bool(health_reason))
                )
            )
            unavailable_error = ''
            if is_unavailable_model:
                reason_for_label = health_reason or probe_category
                unavailable_error = 'API Key 无效或权限不足' if reason_for_label == 'auth' else '模型不存在或不可用'
            analysis_success_count = int(analysis.get('success_count') or 0)
            recent_error = str(analysis.get('recent_error') or '')
            if analysis_success_count > 0 and not recent_error:
                is_unavailable_model = False
                is_recoverable_model = False
                unavailable_error = ''
            probe_status = (
                'failed' if is_unavailable_model
                else 'success' if probe_ok is True or analysis_success_count > 0
                else 'recoverable' if is_recoverable_model
                else 'untested'
            )
            is_failed_probe = probe_status == 'failed'
            analysis_status = str(analysis.get('analysis_status') or 'ok')
            hide_reason = str(analysis.get('hide_reason') or '')
            probe_checked_at = probe.get('checked_at') if isinstance(probe.get('checked_at'), int) else None
            verified_after_reset = probe_ok is True and isinstance(probe_checked_at, int) and probe_checked_at >= daily_reset_ts
            if analysis_status == 'hidden':
                if verified_after_reset:
                    analysis_status = 'ok'
                    hide_reason = ''
                elif not is_unavailable_model:
                    analysis_status = 'retest_required'
                    hide_reason = f'{hide_reason} · 等待今日复测' if hide_reason else '等待今日复测'
            is_currently_callable = probe_status in {'success', 'recoverable'} and analysis_status == 'ok'
            
            # Use freellmapi Bandit logic for display!
            success_streak = int(entry.get('success_streak', 0)) if isinstance(entry, dict) else 0
            failure_streak = int(entry.get('failure_streak', 0)) if isinstance(entry, dict) else 0
            # To simulate historical totals, we just use the current streaks as totals for visual display
            reliability = expected_reliability(success_streak, failure_streak)
            speed = synthetic_speed_score(c.provider, c.model)
            intel = synthetic_intelligence_score(c.provider, c.model)
            
            # Headroom based on rate limits
            rate_limits = entry.get('rate_limits', {}) if isinstance(entry, dict) else {}
            remaining_req = None
            if 'x-ratelimit-remaining-requests' in rate_limits:
                try:
                    remaining_req = int(rate_limits['x-ratelimit-remaining-requests'])
                except ValueError:
                    pass
            headroom = headroom_factor(remaining_req)
            
            score = combine_score(reliability, speed, intel, headroom, 1.0, BANDIT_PRESETS['balanced'])
            route_priority = route_priority_sort_key(c.provider, c.model, entry)
            
            limits = get_model_limits(c.provider, c.model)
            
            p_display = custom_provider_names.get(c.provider, c.provider)
            model_display = ''
            model_key_display = c.model
            if c.provider == 'qoder':
                p_display = 'Qoder'
                model_display = qoder_model_display_name(c.model)
                model_key_display = qoder_model_key_display(c.model)
            if c.provider not in provider_key_counts:
                try:
                    provider_key_counts[c.provider] = len(self.get_provider_keys(c.provider))
                except Exception:
                    logger.exception('models_stats provider key count failed: %s', c.provider)
                    provider_key_counts[c.provider] = 0
            if provider_key_counts[c.provider] > 1:
                p_display = f"{p_display} (轮询)"
                
            stats.append({
                'provider': c.provider,
                'provider_display': p_display,
                'model': c.model,
                'model_display': model_display,
                'model_key_display': model_key_display,
                'source': c.source,
                'rank': c.rank,
                'score': score,
                'route_priority': [round(float(route_priority[0]), 6), round(float(route_priority[1]), 6), round(float(route_priority[2]), 6)],
                'rel': int(reliability * 100),
                'spd': int(speed * 100),
                'int': int(intel * 100),
                'headroom': float(f"{headroom:.2f}"),
                'ok': entry.get('ok') if isinstance(entry, dict) else None,
                'probe_status': probe_status,
                'latency_ms': probe_latency_ms,
                'probe_checked_at': probe_checked_at,
                'probe_error': unavailable_error or probe_error,
                'probe_category': health_reason or probe_category,
                'rate_limits': rate_limits,
                'observations': success_streak + failure_streak,
                'monthly_token_budget': limits['monthly_token_budget'],
                'rpm_limit': limits['rpm_limit'],
                'rpd_limit': limits['rpd_limit'],
                'enabled': (c.provider in active_set) and (key not in disabled_models) and not is_temporarily_disabled and is_currently_callable,
                'manually_enabled': (c.provider in active_set) and (key not in disabled_models) and not is_failed_probe,
                'temporarily_disabled': is_temporarily_disabled,
                'disabled_until': temporary_disabled.get('disabled_until') if isinstance(temporary_disabled, dict) else None,
                'disabled_reason': temporary_disabled.get('disabled_reason') if isinstance(temporary_disabled, dict) else '',
                'usage_count': analysis.get('usage_count', 0),
                'success_count': analysis.get('success_count', 0),
                'success_rate': analysis.get('success_rate'),
                'avg_latency_ms': analysis.get('avg_latency_ms'),
                'recent_error': analysis.get('recent_error', ''),
                'analysis_status': analysis_status,
                'hide_reason': hide_reason,
                'chat_candidate': chat_candidate,
            })
            
        manual_rank = {key: index for index, key in enumerate(manual_order)}
        stats.sort(key=lambda item: manual_rank.get(f"{item.get('provider')}/{item.get('model')}", len(manual_rank) + int(item.get('rank') or 0)))
        for index, item in enumerate(stats):
            item['rank'] = index

        # Keep manual_order ahead of score-based capabilities.
        return {'models': stats, 'strategy': 'priority'}

    def models_stats_fallback(self) -> dict[str, object]:
        from .provider_catalog import get_provider_model_hints
        from .scoring import synthetic_speed_score, synthetic_intelligence_score, get_model_limits, is_chat_candidate_model

        try:
            db_keys = get_all_keys(self.db_url)
        except Exception:
            logger.exception('models_stats_fallback config key load failed')
            db_keys = {}

        try:
            probe_results = get_model_probe_results(self.db_url)
        except Exception:
            logger.exception('models_stats_fallback probe result load failed')
            probe_results = {}

        try:
            manual_order = self.get_manual_order()
        except Exception:
            logger.exception('models_stats_fallback manual order load failed')
            manual_order = []

        try:
            analysis_stats = self._model_analysis_stats()
        except Exception:
            logger.exception('models_stats_fallback analysis load failed')
            analysis_stats = {}

        try:
            configured_names = configured_provider_names(db_keys)
        except Exception:
            logger.exception('models_stats_fallback provider detection failed')
            configured_names = []

        try:
            if self._loaded_account_provider_accounts('qoder') and 'qoder' not in configured_names:
                configured_names.append('qoder')
        except Exception:
            logger.exception('models_stats_fallback qoder detection failed')

        custom_provider_names: dict[str, str] = {}
        provider_models: dict[str, list[str]] = {}
        try:
            custom_models = self.get_custom_models()
        except Exception:
            logger.exception('models_stats_fallback custom model load failed')
            custom_models = []

        custom_groups: dict[str, list[dict[str, object]]] = {}
        for cm in custom_models:
            if not isinstance(cm, dict) or not cm.get('id'):
                continue
            display_name = str(cm.get('display_name') or cm.get('id')).strip()
            custom_groups.setdefault(display_name, []).append(cm)

        for group in custom_groups.values():
            primary = group[0]
            provider_id = str(primary.get('id') or '').strip()
            if not provider_id or primary.get('enabled', True) is False:
                continue
            models: list[str] = []
            for cm in group:
                if cm.get('enabled', True) is False or cm.get('verified') is False:
                    continue
                values = cm.get('models') if isinstance(cm.get('models'), list) else [cm.get('model')]
                for value in values:
                    model_id = str(value or '').strip()
                    if model_id and model_id not in models:
                        models.append(model_id)
            if models:
                configured_names.append(provider_id)
                provider_models[provider_id] = models
                custom_provider_names[provider_id] = str(primary.get('display_name') or primary.get('base_url') or provider_id)

        stats: list[dict[str, object]] = []
        seen: set[str] = set()
        for provider_name in configured_names:
            try:
                hints = provider_models.get(provider_name) or get_provider_model_hints(provider_name)
            except Exception:
                logger.exception('models_stats_fallback hint load failed: %s', provider_name)
                continue
            for model_id in hints:
                model_text = str(model_id or '').strip()
                key = f'{provider_name}/{model_text}'
                if not model_text or key in seen:
                    continue
                seen.add(key)
                probe = probe_results.get(key, {})
                if not isinstance(probe, dict):
                    probe = {}
                probe_ok = probe.get('ok')
                probe_error = str(probe.get('error') or '')
                probe_status_code = probe.get('status')
                probe_latency_ms = probe.get('latency_ms') if isinstance(probe.get('latency_ms'), int) else None
                probe_category = ''
                if probe_ok is False:
                    try:
                        probe_category = classify_error(int(probe_status_code or 0), probe_error).category
                    except Exception:
                        probe_category = classify_error(0, probe_error).category
                analysis = analysis_stats.get(key, {}) if isinstance(analysis_stats, dict) else {}
                if not isinstance(analysis, dict):
                    analysis = {}
                recent_error = str(analysis.get('recent_error') or '')
                analysis_success_count = int(analysis.get('success_count') or 0)
                inferred_category = probe_category
                if not inferred_category and recent_error:
                    inferred_category = classify_error(0, recent_error).category
                probe_status = (
                    'failed' if is_permanent_unavailable_category(inferred_category)
                    else 'success' if probe_ok is True or analysis_success_count > 0
                    else 'recoverable' if inferred_category in {'server', 'network', 'rate_limit', 'quota', 'unknown'} and bool(recent_error)
                    else 'recoverable' if probe_ok is False
                    else 'untested'
                )
                try:
                    limits = get_model_limits(provider_name, model_text)
                    speed = synthetic_speed_score(provider_name, model_text)
                    intel = synthetic_intelligence_score(provider_name, model_text)
                    chat_candidate = is_chat_candidate_model(provider_name, model_text)
                except Exception:
                    logger.exception('models_stats_fallback stat build failed: %s', key)
                    continue
                stats.append({
                    'provider': provider_name,
                    'provider_display': custom_provider_names.get(provider_name, 'Qoder' if provider_name == 'qoder' else provider_name),
                    'model': model_text,
                    'model_display': '',
                    'model_key_display': model_text,
                    'source': 'fallback',
                    'rank': len(stats),
                    'score': round((speed + intel + 0.6) / 3, 4),
                    'route_priority': [round(float(intel), 6), round(float(speed), 6), 0.6],
                    'rel': 60,
                    'spd': int(speed * 100),
                    'int': int(intel * 100),
                    'headroom': 1.0,
                    'ok': probe_ok,
                    'probe_status': probe_status,
                    'latency_ms': probe_latency_ms,
                    'probe_checked_at': probe.get('checked_at') if isinstance(probe.get('checked_at'), int) else None,
                    'probe_error': probe_error,
                    'probe_category': probe_category,
                    'rate_limits': {},
                    'observations': 0,
                    'monthly_token_budget': limits['monthly_token_budget'],
                    'rpm_limit': limits['rpm_limit'],
                    'rpd_limit': limits['rpd_limit'],
                    'enabled': chat_candidate and probe_status != 'failed',
                    'manually_enabled': chat_candidate and probe_status != 'failed',
                    'temporarily_disabled': False,
                    'disabled_until': None,
                    'disabled_reason': '',
                    'usage_count': analysis.get('usage_count', 0),
                    'success_count': analysis.get('success_count', 0),
                    'success_rate': analysis.get('success_rate'),
                    'avg_latency_ms': analysis.get('avg_latency_ms'),
                    'recent_error': recent_error,
                    'analysis_status': analysis.get('analysis_status', 'ok'),
                    'hide_reason': analysis.get('hide_reason', ''),
                    'chat_candidate': chat_candidate,
                })

        manual_rank = {key: index for index, key in enumerate(manual_order)}
        stats.sort(key=lambda item: manual_rank.get(f"{item.get('provider')}/{item.get('model')}", len(manual_rank) + int(item.get('rank') or 0)))
        for index, item in enumerate(stats):
            item['rank'] = index

        return {'models': stats, 'strategy': 'priority', 'fallback': True}

    def route_model_order(self) -> list[tuple[str, str]]:
        try:
            data = self.models_stats()
        except Exception:
            logger.exception('route_model_order models_stats failed')
            data = self.models_stats_fallback()
        items = data.get('models', []) if isinstance(data, dict) else []
        ordered: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get('enabled') is False or item.get('chat_candidate') is False:
                continue
            provider = str(item.get('provider') or '').strip()
            model = str(item.get('model') or '').strip()
            if provider and model:
                ordered.append((provider, model))
        return ordered

    def resolve_openai_target(self, payload: JsonObject) -> ResolvedOpenAIRequest:
        raw_model = payload.get('model')
        raw_provider = payload.get('provider')
        resolved = resolve_model_request(
            model=str(raw_model) if isinstance(raw_model, str) else '',
            provider=str(raw_provider) if isinstance(raw_provider, str) else None,
            configured=self.available_providers(),
            known_providers={provider.name for provider in list_providers()},
        )
        return ResolvedOpenAIRequest(provider=resolved.provider, model=resolved.model, alias=resolved.alias)

    @staticmethod
    def _content_type(headers: dict[str, str]) -> str:
        return str(headers.get('Content-Type') or headers.get('content-type') or '').lower()

    @staticmethod
    def _sse_json_line(payload: JsonObject | str) -> bytes:
        if isinstance(payload, str):
            return f'data: {payload}\n\n'.encode('utf-8')
        return f'data: {json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}\n\n'.encode('utf-8')

    @staticmethod
    def _sse_done_chunk() -> Iterable[bytes]:
        yield ProxyService._sse_json_line('[DONE]')

    @staticmethod
    def _sanitize_openai_forward_payload(provider_name: str, model_id: str, payload: JsonObject) -> tuple[str, JsonObject]:
        normalized_model = model_id.strip()
        provider_prefix = f'{provider_name}/'
        if normalized_model.startswith(provider_prefix):
            normalized_model = normalized_model.removeprefix(provider_prefix)
        request_payload = dict(payload)
        request_payload.pop('provider', None)
        request_payload['model'] = normalized_model
        return normalized_model, request_payload

    def execute_openai_target(self, target: ResolvedOpenAIRequest, payload: JsonObject) -> OpenAIForwardResult:
        if target.alias is not None:
            return self.forward_alias_chat(target.alias, payload)
        if target.provider is None:
            return OpenAIForwardResult(
                ok=False,
                provider='free-proxy',
                model=target.model,
                status=400,
                headers={},
                body=b'',
                error='missing provider',
                category='invalid_request_error',
            )
        return self.forward_direct_chat(target.provider, target.model, payload)

    def forward_alias_chat(self, alias: AliasName, payload: JsonObject) -> OpenAIForwardResult:
        candidates = self.route_model_order() if alias == 'auto' else []
        if not candidates:
            candidates = resolve_alias_candidates(
                alias,
                self.available_providers(),
                health=load_health(self.health_path),
                now_ts=int(time.time()),
                ttl_seconds=self.health_ttl_seconds,
                manual_order=self.get_manual_order(),
                disabled_models=self.get_disabled_models()
            )
        if not candidates:
            return OpenAIForwardResult(
                ok=False,
                provider='free-proxy',
                model=alias,
                status=400,
                headers={},
                body=b'',
                error='no configured providers found, please save at least one API key first',
                category='invalid_request_error',
            )
        last_result: OpenAIForwardResult | None = None
        for provider_name, model_id in candidates:
            result = self.forward_direct_chat(provider_name, model_id, payload)
            if result.ok:
                return result
            last_result = result
        if last_result is not None:
            return last_result
        return OpenAIForwardResult(
            ok=False,
            provider='free-proxy',
            model=alias,
            status=502,
            headers={},
            body=b'',
            error='no available model found from configured providers',
            category='server_error',
        )

    def forward_direct_chat(self, provider_name: str, model_id: str, payload: JsonObject) -> OpenAIForwardResult:
        provider = get_provider(provider_name)
        if provider.format != 'openai':
            prompt = self._extract_prompt(payload)
            requested_output_tokens = self._requested_output_tokens(payload)
            result = self.chat(provider_name, model_id, prompt, max_output_tokens=requested_output_tokens)
            if result.ok:
                actual_model = result.actual_model or model_id
                return OpenAIForwardResult(
                    ok=True,
                    provider=provider_name,
                    model=actual_model,
                    status=200,
                    headers={},
                    body=b'',
                    content=result.content,
                )
            return OpenAIForwardResult(
                ok=False,
                provider=provider_name,
                model=model_id,
                status=result.status or 502,
                headers={},
                body=b'',
                error=result.error,
                category=result.category,
                suggestion=result.suggestion,
            )

        adapter = self.provider_adapter(provider_name)
        normalized_model_id, request_payload = self._sanitize_openai_forward_payload(provider_name, model_id, payload)
        runtime_start = time.time()
        self.mark_runtime_model_start(provider_name, normalized_model_id)
        prompt = self._extract_prompt(request_payload)
        requested_output_tokens = self._requested_output_tokens(request_payload)
        capabilities = get_model_capabilities(provider_name, normalized_model_id)
        if capabilities.get('reasoning') is True:
            requested_output_tokens = min(requested_output_tokens or model_default_output_tokens(provider_name, normalized_model_id, 1024), model_default_output_tokens(provider_name, normalized_model_id, 1024))
        budget = resolve_token_budget(
            provider=provider_name,
            model=normalized_model_id,
            prompt=prompt,
            requested_output_tokens=requested_output_tokens,
            learned_limits=load_token_limits(self.token_limit_path),
            model_metadata=None,
        )
        request_payload['max_tokens'] = budget.output_tokens_limit
        if not isinstance(request_payload.get('messages'), list) or not request_payload.get('messages'):
            request_payload['messages'] = [{'role': 'user', 'content': budget.trimmed_prompt}]
            request_payload.pop('prompt', None)

        requested_stream = bool(payload.get('stream'))
        capabilities = get_model_capabilities(provider_name, normalized_model_id)
        upstream_stream = requested_stream and capabilities.get('streaming', False)
        request_payload['stream'] = upstream_stream

        try:
            if upstream_stream:
                status, headers, stream_iter = adapter.chat_completions_stream(request_payload)
            else:
                status, headers, body = adapter.chat_completions_raw(request_payload)
                stream_iter = None
        except ProviderError as exc:
            category = classify_error(0, str(exc)).category
            self.mark_runtime_model_finish(provider_name, normalized_model_id, False, int((time.time() - runtime_start) * 1000), str(exc))
            return OpenAIForwardResult(
                ok=False,
                provider=provider_name,
                model=normalized_model_id,
                status=502,
                headers={},
                body=b'',
                error=str(exc),
                category=category,
                suggestion=remediation_suggestion(category, provider_name),
            )

        if status < 400:
            upsert_health(provider_name, normalized_model_id, True, headers=headers, path=self.health_path)
            if upstream_stream:
                return OpenAIForwardResult(ok=True, provider=provider_name, model=normalized_model_id, status=status, headers=headers, body=None, stream_chunks=self._runtime_tracked_stream(stream_iter, provider_name, normalized_model_id, runtime_start))
            self.mark_runtime_model_finish(provider_name, normalized_model_id, True, int((time.time() - runtime_start) * 1000), None)
            return OpenAIForwardResult(ok=True, provider=provider_name, model=normalized_model_id, status=status, headers=headers, body=body)

        error_body = b''.join(bytes(chunk) for chunk in stream_iter if chunk) if upstream_stream else body
        text = error_body.decode('utf-8', errors='ignore')
        failure = classify_error(status, text)
        self.mark_runtime_model_finish(provider_name, normalized_model_id, False, int((time.time() - runtime_start) * 1000), text or f'upstream status {status}')
        upsert_health(provider_name, normalized_model_id, False, reason=failure.category, headers=headers, path=self.health_path)
        if self.debug_log is not None:
            self.debug_log(
                'request_failed',
                provider=provider_name,
                model=normalized_model_id,
                status=status,
                category=failure.category,
                error=text or f'upstream status {status}',
                suggestion=remediation_suggestion(failure.category, provider_name),
            )
        return OpenAIForwardResult(
            ok=False,
            provider=provider_name,
            model=normalized_model_id,
            status=status,
            headers=headers,
            body=error_body if upstream_stream else b'',
            error=text or f'upstream status {status}',
            category=failure.category,
            suggestion=remediation_suggestion(failure.category, provider_name),
        )

    def _extract_prompt(self, payload: JsonObject) -> str:
        from .prompt_utils import extract_prompt
        return extract_prompt(payload)

    def _runtime_tracked_stream(self, stream: Iterable[bytes], provider_name: str, model_id: str, start_time: float) -> Iterable[bytes]:
        ok = True
        error: str | None = None
        try:
            for chunk in stream:
                yield chunk
        except Exception as exc:
            ok = False
            error = str(exc)
            raise
        finally:
            if hasattr(stream, 'close'):
                stream.close()
            self.mark_runtime_model_finish(provider_name, model_id, ok, int((time.time() - start_time) * 1000), error)

    @staticmethod
    def _requested_output_tokens(payload: JsonObject) -> int | None:
        for key in ('max_tokens', 'max_completion_tokens', 'max_output_tokens'):
            value = payload.get(key)
            if isinstance(value, int):
                return value
        return None
