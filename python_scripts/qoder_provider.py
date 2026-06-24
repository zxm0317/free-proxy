from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .provider_adapter import AdapterResponse
from .provider_errors import ProviderError, ProviderHTTPError
from .provider_transport import Transport, UrlLibTransport
from .response_normalizer import sanitize_model_text


def _trace_qoder(event: str, **fields: object) -> None:
    parts = [f'event={event}']
    for key, value in fields.items():
        text = str(value)
        if len(text) > 200:
            text = text[:200] + '...'
        parts.append(f'{key}={text}')
    print('TRACE_QODER ' + ' '.join(parts), flush=True)


QODER_LOGIN_URL = 'https://qoder.com/device/selectAccounts'
QODER_DEVICE_TOKEN_URL = 'https://openapi.qoder.sh/api/v1/deviceToken/poll'
QODER_USERINFO_URL = 'https://openapi.qoder.sh/api/v1/userinfo'
QODER_CHAT_BASE = 'https://api3.qoder.sh'
QODER_MODEL_LIST_URL = f'{QODER_CHAT_BASE}/algo/api/v2/model/list'
QODER_CHAT_URL = f'{QODER_CHAT_BASE}/algo/api/v2/service/pro/sse/agent_chat_generation?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1'

QODER_IDE_VERSION = '1.0.0'
QODER_CLIENT_TYPE = '5'
QODER_DATA_POLICY = 'disagree'
QODER_LOGIN_VERSION = 'v2'
QODER_MACHINE_OS = 'x86_64_windows'
QODER_MACHINE_TYPE = '5'

QODER_STATIC_MODELS = [
    'auto',
    'ultimate',
    'performance',
    'efficient',
    'lite',
    'qmodel',
    'qmodel_latest',
    'dmodel',
    'dfmodel',
    'gm51model',
    'kmodel',
    'mmodel',
]

QODER_PROVIDER_ALIAS = 'qd'
QODER_MODEL_DISPLAY_NAMES = {
    'auto': 'Qoder 自动路由',
    'ultimate': 'Qoder Ultimate',
    'performance': 'Qoder Performance',
    'efficient': 'Qoder Efficient',
    'lite': 'Qoder Lite',
    'qmodel': 'Qwen 3.6 Plus',
    'qmodel_latest': 'Qwen 3.7 Max',
    'dmodel': 'DeepSeek V4 Pro',
    'dfmodel': 'DeepSeek V4 Flash',
    'gm51model': 'GLM 5.1',
    'kmodel': 'Kimi K2.6',
    'mmodel': 'MiniMax M2.7',
}

QODER_RSA_PUBLIC_KEY = b'''-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA8iMH5c02LilrsERw9t6Pv5Nc
4k6Pz1EaDicBMpdpxKduSZu5OANqUq8er4GM95omAGIOPOh+Nx0spthYA2BqGz+l
6HRkPJ7S236FZz73In/KVuLnwI8JJ2CbuJap8kvheCCZpmAWpb/cPx/3Vr/J6I17
XcW+ML9FoCI6AOvOzwIDAQAB
-----END PUBLIC KEY-----'''

_STD_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
_CUSTOM_ALPHABET = '_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!'
_S2C = {ord(src): ord(dst) for src, dst in zip(_STD_ALPHABET, _CUSTOM_ALPHABET)}
_S2C[ord('=')] = ord('$')
_PUBLIC_KEY = serialization.load_pem_public_key(QODER_RSA_PUBLIC_KEY)


def qoder_model_display_name(model_id: str) -> str:
    raw = str(model_id or '')
    display = QODER_MODEL_DISPLAY_NAMES.get(raw)
    return f'{display} ({raw})' if display and display != raw else raw


def qoder_model_key_display(model_id: str) -> str:
    return f'{QODER_PROVIDER_ALIAS}/{model_id}'


def _base64_url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def start_qoder_login() -> dict[str, str]:
    verifier = _base64_url(secrets.token_bytes(32))
    challenge = _base64_url(hashlib.sha256(verifier.encode('utf-8')).digest())
    nonce = str(uuid.uuid4())
    machine_id = str(uuid.uuid4())
    params = urlencode({
        'challenge': challenge,
        'challenge_method': 'S256',
        'machine_id': machine_id,
        'nonce': nonce,
    })
    return {
        'verification_url': f'{QODER_LOGIN_URL}?{params}',
        'code_verifier': verifier,
        'nonce': nonce,
        'machine_id': machine_id,
    }


