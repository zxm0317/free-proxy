from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .config import settings
from .opencode_config import configure_opencode_provider, detect_opencode_config
from .openclaw_config import configure_openclaw_model, detect_openclaw_config, list_backups, restore_backup
from .errors import classify_error
from .provider_errors import ProviderError
from .service import ProxyService

logger = logging.getLogger('free-proxy')

app = FastAPI(title='free-proxy')

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_web_root = Path(__file__).resolve().parent / 'web'
if _web_root.exists() and _web_root.is_dir():
    app.mount('/web', StaticFiles(directory=str(_web_root)), name='web')
else:
    logger.warning(f"Web root directory not found: {_web_root}. Web UI will not be available.")

_service: ProxyService | None = None
_debug_enabled = False
_automatic_retest_task: asyncio.Task | None = None


def _debug_log(event: str, **fields: object) -> None:
    if not _debug_enabled:
        return
    parts = [f'event={event}']
    for key, value in fields.items():
        if key == 'messages' or key == 'prompt':
            continue
        parts.append(f'{key}={value}')
    logger.info(' '.join(parts))


def _trace_request(event: str, **fields: object) -> None:
    parts = [f'event={event}']
    for key, value in fields.items():
        text = str(value)
        if len(text) > 200:
            text = text[:200] + '...'
        parts.append(f'{key}={text}')
    print('TRACE_REQUEST ' + ' '.join(parts), flush=True)


def get_service() -> ProxyService:
    global _service
    if _service is None:
        _service = ProxyService(debug_log=_debug_log)
    return _service

async def _automatic_retest_loop() -> None:
    await asyncio.sleep(3)
    while True:
        try:
            result = await asyncio.to_thread(get_service().automatic_retest_due_models)
            checked = int(result.get('checked', 0))
            if checked:
                logger.info('Automatic model retest completed: checked=%s', checked)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error('Automatic model retest failed: %s', exc)
        await asyncio.sleep(300)


@app.on_event("startup")
async def startup_event():
    global _automatic_retest_task
    try:
        logger.info('Initializing database connection and caching keys...')
        svc = get_service()
        await asyncio.gather(
            asyncio.to_thread(svc.get_manual_order),
            asyncio.to_thread(svc.get_disabled_models),
            asyncio.to_thread(svc.usable_model_keys),
            asyncio.to_thread(svc.account_provider_accounts, 'qoder'),
        )
        if svc.account_provider_accounts('qoder'):
            await asyncio.to_thread(svc.list_models, 'qoder')
        logger.info('Database cache initialized successfully.')
        _automatic_retest_task = asyncio.create_task(_automatic_retest_loop())
    except Exception as exc:
        logger.error(f"Startup initialization failed: {exc}")


@app.on_event("shutdown")
async def shutdown_event():
    global _automatic_retest_task
    if _automatic_retest_task is None:
        return
    _automatic_retest_task.cancel()
    try:
        await _automatic_retest_task
    except asyncio.CancelledError:
        pass
    _automatic_retest_task = None

_security = HTTPBearer(auto_error=False)

async def check_auth_openai(request: Request) -> str | JSONResponse:
    svc = get_service()
    expected_key = svc.get_proxy_key()
    if not expected_key:
        return JSONResponse(
            {'error': {'message': 'Proxy API Key is not configured. Please generate one in the UI first.', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}},
            status_code=401,
        )
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer ') or auth_header[7:] != expected_key:
        return JSONResponse(
            {'error': {'message': 'Invalid Proxy API Key', 'type': 'invalid_request_error', 'param': None, 'code': 'invalid_api_key'}},
            status_code=401,
        )
    return auth_header[7:]
def check_admin_auth(request: Request) -> str:
    # Local-only deployment: temporarily allow the web console without login.
    return request.cookies.get('adminToken') or 'local-dev'

def get_optional_admin_auth(request: Request) -> str | None:
    return request.cookies.get('adminToken') or 'local-dev'

class LoginRequest(BaseModel):
    password: str

@app.post('/api/auth/login')
async def auth_login(req: LoginRequest):
    admin_pwd = get_service().get_admin_password()
    if req.password == admin_pwd:
        resp = JSONResponse({'ok': True})
        resp.set_cookie('adminToken', admin_pwd, httponly=True, samesite='lax')
        return resp
    return JSONResponse({'ok': False, 'error': '密码错误'}, status_code=401)

@app.post('/api/auth/logout')
async def auth_logout():
    resp = JSONResponse({'ok': True})
    resp.delete_cookie('adminToken')
    return resp

def _invalid_json_response(*, openai: bool = False) -> JSONResponse:
    if openai:
        return JSONResponse(
            {'error': {'message': 'invalid json', 'type': 'invalid_request_error', 'param': None, 'code': None}},
            status_code=400,
        )
    return JSONResponse({'ok': False, 'error': 'invalid json'}, status_code=400)


async def _read_json_payload(request: Request, *, openai: bool = False) -> tuple[dict[str, object] | None, JSONResponse | None]:
    body = await request.body()
    if not body:
        return {}, None
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, _invalid_json_response(openai=openai)
    if not isinstance(payload, dict):
        return None, _invalid_json_response(openai=openai)
    return payload, None


def set_debug(enabled: bool) -> None:
    global _debug_enabled
    _debug_enabled = enabled
    if enabled:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s %(name)s: %(message)s',
            stream=sys.stderr,
        )


@app.middleware('http')
async def security_and_log_middleware(request: Request, call_next):
    if _debug_enabled:
        _debug_log(
            'request_received',
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else 'unknown',
        )
    response = await call_next(request)
    if _debug_enabled:
        _debug_log(
            'request_completed',
            method=request.method,
            path=request.url.path,
            status=response.status_code,
        )
    
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https:; connect-src 'self' https:;"
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    return response


@app.get('/')
async def index():
    return FileResponse(str(_web_root / 'index.html'))


@app.get('/login')
async def login_page():
    return FileResponse(str(_web_root / 'login.html'))


@app.get('/health')
async def health():
    return {'ok': True}


@app.get('/v1/models')
async def list_models():
    svc = get_service()
    return {'object': 'list', 'data': svc.public_models()}


@app.get('/api/proxy-key', dependencies=[Depends(check_admin_auth)])
async def get_proxy_key():
    svc = get_service()
    return {'key': svc.get_proxy_key()}


@app.post('/api/proxy-key/generate', dependencies=[Depends(check_admin_auth)])
async def generate_proxy_key():
    svc = get_service()
    return {'key': svc.generate_proxy_key()}


@app.get('/api/provider-keys')
async def get_provider_keys():
    svc = get_service()
    return svc.provider_key_statuses()


@app.get('/api/account-providers', dependencies=[Depends(check_admin_auth)])
async def get_account_providers():
    return get_service().account_provider_statuses()


