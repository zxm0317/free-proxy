from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from collections.abc import Callable, Iterable
from pathlib import Path

from .config import DOTENV_PATH, hydrate_env, load_dotenv
from .db_store import get_all_keys, get_key, init_db, upsert_key, increment_model_usage, get_model_usage_stats, get_manual_order, save_manual_order, log_request, get_model_probe_results, save_model_probe_result
from .errors import classify_error, remediation_suggestion
from .health_store import load_health, upsert_health
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
        self.sync_custom_providers()

    def sync_custom_providers(self) -> None:
        custom_models_json = get_key(self.db_url, 'custom_openai_models')
        if custom_models_json:
            try:
                custom_models = json.loads(custom_models_json)
                for cm in custom_models:
                    from .provider_catalog import register_custom_provider
                    register_custom_provider(
                        name=cm['id'],
                        base_url=cm['base_url'],
                        api_key_env=f"CUSTOM_KEY_{cm['id']}",
                        format='openai',
                        model_hints=[cm['model']]
                    )
            except Exception:
                pass

    def available_providers(self) -> list[str]:
        providers = configured_provider_names(get_all_keys(self.db_url))
        disabled = self.get_disabled_providers()
        providers = [p for p in providers if p not in disabled and not p.startswith('custom-')]
        custom_models = self.get_custom_models()
        for cm in custom_models:
            if cm.get('enabled', True) is not False:
                providers.append(cm['id'])
        return providers

    def public_models(self) -> list[dict[str, str]]:
        return [dict(item) for item in PUBLIC_MODEL_ALIASES]

    def provider_adapter(self, provider_name: str) -> ProviderAdapter:
        provider = get_provider(provider_name)
        api_key = get_key(self.db_url, provider.api_key_env) or ''
        if not api_key and not provider_name.startswith('custom-'):
            raise ProviderError(f'{provider_name} 没有配置 API Key')
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
            request_logger=lambda platform, model_id, status, input_tokens, output_tokens, latency_ms, error=None: log_request(self.db_url, platform, model_id, status, input_tokens, output_tokens, latency_ms, error),
        )

    def get_usage_stats(self) -> list[dict[str, object]]:
        return get_model_usage_stats(self.db_url)

    def get_manual_order(self, bypass_cache: bool = False) -> list[str]:
        return get_manual_order(self.db_url, bypass_cache)

    def save_manual_order(self, order: list[str]) -> None:
        save_manual_order(self.db_url, order)

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
        db_keys = get_all_keys(self.db_url)
        disabled = self.get_disabled_providers()
        statuses: dict[str, dict[str, object]] = {}
        for provider in list_providers():
            value = str(db_keys.get(provider.api_key_env, '')).strip()
            statuses[provider.name] = {
                'configured': bool(value),
                'masked': self._mask_key(value) if value else '',
                'enabled': bool(value) and (provider.name not in disabled),
                'models': [m for m in get_provider_model_hints(provider.name) if m.startswith('free-proxy/')],
            }
        return statuses

    def configure_provider_key(self, provider_name: str, api_key: str) -> dict[str, object]:
        provider = get_provider(provider_name)
        value = api_key.strip()
        if not value:
            raise ProviderError('api_key 不能为空')
        upsert_key(self.db_url, provider.api_key_env, value)
        return {'ok': True, 'provider': provider_name, 'masked': self._mask_key(value)}

    def delete_provider_key(self, provider_name: str) -> dict[str, object]:
        provider = get_provider(provider_name)
        from .db_store import delete_key
        delete_key(self.db_url, provider.api_key_env)
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
            return {'ok': True}

    def get_disabled_models(self) -> list[str]:
        data_str = get_key(self.db_url, 'disabled_models')
        if not data_str:
            return []
        try:
            import json
            return json.loads(data_str)
        except Exception:
            return []

    def toggle_model(self, model_key: str, enabled: bool) -> dict[str, object]:
        import json
        disabled = self.get_disabled_models()
        if enabled:
            if model_key in disabled:
                disabled.remove(model_key)
        else:
            if model_key not in disabled:
                disabled.append(model_key)
        upsert_key(self.db_url, 'disabled_models', json.dumps(disabled))
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

    def get_custom_models(self) -> list[dict[str, object]]:
        data_str = get_key(self.db_url, 'custom_openai_models')
        if not data_str:
            return []
        try:
            return json.loads(data_str)
        except Exception:
            return []

    def add_custom_model(self, base_url: str, model: str, display_name: str = '', api_key: str = '') -> dict[str, object]:
        models = self.get_custom_models()
        custom_ids = {m['id'] for m in models}
        new_id = None
        i = 1
        while True:
            candidate = f"custom-{i}"
            if candidate not in custom_ids:
                new_id = candidate
                break
            i += 1
        
        new_model = {
            'id': new_id,
            'base_url': base_url,
            'model': model,
            'display_name': display_name or model,
            'api_key': api_key,
            'enabled': True,
            'created_at': int(time.time())
        }
        models.append(new_model)
        upsert_key(self.db_url, 'custom_openai_models', json.dumps(models))
        
        if api_key:
            upsert_key(self.db_url, f"CUSTOM_KEY_{new_id}", api_key)
            
        self.sync_custom_providers()
        return {'ok': True, 'model': new_model}

    def delete_custom_model(self, model_id: str) -> dict[str, object]:
        models = self.get_custom_models()
        filtered = [m for m in models if m['id'] != model_id]
        upsert_key(self.db_url, 'custom_openai_models', json.dumps(filtered))
        from .db_store import delete_key
        delete_key(self.db_url, f"CUSTOM_KEY_{model_id}")
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

    def verify_provider_key(self, provider_name: str) -> dict[str, object]:
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
            models = self.list_models(provider_name)
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

        for candidate in candidates[:3]:
            result = self.probe(provider_name, candidate)
            if result.ok:
                return {
                    'ok': True,
                    'provider': provider_name,
                    'models': candidates,
                    'category': None,
                    'verified_model': result.actual_model or candidate,
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
            failed = self.probe(provider_name, candidates[0])
            category = failed.category or classify_error(0, failed.error or '').category
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

    def list_models(self, provider_name: str) -> list[str]:
        return self.provider_adapter(provider_name).list_models()

    def probe(self, provider_name: str, model_id: str, timeout: int | None = None) -> ProbeResult:
        return self.chat(provider_name, model_id, prompt='ok', max_output_tokens=1, timeout=timeout)

    def chat(self, provider_name: str, model_id: str, prompt: str, max_output_tokens: int | None = None, timeout: int | None = None) -> ProbeResult:
        adapter = self.provider_adapter(provider_name)
        trimmed = trim_prompt(provider_name, prompt)
        candidates = [model_id]

        output_tokens = max_output_tokens if max_output_tokens is not None else response_token_budget(provider_name)
        learned_limits = load_token_limits(self.token_limit_path)
        last_error: str | None = None
        last_category: str | None = None
        last_status: int | None = None

        for candidate in candidates:
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
                upsert_health(provider_name, candidate, True, path=self.health_path)
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
                        upsert_health(provider_name, candidate, True, path=self.health_path)
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
        
    def get_cached_provider_models(self, provider_name: str) -> list[str]:
        now = time.time()
        if not hasattr(self, '_models_cache'):
            self._models_cache = {}
        if provider_name in self._models_cache:
            models, expiry = self._models_cache[provider_name]
            if now < expiry:
                return models
        try:
            adapter = self.provider_adapter(provider_name)
            models = adapter.list_models()
        except Exception:
            from .provider_catalog import get_provider_model_hints
            models = get_provider_model_hints(provider_name)
        self._models_cache[provider_name] = (models, now + 300)
        return models

    def models_stats(self) -> dict[str, object]:
        from .provider_routing import build_auto_candidates
        from .scoring import expected_reliability, synthetic_speed_score, synthetic_intelligence_score, headroom_factor, combine_score, BANDIT_PRESETS, get_model_limits
        health = load_health(self.health_path)
        manual_order = self.get_manual_order()
        probe_results = get_model_probe_results(self.db_url)
        
        db_keys = get_all_keys(self.db_url)
        configured_names = configured_provider_names(db_keys)
        all_configured = list(configured_names)
        custom_models = self.get_custom_models()
        for cm in custom_models:
            if cm['id'] not in all_configured:
                all_configured.append(cm['id'])
                
        disabled = self.get_disabled_providers()
        active_set = set(configured_names) - set(disabled)
        for cm in custom_models:
            if cm.get('enabled', True) is False and cm['id'] in active_set:
                active_set.remove(cm['id'])

        disabled_models = self.get_disabled_models()

        provider_models = {}
        for p_name in configured_names:
            if p_name.startswith('custom-'):
                continue
            provider_models[p_name] = self.get_cached_provider_models(p_name)

        candidates = build_auto_candidates(
            requested_model=None,
            configured=all_configured,
            health=health,
            now_ts=int(time.time()),
            ttl_seconds=self.health_ttl_seconds,
            manual_order=manual_order,
            provider_models=provider_models
        )
        stats = []
        for i, c in enumerate(candidates):
            key = f"{c.provider}/{c.model}"
            entry = health.get(key, {})
            probe = probe_results.get(key, {})
            probe_ok = probe.get('ok') if isinstance(probe, dict) else None
            
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
            
            limits = get_model_limits(c.provider, c.model)
            
            stats.append({
                'provider': c.provider,
                'model': c.model,
                'source': c.source,
                'rank': c.rank,
                'score': score,
                'rel': int(reliability * 100),
                'spd': int(speed * 100),
                'int': int(intel * 100),
                'headroom': float(f"{headroom:.2f}"),
                'ok': entry.get('ok') if isinstance(entry, dict) else None,
                'probe_status': 'success' if probe_ok is True else 'failed' if probe_ok is False else 'untested',
                'latency_ms': probe.get('latency_ms') if isinstance(probe.get('latency_ms'), int) else None,
                'probe_checked_at': probe.get('checked_at') if isinstance(probe.get('checked_at'), int) else None,
                'probe_error': str(probe.get('error') or '') if isinstance(probe, dict) else '',
                'rate_limits': rate_limits,
                'observations': success_streak + failure_streak,
                'monthly_token_budget': limits['monthly_token_budget'],
                'rpm_limit': limits['rpm_limit'],
                'rpd_limit': limits['rpd_limit'],
                'enabled': (c.provider in active_set) and (key not in disabled_models)
            })
            
        # Keep the order of candidates (which respects manual_order and capabilities)
        return {'models': stats, 'strategy': 'priority'}

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
                return OpenAIForwardResult(ok=True, provider=provider_name, model=normalized_model_id, status=status, headers=headers, body=None, stream_chunks=stream_iter)
            return OpenAIForwardResult(ok=True, provider=provider_name, model=normalized_model_id, status=status, headers=headers, body=body)

        error_body = b''.join(bytes(chunk) for chunk in stream_iter if chunk) if upstream_stream else body
        text = error_body.decode('utf-8', errors='ignore')
        failure = classify_error(status, text)
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

    @staticmethod
    def _requested_output_tokens(payload: JsonObject) -> int | None:
        for key in ('max_tokens', 'max_completion_tokens', 'max_output_tokens'):
            value = payload.get(key)
            if isinstance(value, int):
                return value
        return None