def poll_qoder_login(*, nonce: str, code_verifier: str, machine_id: str) -> dict[str, Any]:
    if not nonce or not code_verifier:
        raise ProviderError('Qoder login state is incomplete')
    url = f'{QODER_DEVICE_TOKEN_URL}?{urlencode({"nonce": nonce, "verifier": code_verifier, "challenge_method": "S256"})}'
    try:
        response = httpx.get(url, headers={'Accept': 'application/json', 'User-Agent': 'Go-http-client/2.0'}, timeout=15)
    except httpx.RequestError as exc:
        raise ProviderError(f'Qoder login poll failed: {exc}') from exc
    if response.status_code in {202, 404}:
        return {'status': 'pending'}
    if response.status_code >= 400:
        raise ProviderHTTPError(message=f'Qoder login poll HTTP {response.status_code}: {response.text[:200]}', status=response.status_code, category='auth')
    try:
        data = response.json()
    except Exception as exc:
        raise ProviderError(f'Qoder login poll returned invalid JSON: {exc}') from exc
    token = str(data.get('token') or '')
    if not token:
        raise ProviderError('Qoder login poll returned no token')
    user_id = str(data.get('user_id') or '')
    expires_at = _parse_expiry(data.get('expires_at'), data.get('expires_in'))
    user_info = fetch_qoder_user_info(token)
    label = str(user_info.get('email') or user_info.get('name') or user_id or 'Qoder Account')
    return {
        'status': 'ok',
        'account': {
            'id': secrets.token_hex(8),
            'provider': 'qoder',
            'label': label,
            'name': user_info.get('name') or '',
            'email': user_info.get('email') or '',
            'user_id': user_id,
            'machine_id': machine_id,
            'access_token': token,
            'refresh_token': str(data.get('refresh_token') or ''),
            'expires_at': expires_at,
            'status': 'active',
            'metadata': {
                'organization_id': user_info.get('organization_id') or '',
            },
        },
    }


def fetch_qoder_user_info(access_token: str) -> dict[str, str]:
    try:
        response = httpx.get(
            QODER_USERINFO_URL,
            headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json', 'User-Agent': 'Go-http-client/2.0'},
            timeout=15,
        )
        if response.status_code >= 400:
            return {'name': '', 'email': '', 'organization_id': ''}
        data = response.json()
    except Exception:
        return {'name': '', 'email': '', 'organization_id': ''}
    return {
        'name': str(data.get('name') or data.get('username') or '').strip(),
        'email': str(data.get('email') or '').strip(),
        'organization_id': str(data.get('organization_id') or '').strip(),
    }


def _parse_expiry(expires_at: object, expires_in: object) -> int:
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        return int(expires_at)
    if isinstance(expires_at, str) and expires_at.strip():
        raw = expires_at.strip()
        if raw.isdigit():
            return int(raw)
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(raw.replace('Z', '+00:00')).timestamp() * 1000)
        except Exception:
            pass
    if isinstance(expires_in, (int, float)) and expires_in >= 0:
        return int(time.time() * 1000 + float(expires_in) * 1000)
    return int(time.time() * 1000 + 30 * 24 * 60 * 60 * 1000)


def qoder_encode_body(plaintext: bytes | str) -> bytes:
    raw = plaintext.encode('utf-8') if isinstance(plaintext, str) else plaintext
    std = base64.b64encode(raw).decode('ascii')
    n = len(std)
    a = n // 3
    rearranged = std[n - a:] + std[a:n - a] + std[:a]
    return bytes(_S2C.get(ord(ch), ord(ch)) for ch in rearranged)


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad = block_size - (len(data) % block_size)
    return data + bytes([pad]) * pad


def _aes_encrypt_base64(plaintext: str, key_str: str) -> str:
    key = key_str.encode('utf-8')
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(_pkcs7_pad(plaintext.encode('utf-8'))) + encryptor.finalize()
    return base64.b64encode(encrypted).decode('ascii')


def _rsa_encrypt_base64(data: str) -> str:
    encrypted = _PUBLIC_KEY.encrypt(data.encode('utf-8'), padding.PKCS1v15())
    return base64.b64encode(encrypted).decode('ascii')


def _compute_sig_path(request_url: str) -> str:
    path = urlparse(request_url).path or ''
    return path[len('/algo'):] if path.startswith('/algo') else path