@app.get('/api/account-providers/{provider}/login/start', dependencies=[Depends(check_admin_auth)])
async def start_account_provider_login(provider: str):
    try:
        return await run_in_threadpool(get_service().start_account_provider_login, provider)
    except ProviderError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.get('/api/account-providers/{provider}/login/poll', dependencies=[Depends(check_admin_auth)])
async def poll_account_provider_login(provider: str, state: str):
    try:
        return await run_in_threadpool(get_service().poll_account_provider_login, provider, state)
    except ProviderError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.get('/api/account-providers/{provider}/accounts', dependencies=[Depends(check_admin_auth)])
async def get_account_provider_accounts(provider: str):
    from .account_provider_store import public_account
    return {
        'ok': True,
        'provider': provider,
        'accounts': [public_account(account) for account in get_service().account_provider_accounts(provider)],
    }


@app.delete('/api/account-providers/{provider}/accounts/{account_id}', dependencies=[Depends(check_admin_auth)])
async def delete_account_provider_account(provider: str, account_id: str):
    try:
        return await run_in_threadpool(get_service().delete_account_provider_account, provider, account_id)
    except ProviderError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.post('/api/account-providers/{provider}/validate', dependencies=[Depends(check_admin_auth)])
async def validate_account_provider(provider: str):
    return await run_in_threadpool(get_service().validate_account_provider, provider)


@app.post('/api/account-providers/{provider}/probe-models', dependencies=[Depends(check_admin_auth)])
async def probe_account_provider_models(provider: str):
    return await run_in_threadpool(get_service().probe_account_provider_models, provider)


@app.get('/api/preferred-model')
async def get_preferred_model():
    svc = get_service()
    current = svc.preferred_model()
    if current:
        provider, model = current.split('/', 1)
        return {'ok': True, 'provider': provider, 'model': model, 'requested_model': current}
    return {'ok': True, 'provider': None, 'model': None, 'requested_model': None}


@app.get('/api/usage-stats', dependencies=[Depends(check_admin_auth)])
async def get_usage_stats():
    svc = get_service()
    stats = await run_in_threadpool(svc.get_usage_stats)
    return {'stats': stats}


@app.get('/api/models-stats')
async def get_models_stats():
    svc = get_service()
    try:
        return await run_in_threadpool(svc.models_stats_fallback)
    except Exception:
        logger.exception('models-stats route failed')
        return JSONResponse(
            {
                'ok': False,
                'error': 'models_stats_failed',
                'models': [],
                'strategy': 'priority',
            },
            status_code=200,
        )


@app.get('/api/runtime/current-model')
async def get_runtime_current_model():
    return get_service().runtime_model_status()


@app.get('/api/manual-order', dependencies=[Depends(check_admin_auth)])
async def get_manual_order():
    svc = get_service()
    order = await run_in_threadpool(svc.get_manual_order, True)
    return {'order': order}

@app.post('/api/manual-order', dependencies=[Depends(check_admin_auth)])
async def save_manual_order(request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    
    order = payload.get('order', [])
    if not isinstance(order, list):
        return JSONResponse({'ok': False, 'error': 'order must be a list'}, status_code=400)
        
    try:
        svc = get_service()
        str_order = [str(x) for x in order]
        await run_in_threadpool(svc.save_manual_order, str_order)
        return {'ok': True}
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)