def build_cosy_headers(body: bytes, request_url: str, account: dict[str, Any]) -> dict[str, str]:
    user_id = str(account.get('user_id') or '')
    auth_token = str(account.get('access_token') or '')
    if not user_id:
        raise ProviderError('Qoder account is missing user_id')
    if not auth_token:
        raise ProviderError('Qoder account is missing access_token')

    aes_key = str(uuid.uuid4())[:16]
    info = _aes_encrypt_base64(json.dumps({
        'uid': user_id,
        'security_oauth_token': auth_token,
        'name': account.get('name') or '',
        'aid': '',
        'email': account.get('email') or '',
    }, ensure_ascii=False, separators=(',', ':')), aes_key)
    cosy_key = _rsa_encrypt_base64(aes_key)
    timestamp = str(int(time.time()))
    request_id = str(uuid.uuid4())
    payload = {
        'version': 'v1',
        'requestId': request_id,
        'info': info,
        'cosyVersion': QODER_IDE_VERSION,
        'ideVersion': '',
    }
    payload_b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).decode('ascii')
    sig_path = _compute_sig_path(request_url)
    sig_input = b'\n'.join([
        payload_b64.encode('latin1'),
        cosy_key.encode('latin1'),
        timestamp.encode('latin1'),
        body,
        sig_path.encode('latin1'),
    ])
    sig = hashlib.md5(sig_input).hexdigest()
    machine_id = str(account.get('machine_id') or uuid.uuid4())
    return {
        'Authorization': f'Bearer COSY.{payload_b64}.{sig}',
        'Cosy-Key': cosy_key,
        'Cosy-User': user_id,
        'Cosy-Date': timestamp,
        'Cosy-Version': QODER_IDE_VERSION,
        'Cosy-Machineid': machine_id,
        'Cosy-Machinetoken': machine_id,
        'Cosy-Machinetype': QODER_MACHINE_TYPE,
        'Cosy-Machineos': QODER_MACHINE_OS,
        'Cosy-Clienttype': QODER_CLIENT_TYPE,
        'Cosy-Clientip': '127.0.0.1',
        'Cosy-Bodyhash': hashlib.md5(body).hexdigest(),
        'Cosy-Bodylength': str(len(body)),
        'Cosy-Sigpath': sig_path,
        'Cosy-Data-Policy': QODER_DATA_POLICY,
        'Cosy-Organization-Id': '',
        'Cosy-Organization-Tags': '',
        'Login-Version': QODER_LOGIN_VERSION,
        'X-Request-Id': str(uuid.uuid4()),
    }


def _extract_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get('text')
                if isinstance(text, str):
                    parts.append(text)
        return '\n'.join(parts)
    if content is None:
        return ''
    return str(content)


def _normalize_messages(messages: object) -> tuple[list[dict[str, str]], str]:
    if not isinstance(messages, list):
        return [], ''
    out: list[dict[str, str]] = []
    system_parts: list[str] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get('role') or 'user')
        text = _extract_text(item.get('content'))
        if role == 'system':
            if text:
                system_parts.append(text)
            continue
        out.append({'role': role, 'content': text})
    return out, '\n\n'.join(system_parts)


def _last_user_text(messages: list[dict[str, str]]) -> str:
    for item in reversed(messages):
        if item.get('role') == 'user':
            return str(item.get('content') or '')
    return ''


def _stable_hash(prefix: str, *parts: object) -> str:
    h = hashlib.sha256()
    h.update(prefix.encode('utf-8'))
    for part in parts:
        h.update(b'\0')
        h.update(str(part or '').encode('utf-8'))
    return h.hexdigest()[:16]


class QoderCatalogCache:
    def __init__(self) -> None:
        self._items: dict[str, tuple[float, list[str], dict[str, dict[str, Any]]]] = {}

    def get(self, account: dict[str, Any]) -> tuple[list[str], dict[str, dict[str, Any]]] | None:
        key = str(account.get('user_id') or account.get('id') or '')
        item = self._items.get(key)
        if not item:
            return None
        expires_at, models, raw = item
        if expires_at < time.time():
            self._items.pop(key, None)
            return None
        return list(models), dict(raw)

    def set(self, account: dict[str, Any], models: list[str], raw: dict[str, dict[str, Any]]) -> None:
        key = str(account.get('user_id') or account.get('id') or '')
        self._items[key] = (time.time() + 3600, list(models), dict(raw))


_CATALOG_CACHE = QoderCatalogCache()


@dataclass
class QoderProviderAdapter:
    account: dict[str, Any]
    transport: Transport | None = None
    request_timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if self.transport is None:
            self.transport = UrlLibTransport()

    def list_models(self) -> list[str]:
        cached = _CATALOG_CACHE.get(self.account)
        if cached:
            return cached[0]
        headers = {
            'Accept': 'application/json',
            'Accept-Encoding': 'identity',
            **build_cosy_headers(b'', QODER_MODEL_LIST_URL, self.account),
        }
        try:
            status, _, body = self.transport.request('GET', QODER_MODEL_LIST_URL, headers, None, timeout=15)
        except ProviderError:
            return list(QODER_STATIC_MODELS)
        if status >= 400:
            return list(QODER_STATIC_MODELS)
        try:
            data = json.loads(body.decode('utf-8', errors='replace'))
        except Exception:
            return list(QODER_STATIC_MODELS)
        chat = data.get('chat') if isinstance(data, dict) else None
        if not isinstance(chat, list):
            return list(QODER_STATIC_MODELS)
        models: list[str] = []
        raw: dict[str, dict[str, Any]] = {}
        for entry in chat:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get('key') or '').strip()
            if not key:
                continue
            raw[key] = dict(entry)
            if entry.get('enable') is False:
                continue
            models.append(key)
        if not models:
            models = list(QODER_STATIC_MODELS)
        _CATALOG_CACHE.set(self.account, models, raw)
        return models

    def _model_config(self, model_id: str) -> dict[str, Any]:
        cached = _CATALOG_CACHE.get(self.account)
        raw = cached[1] if cached else {}
        if model_id not in raw:
            self.list_models()
            cached = _CATALOG_CACHE.get(self.account)
            raw = cached[1] if cached else {}
        config = raw.get(model_id)
        if config:
            result = dict(config)
            result['key'] = model_id
            return result
        return {'key': model_id, 'display_name': model_id, 'is_reasoning': False, 'source': 'system'}

    def _build_payload(self, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        model = str(payload.get('model') or 'auto').replace('qoder/', '')
        messages, system_text = _normalize_messages(payload.get('messages') or [])
        last_user = _last_user_text(messages)
        tools = payload.get('tools') if isinstance(payload.get('tools'), list) else []
        model_config = self._model_config(model)
        max_tokens = int(model_config.get('max_output_tokens') or 32768)
        for key in ('max_tokens', 'max_completion_tokens'):
            value = payload.get(key)
            if isinstance(value, int) and value > 0:
                max_tokens = min(max_tokens, value)
        record_id = _stable_hash('qoder-record', model, json.dumps(messages, ensure_ascii=False), max_tokens)
        session_id = _stable_hash('qoder-session', self.account.get('user_id'), model)
        body = {
            'request_id': str(uuid.uuid4()),
            'request_set_id': record_id,
            'chat_record_id': record_id,
            'session_id': session_id,
            'stream': True,
            'chat_task': 'FREE_INPUT',
            'is_reply': True,
            'is_retry': False,
            'source': 1,
            'version': '3',
            'session_type': 'qodercli',
            'agent_id': 'agent_common',
            'task_id': 'common',
            'code_language': '',
            'chat_prompt': '',
            'image_urls': None,
            'aliyun_user_type': '',
            'system': system_text,
            'messages': messages,
            'tools': tools,
            'parameters': {'max_tokens': max_tokens},
            'chat_context': {
                'chatPrompt': '',
                'imageUrls': None,
                'extra': {
                    'context': [],
                    'modelConfig': {'key': model, 'is_reasoning': bool(model_config.get('is_reasoning'))},
                    'originalContent': last_user,
                },
                'features': [],
                'text': last_user,
            },
            'model_config': model_config,
            'business': {
                'product': 'cli',
                'version': '1.0.0',
                'type': 'agent',
                'stage': 'start',
                'id': str(uuid.uuid4()),
                'name': last_user[:30],
                'begin_at': int(time.time() * 1000),
            },
        }
        return model, body

    def _upstream_stream(self, payload: dict[str, Any]) -> tuple[str, int, dict[str, str], Iterable[bytes]]:
        started_at = time.time()
        model, qoder_payload = self._build_payload(payload)
        build_ms = int((time.time() - started_at) * 1000)
        plain = json.dumps(qoder_payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        encoded = qoder_encode_body(plain)
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'X-Model-Key': model,
            'X-Model-Source': str(qoder_payload.get('model_config', {}).get('source') or 'system'),
            'Accept-Encoding': 'identity',
            **build_cosy_headers(encoded, QODER_CHAT_URL, self.account),
        }
        request_started_at = time.time()
        _trace_qoder(
            'upstream_start',
            model=model,
            payload_build_ms=build_ms,
            timeout_s=self.request_timeout_seconds,
            bytes=len(encoded),
        )
        status, response_headers, stream = self.transport.stream_request(
            'POST',
            QODER_CHAT_URL,
            headers,
            encoded,
            timeout=self.request_timeout_seconds,
        )
        _trace_qoder(
            'upstream_headers',
            model=model,
            elapsed_ms=int((time.time() - request_started_at) * 1000),
            total_ms=int((time.time() - started_at) * 1000),
            status=status,
        )
        return model, status, response_headers, stream

    def _wrap_stream(self, chunks: Iterable[bytes], model: str) -> Iterable[bytes]:
        done = False
        buffer = ''
        for chunk in chunks:
            buffer += chunk.decode('utf-8', errors='replace')
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                out = self._process_qoder_sse_line(line, model)
                if out:
                    yield out
                    if b'data: [DONE]' in out:
                        done = True
                        return
        if buffer and not done:
            out = self._process_qoder_sse_line(buffer, model)
            if out:
                yield out
        if not done:
            yield b'data: [DONE]\n\n'

    @staticmethod
    def _process_qoder_sse_line(line: str, model: str) -> bytes | None:
        line = line.strip()
        if not line or not line.startswith('data:'):
            return None
        data = line[5:].strip()
        if data == '[DONE]':
            return b'data: [DONE]\n\n'
        try:
            envelope = json.loads(data)
        except Exception:
            return None
        status = envelope.get('statusCodeValue', 200) if isinstance(envelope, dict) else 200
        inner = envelope.get('body') if isinstance(envelope, dict) else ''
        if status != 200:
            chunk = {
                'id': f'qoder-error-{int(time.time())}',
                'object': 'chat.completion.chunk',
                'created': int(time.time()),
                'model': f'qoder/{model}',
                'choices': [{'index': 0, 'delta': {'content': f'\n[qoder error {status}: {str(inner)[:200]}]'}, 'finish_reason': 'stop'}],
            }
            return f'data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n'.encode('utf-8')
        if not inner:
            return None
        if inner == '[DONE]':
            return b'data: [DONE]\n\n'
        return f'data: {str(inner).replace(chr(13), "").replace(chr(10), "")}\n\n'.encode('utf-8')

    @staticmethod
    def _collect_stream_text(chunks: Iterable[bytes]) -> str:
        parts: list[str] = []
        for chunk in chunks:
            for raw_line in chunk.decode('utf-8', errors='replace').splitlines():
                line = raw_line.strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if not data or data == '[DONE]':
                    continue
                try:
                    payload = json.loads(data)
                except Exception:
                    continue
                choices = payload.get('choices') if isinstance(payload, dict) else None
                if not isinstance(choices, list) or not choices:
                    continue
                first = choices[0]
                if not isinstance(first, dict):
                    continue
                delta = first.get('delta')
                if isinstance(delta, dict) and isinstance(delta.get('content'), str):
                    parts.append(delta['content'])
                message = first.get('message')
                if isinstance(message, dict) and isinstance(message.get('content'), str):
                    parts.append(message['content'])
        return sanitize_model_text(''.join(parts).strip())

    def chat_text(self, model_id: str, prompt: str, max_tokens: int = 256, timeout: int | None = None) -> str:
        payload: dict[str, Any] = {
            'model': model_id,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens,
            'stream': True,
        }
        model, status, _, upstream = self._upstream_stream(payload)
        wrapped = self._wrap_stream(upstream, model)
        if status >= 400:
            raise ProviderHTTPError(message=f'Qoder HTTP {status}', status=status, category='unknown')
        text = self._collect_stream_text(wrapped)
        if not text:
            raise ProviderError('Qoder returned empty content')
        return text

    def forward_chat(self, payload: dict[str, Any]) -> AdapterResponse:
        started_at = time.time()
        model, status, headers, upstream = self._upstream_stream(payload)
        wrapped = self._wrap_stream(upstream, model)
        if bool(payload.get('stream')):
            _trace_qoder(
                'forward_stream',
                model=model,
                elapsed_ms=int((time.time() - started_at) * 1000),
                status=status,
            )
            return AdapterResponse(status, {'Content-Type': 'text/event-stream', **headers}, None, wrapped, 'text/event-stream')
        collect_started_at = time.time()
        content = self._collect_stream_text(wrapped)
        _trace_qoder(
            'collect_done',
            model=model,
            collect_ms=int((time.time() - collect_started_at) * 1000),
            total_ms=int((time.time() - started_at) * 1000),
            status=status,
            chars=len(content),
        )
        body = {
            'id': f'chatcmpl-qoder-{uuid.uuid4().hex[:12]}',
            'object': 'chat.completion',
            'created': int(time.time()),
            'model': f'qoder/{model}',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
        }
        return AdapterResponse(status, {'Content-Type': 'application/json; charset=utf-8', **headers}, json.dumps(body, ensure_ascii=False).encode('utf-8'), None, 'application/json; charset=utf-8')