@app.post('/api/preferred-model')
async def save_preferred_model(request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    provider = str(payload.get('provider', '')).strip()
    model = str(payload.get('model', '')).strip()
    if not provider or not model:
        return JSONResponse({'ok': False, 'error': 'missing provider or model'}, status_code=400)
    svc = get_service()
    try:
        result = svc.save_preferred_model(provider, model)
        return result
    except ProviderError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.get('/api/providers/{provider}/models/recommended')
async def recommended_models(provider: str, model: str | None = None):
    svc = get_service()
    items = await run_in_threadpool(svc.recommended_models, provider, model)
    return {'provider': provider, 'items': items}


@app.get('/api/detect-openclaw')
async def detect_openclaw():
    return detect_openclaw_config()


@app.get('/api/detect-opencode')
async def detect_opencode():
    return detect_opencode_config()


@app.get('/api/backups')
async def list_backups_route():
    return {'backups': list_backups()}


@app.get('/providers')
async def list_providers():
    svc = get_service()
    return {'providers': svc.available_providers()}


@app.get('/providers/{provider}/models')
async def provider_models(provider: str):
    svc = get_service()
    try:
        models = await run_in_threadpool(svc.list_models, provider)
        return {'provider': provider, 'models': models}
    except ProviderError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.post('/api/provider-keys/{provider}/verify')
async def verify_provider_key(provider: str):
    svc = get_service()
    result = await run_in_threadpool(svc.verify_provider_key, provider)
    return JSONResponse(result, status_code=200 if result.get('ok') else 400)


@app.post('/api/provider-keys/{provider}/{key_id}/verify')
async def verify_provider_key_by_id(provider: str, key_id: str):
    svc = get_service()
    try:
        result = await run_in_threadpool(svc.verify_provider_key, provider, key_id)
        return JSONResponse(result, status_code=200 if result.get('ok') else 400)
    except ProviderError as exc:
        return JSONResponse({'ok': False, 'provider': provider, 'error': str(exc)}, status_code=400)


@app.post('/api/configure-openclaw')
async def configure_openclaw(request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    mode = str(payload.get('mode', '')).strip()
    if mode not in {'default', 'fallback'}:
        return JSONResponse({'success': False, 'error': 'Invalid mode'}, status_code=400)
    svc = get_service()
    statuses = svc.provider_key_statuses()
    has_any_configured = any(bool(item.get('configured')) for item in statuses.values())
    if not has_any_configured:
        return JSONResponse({'success': False, 'error': 'Please configure at least one provider API key first'}, status_code=400)
    raw_port = os.environ.get('PORT', str(settings.port)).strip()
    try:
        port = int(raw_port)
    except ValueError:
        port = settings.port
    proxy_api_key = svc.get_proxy_key()
    if not proxy_api_key:
        return JSONResponse({'success': False, 'error': 'Please generate a Proxy API Key first'}, status_code=400)
    result = configure_openclaw_model(mode, port=port, proxy_api_key=proxy_api_key)
    if not result.get('success'):
        return JSONResponse(result, status_code=400)
    message = '已设为 OpenClaw 默认模型' if mode == 'default' else '已加入 OpenClaw 备用模型'
    return {'success': True, 'backup': result.get('backup'), 'message': message}


@app.post('/api/configure-opencode')
async def configure_opencode(request: Request):
    svc = get_service()
    statuses = svc.provider_key_statuses()
    has_any_configured = any(bool(item.get('configured')) for item in statuses.values())
    if not has_any_configured:
        return JSONResponse({'success': False, 'error': 'Please configure at least one provider API key first'}, status_code=400)
    raw_port = os.environ.get('PORT', str(settings.port)).strip()
    try:
        port = int(raw_port)
    except ValueError:
        port = settings.port
    proxy_api_key = svc.get_proxy_key()
    if not proxy_api_key:
        return JSONResponse({'success': False, 'error': 'Please generate a Proxy API Key first'}, status_code=400)
    result = configure_opencode_provider(port=port, proxy_api_key=proxy_api_key)
    if not result.get('success'):
        return JSONResponse(result, status_code=400)
    return {'success': True, 'backup': result.get('backup'), 'message': '已写入 Opencode free-proxy provider'}


@app.post('/api/restore-backup')
async def restore_backup_route(request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    backup = str(payload.get('backup', '')).strip()
    if not backup:
        return JSONResponse({'success': False, 'error': 'Backup filename is required'}, status_code=400)
    result = restore_backup(backup)
    if not result.get('success'):
        return JSONResponse(result, status_code=400)
    return {'success': True, 'message': 'Restore successful'}


@app.post('/api/provider-keys/{provider}')
async def save_provider_key(provider: str, request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    api_key = str(payload.get('api_key', '')).strip()
    if not api_key:
        return JSONResponse({'ok': False, 'error': 'missing api_key'}, status_code=400)
    svc = get_service()
    try:
        result = svc.configure_provider_key(provider, api_key)
        return result
    except ProviderError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.post('/api/provider-keys/{provider}/{key_id}')
async def save_provider_key_by_id(provider: str, key_id: str, request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    api_key = str(payload.get('api_key', '')).strip()
    label = str(payload.get('label', '')).strip()
    if not api_key:
        return JSONResponse({'ok': False, 'error': 'missing api_key'}, status_code=400)
    svc = get_service()
    try:
        result = svc.configure_provider_key(provider, api_key, label=label, key_id=key_id)
        return result
    except ProviderError as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=400)


@app.delete('/api/provider-keys/{provider}')
async def delete_provider_key(provider: str):
    svc = get_service()
    try:
        result = svc.delete_provider_key(provider)
        return result
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)


@app.delete('/api/provider-keys/{provider}/{key_id}')
async def delete_provider_key_by_id(provider: str, key_id: str):
    svc = get_service()
    try:
        result = svc.delete_provider_key(provider, key_id)
        return result
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)


@app.get('/api/custom-models')
async def get_custom_models():
    svc = get_service()
    return {'ok': True, 'models': svc.get_custom_models()}


@app.post('/api/custom-models')
async def add_custom_model(request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    base_url = str(payload.get('base_url', '')).strip()
    model = str(payload.get('model', '')).strip()
    if not base_url or not model:
        return JSONResponse({'ok': False, 'error': 'missing base_url or model'}, status_code=400)
    display_name = str(payload.get('display_name', '')).strip()
    api_key = str(payload.get('api_key', '')).strip()
    
    svc = get_service()
    try:
        result = svc.add_custom_model(base_url, model, display_name, api_key)
        return result
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)


@app.delete('/api/custom-models/{model_id}')
async def delete_custom_model(model_id: str):
    svc = get_service()
    try:
        result = svc.delete_custom_model(model_id)
        return result
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)


@app.post('/api/providers/{provider_name}/toggle')
async def toggle_provider(provider_name: str, request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    enabled = bool(payload.get('enabled', True))
    svc = get_service()
    try:
        result = svc.toggle_provider(provider_name, enabled)
        return result
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)


@app.post('/api/models/toggle')
async def toggle_model(request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    model_id = str(payload.get('model_id', '')).strip()
    enabled = bool(payload.get('enabled', True))
    if not model_id:
        return JSONResponse({'ok': False, 'error': 'model_id is required'}, status_code=400)
    svc = get_service()
    try:
        result = svc.toggle_model(model_id, enabled)
        return result
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)


@app.post('/api/models/test')
async def test_model(request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    model_key = str(payload.get('model_key', '')).strip()
    if not model_key or '/' not in model_key:
        return JSONResponse({'ok': False, 'error': 'model_key is required (format: provider/model)'}, status_code=400)
    provider_name, model_id = model_key.split('/', 1)
    svc = get_service()
    start_time = time.time()
    try:
        import asyncio
        probe_timeout = 45 if provider_name == 'qoder' else 3
        wait_timeout = 60 if provider_name == 'qoder' else 6
        result = await asyncio.wait_for(
            run_in_threadpool(svc.probe, provider_name, model_id, timeout=probe_timeout),
            timeout=wait_timeout,
        )
        latency = int((time.time() - start_time) * 1000)
        if result.ok:
            await run_in_threadpool(svc.record_model_probe_result, model_key, ok=True, latency_ms=latency, status=200)
            return {
                'ok': True,
                'status': 200,
                'latency_ms': latency,
                'message': 'Success'
            }
        else:
            status_code = result.status if result.status is not None else 500
            await run_in_threadpool(svc.record_model_probe_result, model_key, ok=False, latency_ms=latency, status=status_code, error=result.error or '探测失败')
            return {
                'ok': False,
                'status': status_code,
                'latency_ms': latency,
                'error': result.error or '探测失败',
                'category': result.category or classify_error(status_code, result.error or '').category,
            }
    except TimeoutError:
        latency = int((time.time() - start_time) * 1000)
        await run_in_threadpool(svc.record_model_probe_result, model_key, ok=False, latency_ms=latency, status=504, error='探测超时')
        return {
            'ok': False,
            'status': 504,
            'latency_ms': latency,
            'error': '探测超时',
            'category': 'network',
        }
    except Exception as exc:
        latency = int((time.time() - start_time) * 1000)
        category = classify_error(500, str(exc)).category
        await run_in_threadpool(svc.record_model_probe_result, model_key, ok=False, latency_ms=latency, status=500, error=str(exc))
        return {
            'ok': False,
            'status': 500,
            'latency_ms': latency,
            'error': str(exc),
            'category': category,
        }


@app.post('/api/custom-models/{model_id}/key')
async def update_custom_model_key(model_id: str, request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    api_key = str(payload.get('api_key', '')).strip()
    svc = get_service()
    try:
        result = svc.update_custom_model_key(model_id, api_key)
        return result
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)



@app.post('/providers/{provider}/probe')
async def probe_provider(provider: str, request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    model = str(payload.get('model', '')).strip()
    if not model:
        return JSONResponse({'ok': False, 'error': 'missing model'}, status_code=400)
    svc = get_service()
    try:
        result = await run_in_threadpool(svc.probe, provider, model)
        if _debug_enabled:
            _debug_log(
                'probe_result',
                provider=provider,
                model=model,
                ok=result.ok,
                actual_model=result.actual_model,
                error=result.error,
                category=result.category,
                status=result.status,
            )
        return JSONResponse(result.__dict__, status_code=200 if result.ok else 400)
    except Exception as exc:
        if _debug_enabled:
            _debug_log(
                'probe_error',
                provider=provider,
                model=model,
                error=str(exc),
            )
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)


@app.post('/chat/completions', dependencies=[Depends(check_admin_auth)])
async def legacy_chat_completions(request: Request):
    payload, error_response = await _read_json_payload(request)
    if error_response is not None:
        return error_response
    provider = str(payload.get('provider', '')).strip()
    model = str(payload.get('model', '')).strip()
    if not provider or not model:
        return JSONResponse({'ok': False, 'error': 'missing provider or model'}, status_code=400)
    svc = get_service()
    stream = bool(payload.get('stream'))
    if _debug_enabled:
        _debug_log(
            'chat_completions_request',
            provider=provider,
            model=model,
            stream=stream,
        )
    if stream:
        try:
            result = await run_in_threadpool(svc.forward_direct_chat, provider, model, payload)
            if _debug_enabled:
                _debug_log(
                    'chat_completions_result',
                    provider=provider,
                    model=model,
                    ok=result.ok,
                    status=result.status,
                    has_body=bool(result.body),
                    has_stream=bool(result.stream_chunks),
                    content_length=len(result.body) if result.body else 0,
                    error=result.error,
                )
            if result.ok and result.stream_chunks is not None:
                return StreamingResponse(
                    _iter_chunks(result.stream_chunks),
                    media_type='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'},
                )
            if result.ok and result.body:
                try:
                    parsed_body = json.loads(result.body)
                    if _debug_enabled:
                        _debug_log(
                            'chat_completions_body_preview',
                            provider=provider,
                            model=model,
                            body_preview=str(result.body[:300], 'utf-8', errors='ignore'),
                        )
                    return JSONResponse(content=parsed_body)
                except json.JSONDecodeError as exc:
                    if _debug_enabled:
                        _debug_log(
                            'chat_completions_json_decode_error',
                            provider=provider,
                            model=model,
                            error=str(exc),
                            body_preview=str(result.body[:300], 'utf-8', errors='ignore'),
                        )
                    return JSONResponse({'ok': False, 'error': 'upstream returned invalid JSON'}, status_code=502)
            if result.ok and not result.body and result.content:
                return {'ok': True, 'provider': provider, 'model': model, 'actual_model': result.model or model, 'content': result.content}
            if not result.ok:
                if _debug_enabled:
                    _debug_log(
                        'chat_completions_error',
                        provider=provider,
                        model=model,
                        error=result.error,
                        category=result.category,
                        status=result.status,
                    )
                return JSONResponse({
                    'ok': False,
                    'provider': provider,
                    'model': model,
                    'error': result.error,
                    'category': result.category,
                    'status': result.status,
                    'suggestion': result.suggestion,
                }, status_code=result.status or 400)
        except ProviderError as exc:
            if _debug_enabled:
                _debug_log(
                    'chat_completions_exception',
                    provider=provider,
                    model=model,
                    error=str(exc),
                )
            return JSONResponse({'ok': False, 'provider': provider, 'model': model, 'error': str(exc)}, status_code=400)
        except Exception as exc:
            if _debug_enabled:
                _debug_log(
                    'chat_completions_unexpected_error',
                    provider=provider,
                    model=model,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            return JSONResponse({'ok': False, 'provider': provider, 'model': model, 'error': str(exc)}, status_code=500)
    prompt = _extract_prompt_from_payload(payload)
    result = await run_in_threadpool(svc.chat, provider, model, prompt)
    if result.ok:
        return {'ok': True, 'provider': provider, 'model': model, 'actual_model': result.actual_model or model, 'content': result.content}
    return JSONResponse({
        'ok': False,
        'provider': provider,
        'model': model,
        'error': result.error,
        'category': result.category,
        'status': result.status,
        'suggestion': result.suggestion,
    }, status_code=400)


@app.post('/qoder/v1/chat/completions')
async def qoder_chat_completions(request: Request):
    auth_res = await check_auth_openai(request)
    if isinstance(auth_res, JSONResponse):
        return auth_res
    payload, error_response = await _read_json_payload(request, openai=True)
    if error_response is not None:
        return error_response
    payload = dict(payload)
    raw_model = str(payload.get('model') or 'auto')
    model = raw_model.split('/', 1)[1] if raw_model.startswith('qoder/') else raw_model
    payload['model'] = model or 'auto'
    try:
        adapter = get_service().provider_adapter('qoder')
        result = await run_in_threadpool(adapter.forward_chat, payload)
    except ProviderError as exc:
        return JSONResponse({'error': {'message': str(exc), 'type': 'provider_error', 'param': None, 'code': None}}, status_code=400)
    if result.stream is not None:
        return StreamingResponse(
            _iter_chunks(result.stream),
            media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'},
        )
    if result.body is not None:
        try:
            return JSONResponse(content=json.loads(result.body), status_code=result.status or 200)
        except Exception:
            return JSONResponse(content={'error': {'message': 'qoder returned invalid json'}}, status_code=502)
    return JSONResponse(content={'error': {'message': 'qoder returned empty response'}}, status_code=502)


@app.post('/v1/chat/completions')
async def openai_chat_completions(request: Request):
    request_start = time.time()
    _trace_request(
        'chat_start',
        path=request.url.path,
        client=request.client.host if request.client else 'unknown',
        user_agent=request.headers.get('User-Agent', '')[:120],
    )
    # Need to read body manually for logging before auth
    body_bytes = await request.body()
    _trace_request(
        'body_read',
        elapsed_ms=int((time.time() - request_start) * 1000),
        bytes=len(body_bytes),
    )
    # Mock request.body() so subsequent calls still work
    async def _mock_receive():
        return {"type": "http.request", "body": body_bytes}
    request._receive = _mock_receive
    
    auth_res = await check_auth_openai(request)
    if isinstance(auth_res, JSONResponse):
        _trace_request(
            'auth_failed',
            elapsed_ms=int((time.time() - request_start) * 1000),
            status=auth_res.status_code,
        )
        await _log_debug_request(request, body_bytes, f"Auth Failed: {auth_res.body}")
        return auth_res
    _trace_request('auth_ok', elapsed_ms=int((time.time() - request_start) * 1000))
    
    payload, error_response = await _read_json_payload(request, openai=True)
    if error_response is not None:
        _trace_request(
            'json_failed',
            elapsed_ms=int((time.time() - request_start) * 1000),
            status=error_response.status_code,
        )
        await _log_debug_request(request, body_bytes, f"JSON Parse Failed: {error_response.body}")
        return error_response
    _trace_request(
        'json_ok',
        elapsed_ms=int((time.time() - request_start) * 1000),
        model=payload.get('model') if isinstance(payload, dict) else 'none',
        stream=payload.get('stream') if isinstance(payload, dict) else 'none',
    )
        
    await _log_debug_request(request, body_bytes, "Success /v1/chat/completions")
    messages = payload.get('messages') if isinstance(payload, dict) else None
    _trace_request(
        'payload_summary',
        model=payload.get('model') if isinstance(payload, dict) else 'none',
        stream=payload.get('stream') if isinstance(payload, dict) else 'none',
        message_count=len(messages) if isinstance(messages, list) else 0,
    )
    user_agent = request.headers.get('User-Agent', '')
    client_hint = 'opencode' if 'opencode' in user_agent.lower() else 'openclaw' if 'openclaw' in user_agent.lower() else ''
    try:
        payload = dict(payload)
        payload['client_hint'] = client_hint
        svc = get_service()
        relay = svc.openai_relay()
        req = relay.normalize(payload)
    except ValueError as exc:
        _trace_request(
            'normalize_failed',
            elapsed_ms=int((time.time() - request_start) * 1000),
            error=str(exc),
        )
        error_code = 'model_deprecated' if 'no longer supported' in str(exc) else None
        return JSONResponse(
            {'error': {'message': str(exc), 'type': 'invalid_request_error', 'param': None, 'code': error_code}},
            status_code=400,
        )

    relay_start = time.time()
    result = await asyncio.to_thread(relay.handle_chat, req)
    _trace_request(
        'relay_done',
        relay_ms=int((time.time() - relay_start) * 1000),
        total_ms=int((time.time() - request_start) * 1000),
        status=result.status,
        stream=result.stream_chunks is not None,
        routed_via=result.headers.get('X-Routed-Via') if result.headers else 'none',
        fallback_attempts=result.headers.get('X-Fallback-Attempts') if result.headers else 'none',
    )

    if result.stream_chunks is not None:
        headers = {
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
        if result.headers:
            for k, v in result.headers.items():
                if k.lower() not in ('content-type', 'content-length', 'transfer-encoding', 'connection', 'cache-control'):
                    headers[k] = v
        return StreamingResponse(
            _iter_chunks(result.stream_chunks),
            media_type=result.headers.get('Content-Type', 'text/event-stream'),
            headers=headers,
        )
    if result.body is not None:
        _trace_request(
            'chat_response',
            total_ms=int((time.time() - request_start) * 1000),
            status=result.status,
            bytes=len(result.body),
        )
        return JSONResponse(content=json.loads(result.body), status_code=result.status or 200, headers=dict(result.headers))
        return JSONResponse(content=b'', status_code=result.status or 200)

async def _log_debug_request(request: Request, body: bytes, error_reason: str = ''):
    try:
        svc = get_service()
        headers_str = json.dumps(dict(request.headers), ensure_ascii=False)
        body_str = body.decode('utf-8', errors='replace')
        async def insert_log():
            import psycopg
            try:
                with psycopg.connect(svc.db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO debug_requests (headers, body, error_reason) VALUES (%s, %s, %s)",
                            (headers_str, body_str, error_reason)
                        )
                    conn.commit()
            except Exception as e:
                print(f"Failed to log debug request: {e}")
        import asyncio
        asyncio.create_task(insert_log())
    except Exception:
        pass


@app.post('/v1/completions')
async def openai_legacy_completions(request: Request):
    body_bytes = await request.body()
    async def _mock_receive():
        return {"type": "http.request", "body": body_bytes}
    request._receive = _mock_receive
    
    auth_res = await check_auth_openai(request)
    if isinstance(auth_res, JSONResponse):
        await _log_debug_request(request, body_bytes, f"Auth Failed: {auth_res.body}")
        return auth_res
    
    payload, error_response = await _read_json_payload(request, openai=True)
    if error_response is not None:
        await _log_debug_request(request, body_bytes, f"JSON Parse Failed: {error_response.body}")
        return error_response
    
    await _log_debug_request(request, body_bytes, "Success /v1/completions")
    if error_response is not None:
        return error_response
    _trace_request(
        'legacy_payload_summary',
        model=payload.get('model') if isinstance(payload, dict) else 'none',
        prompt_type=type(payload.get('prompt')).__name__ if isinstance(payload, dict) else 'none',
    )
    
    # Translate legacy format to chat format
    prompt = payload.get('prompt', '')
    if isinstance(prompt, list):
        prompt = ''.join(prompt)
        
    chat_payload = dict(payload)
    chat_payload['messages'] = [{'role': 'user', 'content': prompt}]
    
    user_agent = request.headers.get('User-Agent', '')
    client_hint = 'opencode' if 'opencode' in user_agent.lower() else 'openclaw' if 'openclaw' in user_agent.lower() else ''
    try:
        chat_payload['client_hint'] = client_hint
        svc = get_service()
        relay = svc.openai_relay()
        req = relay.normalize(chat_payload)
    except ValueError as exc:
        error_code = 'model_deprecated' if 'no longer supported' in str(exc) else None
        return JSONResponse(
            {'error': {'message': str(exc), 'type': 'invalid_request_error', 'param': None, 'code': error_code}},
            status_code=400,
        )

    result = await run_in_threadpool(relay.handle_chat, req)

    if result.stream_chunks is not None:
        return StreamingResponse(
            _iter_chunks(result.stream_chunks),
            media_type=result.headers.get('Content-Type', 'text/event-stream'),
            headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive', 'X-Accel-Buffering': 'no'},
        )
    if result.body is not None:
        return JSONResponse(content=json.loads(result.body), status_code=result.status or 200, headers=dict(result.headers))
    return JSONResponse(content=b'', status_code=result.status or 200)
def _iter_chunks(chunks):
    try:
        for chunk in chunks:
            yield chunk
    finally:
        if hasattr(chunks, 'close'):
            chunks.close()


def _extract_prompt_from_payload(payload: dict) -> str:
    from .prompt_utils import extract_prompt
    return extract_prompt(payload)


def to_sqlite_datetime(timestamp: float) -> str:
    import datetime
    dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def get_since_timestamp(range_str: str) -> str:
    import datetime
    import time
    if range_str in ('today', '1d'):
        local_midnight = datetime.datetime.now().astimezone().replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        utc_midnight = local_midnight.astimezone(datetime.timezone.utc)
        return utc_midnight.strftime('%Y-%m-%d %H:%M:%S')
    now = time.time()
    if range_str == '24h':
        delta = 24 * 60 * 60
    elif range_str == '30d':
        delta = 30 * 24 * 60 * 60
    else: # '7d' or default
        delta = 7 * 24 * 60 * 60
    return to_sqlite_datetime(now - delta)

def execute_query(adapter, query, params=()):
    from .db_store import SqliteAdapter
    if not isinstance(adapter, SqliteAdapter):
        query = query.replace('%', '%%').replace('%%s', '%s')
    return adapter.fetchall(query, params)

def fetchone_query(adapter, query, params=()):
    from .db_store import SqliteAdapter
    if not isinstance(adapter, SqliteAdapter):
        query = query.replace('%', '%%').replace('%%s', '%s')
    return adapter.fetchone(query, params)

def _get_provider_display_name(svc, provider_name: str) -> str:
    display_name = provider_name
    if provider_name.startswith('custom-'):
        custom_models = svc.get_custom_models()
        target_cm = next((cm for cm in custom_models if cm['id'] == provider_name), None)
        if target_cm:
            display_name = str(target_cm.get('display_name') or target_cm.get('base_url') or target_cm['id']).strip()
    
    try:
        keys = svc.get_provider_keys(provider_name)
    except KeyError:
        return display_name
    if len(keys) > 1:
        return f"{display_name} (轮询)"
    return display_name

def _aggregate_by_platform(svc, platform_rows) -> list[dict]:
    agg = {}
    for r in platform_rows:
        raw_platform = r[0]
        display = _get_provider_display_name(svc, raw_platform)
        requests = int(r[1] or 0)
        success_rate = float(r[2] or 0)
        avg_latency_ms = float(r[3] or 0)
        input_tokens = int(r[4] or 0)
        output_tokens = int(r[5] or 0)
        
        if display not in agg:
            agg[display] = {
                'platform': display,
                'requests': 0,
                'success_count': 0,
                'total_latency': 0,
                'totalInputTokens': 0,
                'totalOutputTokens': 0
            }
        
        success_count = round((success_rate / 100.0) * requests)
        agg[display]['requests'] += requests
        agg[display]['success_count'] += success_count
        agg[display]['total_latency'] += avg_latency_ms * requests
        agg[display]['totalInputTokens'] += input_tokens
        agg[display]['totalOutputTokens'] += output_tokens
        
    res = []
    for display, data in agg.items():
        reqs = data['requests']
        succ = data['success_count']
        tot_lat = data['total_latency']
        res.append({
            'platform': display,
            'requests': reqs,
            'successRate': round((succ / reqs * 100.0) if reqs > 0 else 0.0, 1),
            'avgLatencyMs': round((tot_lat / reqs) if reqs > 0 else 0),
            'totalInputTokens': data['totalInputTokens'],
            'totalOutputTokens': data['totalOutputTokens']
        })
    res.sort(key=lambda x: x['requests'], reverse=True)
    return res

_analytics_dashboard_cache: dict[str, tuple[float, dict[str, object]]] = {}
ANALYTICS_DASHBOARD_CACHE_TTL = 30


@app.get('/api/analytics/summary', dependencies=[Depends(check_admin_auth)])
async def get_analytics_summary(range: str = '7d'):
    since = get_since_timestamp(range)
    svc = get_service()
    from .db_store import get_adapter
    adapter = get_adapter(svc.db_url)
    try:
        row = fetchone_query(
            adapter,
            """
            SELECT
              COUNT(*) as total_requests,
              SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
              SUM(input_tokens) as total_input_tokens,
              SUM(output_tokens) as total_output_tokens,
              AVG(latency_ms) as avg_latency_ms
            FROM requests
            WHERE created_at >= %s
            """,
            (since,)
        )
        total_requests = int(row[0] or 0)
        success_count = int(row[1] or 0)
        total_input_tokens = int(row[2] or 0)
        total_output_tokens = int(row[3] or 0)
        avg_latency_ms = float(row[4] or 0)

        success_rate = (success_count / total_requests * 100.0) if total_requests > 0 else 0.0
        input_cost = (total_input_tokens / 1000000.0) * 3.0
        output_cost = (total_output_tokens / 1000000.0) * 15.0
        estimated_cost_savings = input_cost + output_cost

        return {
            'totalRequests': total_requests,
            'successRate': round(success_rate, 1),
            'totalInputTokens': total_input_tokens,
            'totalOutputTokens': total_output_tokens,
            'avgLatencyMs': round(avg_latency_ms),
            'estimatedCostSavings': round(estimated_cost_savings, 2),
        }
    finally:
        adapter.close()

@app.get('/api/analytics/by-model', dependencies=[Depends(check_admin_auth)])
async def get_analytics_by_model(range: str = '7d'):
    since = get_since_timestamp(range)
    svc = get_service()
    from .db_store import get_adapter
    adapter = get_adapter(svc.db_url)
    try:
        rows = execute_query(
            adapter,
            """
            SELECT
              platform,
              model_id,
              COUNT(*) as requests,
              SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate,
              AVG(latency_ms) as avg_latency_ms,
              SUM(input_tokens) as total_input_tokens,
              SUM(output_tokens) as total_output_tokens
            FROM requests
            WHERE created_at >= %s
            GROUP BY platform, model_id
            ORDER BY requests DESC
            """,
            (since,)
        )
        model_agg = {}
        for r in rows:
            platform = r[0]
            model_id = r[1]
            requests = int(r[2] or 0)
            success_rate = float(r[3] or 0)
            avg_latency_ms = float(r[4] or 0)
            total_input_tokens = int(r[5] or 0)
            total_output_tokens = int(r[6] or 0)
            
            display_name = model_id
            try:
                from .provider_catalog import get_model_capabilities
                caps = get_model_capabilities(platform, model_id)
                if caps and caps.get('display_name'):
                    display_name = caps.get('display_name')
            except Exception:
                pass
                
            display_platform = _get_provider_display_name(svc, platform)
            key = (display_platform, model_id, display_name)
            if key not in model_agg:
                model_agg[key] = {
                    'requests': 0,
                    'success_count': 0,
                    'total_latency': 0,
                    'totalInputTokens': 0,
                    'totalOutputTokens': 0
                }
            success_count = round((success_rate / 100.0) * requests)
            model_agg[key]['requests'] += requests
            model_agg[key]['success_count'] += success_count
            model_agg[key]['total_latency'] += avg_latency_ms * requests
            model_agg[key]['totalInputTokens'] += total_input_tokens
            model_agg[key]['totalOutputTokens'] += total_output_tokens
            
        res = []
        for key, data in model_agg.items():
            reqs = data['requests']
            succ = data['success_count']
            tot_lat = data['total_latency']
            res.append({
                'platform': key[0],
                'modelId': key[1],
                'displayName': key[2],
                'requests': reqs,
                'successRate': round((succ / reqs * 100.0) if reqs > 0 else 0.0, 1),
                'avgLatencyMs': round((tot_lat / reqs) if reqs > 0 else 0),
                'totalInputTokens': data['totalInputTokens'],
                'totalOutputTokens': data['totalOutputTokens'],
            })
        res.sort(key=lambda x: x['requests'], reverse=True)
        return res
    finally:
        adapter.close()

@app.get('/api/analytics/by-platform', dependencies=[Depends(check_admin_auth)])
async def get_analytics_by_platform(range: str = '7d'):
    since = get_since_timestamp(range)
    svc = get_service()
    from .db_store import get_adapter
    adapter = get_adapter(svc.db_url)
    try:
        rows = execute_query(
            adapter,
            """
            SELECT
              platform,
              COUNT(*) as requests,
              SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate,
              AVG(latency_ms) as avg_latency_ms,
              SUM(input_tokens) as total_input_tokens,
              SUM(output_tokens) as total_output_tokens
            FROM requests
            WHERE created_at >= %s
            GROUP BY platform
            ORDER BY requests DESC
            """,
            (since,)
        )
        return _aggregate_by_platform(svc, rows)
    finally:
        adapter.close()

@app.get('/api/analytics/timeline', dependencies=[Depends(check_admin_auth)])
async def get_analytics_timeline(range: str = '7d', interval: str = 'day'):
    since = get_since_timestamp(range)
    svc = get_service()
    from .db_store import get_adapter, SqliteAdapter
    adapter = get_adapter(svc.db_url)
    try:
        if isinstance(adapter, SqliteAdapter):
            date_format = '%Y-%m-%dT%H:00:00' if interval == 'hour' else '%Y-%m-%d'
            query = f"""
                SELECT
                  strftime('{date_format}', created_at) as timestamp,
                  COUNT(*) as requests,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failure_count
                FROM requests
                WHERE created_at >= %s
                GROUP BY strftime('{date_format}', created_at)
                ORDER BY timestamp ASC
            """
        else:
            date_format = 'YYYY-MM-DD"T"HH24:00:00' if interval == 'hour' else 'YYYY-MM-DD'
            query = f"""
                SELECT
                  to_char(created_at, '{date_format}') as timestamp,
                  COUNT(*) as requests,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failure_count
                FROM requests
                WHERE created_at >= %s
                GROUP BY to_char(created_at, '{date_format}')
                ORDER BY timestamp ASC
            """
        rows = execute_query(adapter, query, (since,))
        return [{
            'timestamp': r[0],
            'requests': int(r[1] or 0),
            'successCount': int(r[2] or 0),
            'failureCount': int(r[3] or 0),
        } for r in rows]
    finally:
        adapter.close()

@app.get('/api/analytics/error-distribution', dependencies=[Depends(check_admin_auth)])
async def get_analytics_error_distribution(range: str = '7d'):
    since = get_since_timestamp(range)
    svc = get_service()
    from .db_store import get_adapter
    adapter = get_adapter(svc.db_url)
    try:
        detailed_rows = execute_query(
            adapter,
            """
            SELECT
              platform,
              model_id,
              CASE
                WHEN error LIKE '%429%' OR error LIKE '%rate limit%' OR error LIKE '%too many%' OR error LIKE '%quota%' THEN 'Rate Limited (429)'
                WHEN error LIKE '%401%' OR error LIKE '%unauthorized%' OR error LIKE '%invalid%key%' THEN 'Auth Error (401)'
                WHEN error LIKE '%403%' OR error LIKE '%forbidden%' THEN 'Forbidden (403)'
                WHEN error LIKE '%404%' OR error LIKE '%not found%' THEN 'Not Found (404)'
                WHEN error LIKE '%timeout%' OR error LIKE '%ETIMEDOUT%' OR error LIKE '%ECONNREFUSED%' THEN 'Timeout/Connection'
                WHEN error LIKE '%500%' OR error LIKE '%internal server%' THEN 'Server Error (500)'
                WHEN error LIKE '%503%' OR error LIKE '%unavailable%' THEN 'Unavailable (503)'
                ELSE 'Other'
              END as error_category,
              COUNT(*) as count
            FROM requests
            WHERE status = 'error' AND created_at >= %s
            GROUP BY platform, model_id, error_category
            ORDER BY count DESC
            """,
            (since,)
        )
        
        by_category_rows = execute_query(
            adapter,
            """
            SELECT
              CASE
                WHEN error LIKE '%429%' OR error LIKE '%rate limit%' OR error LIKE '%too many%' OR error LIKE '%quota%' THEN 'Rate Limited (429)'
                WHEN error LIKE '%401%' OR error LIKE '%unauthorized%' OR error LIKE '%invalid%key%' THEN 'Auth Error (401)'
                WHEN error LIKE '%403%' OR error LIKE '%forbidden%' THEN 'Forbidden (403)'
                WHEN error LIKE '%404%' OR error LIKE '%not found%' THEN 'Not Found (404)'
                WHEN error LIKE '%timeout%' OR error LIKE '%ETIMEDOUT%' OR error LIKE '%ECONNREFUSED%' THEN 'Timeout/Connection'
                WHEN error LIKE '%500%' OR error LIKE '%internal server%' THEN 'Server Error (500)'
                WHEN error LIKE '%503%' OR error LIKE '%unavailable%' THEN 'Unavailable (503)'
                ELSE 'Other'
              END as category,
              COUNT(*) as count
            FROM requests
            WHERE status = 'error' AND created_at >= %s
            GROUP BY category
            ORDER BY count DESC
            """,
            (since,)
        )
        
        by_platform_rows = execute_query(
            adapter,
            """
            SELECT platform, COUNT(*) as count
            FROM requests
            WHERE status = 'error' AND created_at >= %s
            GROUP BY platform
            ORDER BY count DESC
            """,
            (since,)
        )
        
        platform_agg = {}
        for r in by_platform_rows:
            display = _get_provider_display_name(svc, r[0])
            platform_agg[display] = platform_agg.get(display, 0) + (r[1] or 0)
        by_platform_res = [{'platform': k, 'count': v} for k, v in platform_agg.items()]
        by_platform_res.sort(key=lambda x: x['count'], reverse=True)

        detailed_agg = {}
        for r in detailed_rows:
            display = _get_provider_display_name(svc, r[0])
            key = (display, r[1], r[2])
            detailed_agg[key] = detailed_agg.get(key, 0) + (r[3] or 0)
        detailed_res = [{
            'platform': k[0],
            'model_id': k[1],
            'modelId': k[1],
            'error_category': k[2],
            'errorCategory': k[2],
            'count': v
        } for k, v in detailed_agg.items()]
        detailed_res.sort(key=lambda x: x['count'], reverse=True)

        return {
            'byCategory': [{'category': r[0], 'count': r[1]} for r in by_category_rows],
            'byPlatform': by_platform_res,
            'detailed': detailed_res
        }
    finally:
        adapter.close()

@app.get('/api/analytics/errors', dependencies=[Depends(check_admin_auth)])
async def get_analytics_errors(range: str = '7d'):
    since = get_since_timestamp(range)
    svc = get_service()
    from .db_store import get_adapter
    adapter = get_adapter(svc.db_url)
    try:
        rows = execute_query(
            adapter,
            """
            SELECT id, platform, model_id, error, latency_ms, created_at
            FROM requests
            WHERE status = 'error' AND created_at >= %s
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (since,)
        )
        return [{
            'id': r[0],
            'platform': _get_provider_display_name(svc, r[1]),
            'modelId': r[2],
            'model_id': r[2],
            'error': r[3],
            'latencyMs': float(r[4]) if r[4] is not None else 0,
            'createdAt': str(r[5]),
        } for r in rows]
    finally:
        adapter.close()

@app.get('/api/analytics/dashboard', dependencies=[Depends(check_admin_auth)])
async def get_analytics_dashboard(range: str = '7d'):
    cached = _analytics_dashboard_cache.get(range)
    if cached and time.time() - cached[0] < ANALYTICS_DASHBOARD_CACHE_TTL:
        return cached[1]

    since = get_since_timestamp(range)
    svc = get_service()
    from .db_store import get_adapter, SqliteAdapter
    adapter = get_adapter(svc.db_url)
    try:
        summary_row = fetchone_query(
            adapter,
            """
            SELECT
              COUNT(*) as total_requests,
              SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
              SUM(input_tokens) as total_input_tokens,
              SUM(output_tokens) as total_output_tokens,
              AVG(latency_ms) as avg_latency_ms
            FROM requests
            WHERE created_at >= %s
            """,
            (since,)
        )
        total_requests = int(summary_row[0] or 0)
        success_count = int(summary_row[1] or 0)
        total_input_tokens = int(summary_row[2] or 0)
        total_output_tokens = int(summary_row[3] or 0)
        avg_latency_ms = float(summary_row[4] or 0)
        success_rate = (success_count / total_requests * 100.0) if total_requests > 0 else 0.0
        summary = {
            'totalRequests': total_requests,
            'successRate': round(success_rate, 1),
            'totalInputTokens': total_input_tokens,
            'totalOutputTokens': total_output_tokens,
            'avgLatencyMs': round(avg_latency_ms),
            'estimatedCostSavings': round((total_input_tokens / 1000000.0) * 3.0 + (total_output_tokens / 1000000.0) * 15.0, 2),
        }

        platform_rows = execute_query(
            adapter,
            """
            SELECT
              platform,
              COUNT(*) as requests,
              SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate,
              AVG(latency_ms) as avg_latency_ms,
              SUM(input_tokens) as total_input_tokens,
              SUM(output_tokens) as total_output_tokens
            FROM requests
            WHERE created_at >= %s
            GROUP BY platform
            ORDER BY requests DESC
            """,
            (since,)
        )
        by_platform = _aggregate_by_platform(svc, platform_rows)

        if isinstance(adapter, SqliteAdapter):
            date_format = '%Y-%m-%d'
            timeline_query = f"""
                SELECT
                  strftime('{date_format}', created_at) as timestamp,
                  COUNT(*) as requests,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failure_count
                FROM requests
                WHERE created_at >= %s
                GROUP BY strftime('{date_format}', created_at)
                ORDER BY timestamp ASC
            """
        else:
            date_format = 'YYYY-MM-DD'
            timeline_query = f"""
                SELECT
                  to_char(created_at, '{date_format}') as timestamp,
                  COUNT(*) as requests,
                  SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as success_count,
                  SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failure_count
                FROM requests
                WHERE created_at >= %s
                GROUP BY to_char(created_at, '{date_format}')
                ORDER BY timestamp ASC
            """
        timeline_rows = execute_query(adapter, timeline_query, (since,))
        timeline = [{
            'timestamp': r[0],
            'requests': int(r[1] or 0),
            'successCount': int(r[2] or 0),
            'failureCount': int(r[3] or 0),
        } for r in timeline_rows]

        error_by_platform_rows = execute_query(
            adapter,
            """
            SELECT platform, COUNT(*) as count
            FROM requests
            WHERE status = 'error' AND created_at >= %s
            GROUP BY platform
            ORDER BY count DESC
            """,
            (since,)
        )
        error_platform_agg = {}
        for r in error_by_platform_rows:
            display = _get_provider_display_name(svc, r[0])
            error_platform_agg[display] = error_platform_agg.get(display, 0) + (r[1] or 0)
        error_by_platform_res = [{'platform': k, 'count': v} for k, v in error_platform_agg.items()]
        error_by_platform_res.sort(key=lambda x: x['count'], reverse=True)

        error_distribution = {
            'byCategory': [],
            'byPlatform': error_by_platform_res,
            'detailed': [],
        }

        error_rows = execute_query(
            adapter,
            """
            SELECT id, platform, model_id, error, latency_ms, created_at
            FROM requests
            WHERE status = 'error' AND created_at >= %s
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (since,)
        )
        errors = [{
            'id': r[0],
            'platform': _get_provider_display_name(svc, r[1]),
            'modelId': r[2],
            'model_id': r[2],
            'error': r[3],
            'latencyMs': float(r[4]) if r[4] is not None else 0,
            'createdAt': str(r[5]),
        } for r in error_rows]

        model_rows = execute_query(
            adapter,
            """
            SELECT
              platform,
              model_id,
              COUNT(*) as requests,
              SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as success_rate,
              AVG(latency_ms) as avg_latency_ms,
              SUM(input_tokens) as total_input_tokens,
              SUM(output_tokens) as total_output_tokens
            FROM requests
            WHERE created_at >= %s
            GROUP BY platform, model_id
            ORDER BY requests DESC
            """,
            (since,)
        )
        model_agg = {}
        for r in model_rows:
            platform = r[0]
            model_id = r[1]
            requests = int(r[2] or 0)
            success_rate = float(r[3] or 0)
            avg_latency_ms = float(r[4] or 0)
            total_input_tokens = int(r[5] or 0)
            total_output_tokens = int(r[6] or 0)
            
            display_name = model_id
            try:
                from .provider_catalog import get_model_capabilities
                caps = get_model_capabilities(platform, model_id)
                if caps and caps.get('display_name'):
                    display_name = caps.get('display_name')
            except Exception:
                pass
                
            display_platform = _get_provider_display_name(svc, platform)
            key = (display_platform, model_id, display_name)
            if key not in model_agg:
                model_agg[key] = {
                    'requests': 0,
                    'success_count': 0,
                    'total_latency': 0,
                    'totalInputTokens': 0,
                    'totalOutputTokens': 0
                }
            success_count = round((success_rate / 100.0) * requests)
            model_agg[key]['requests'] += requests
            model_agg[key]['success_count'] += success_count
            model_agg[key]['total_latency'] += avg_latency_ms * requests
            model_agg[key]['totalInputTokens'] += total_input_tokens
            model_agg[key]['totalOutputTokens'] += total_output_tokens
            
        by_model = []
        for key, data in model_agg.items():
            reqs = data['requests']
            succ = data['success_count']
            tot_lat = data['total_latency']
            by_model.append({
                'platform': key[0],
                'modelId': key[1],
                'displayName': key[2],
                'requests': reqs,
                'successRate': round((succ / reqs * 100.0) if reqs > 0 else 0.0, 1),
                'avgLatencyMs': round((tot_lat / reqs) if reqs > 0 else 0),
                'totalInputTokens': data['totalInputTokens'],
                'totalOutputTokens': data['totalOutputTokens'],
            })
        by_model.sort(key=lambda x: x['requests'], reverse=True)

        dashboard = {
            'summary': summary,
            'byPlatform': by_platform,
            'timeline': timeline,
            'errorDistribution': error_distribution,
            'errors': errors,
            'byModel': by_model,
        }
        _analytics_dashboard_cache[range] = (time.time(), dashboard)
        return dashboard
    finally:
        adapter.close()
