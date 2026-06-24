from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any

from .db_store import get_key, upsert_key


AccountRecord = dict[str, Any]


def _accounts_key(provider: str) -> str:
    return f'account_provider_accounts_{provider}'


def _record_id(account: AccountRecord) -> str:
    explicit = str(account.get('id') or '').strip()
    if explicit:
        return explicit
    for field in ('user_id', 'email', 'label', 'name'):
        value = str(account.get(field) or '').strip()
        if value:
            return f'{field}:{value}'
    token = str(account.get('access_token') or account.get('refresh_token') or '').strip()
    if token:
        return f'token:{hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]}'
    return 'unknown'


def load_accounts(db_url: str, provider: str) -> list[AccountRecord]:
    raw = get_key(db_url, _accounts_key(provider))
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [dict(item) for item in data if isinstance(item, dict)]


def save_accounts(db_url: str, provider: str, accounts: list[AccountRecord]) -> None:
    upsert_key(db_url, _accounts_key(provider), json.dumps(accounts, ensure_ascii=False))


def upsert_account(db_url: str, provider: str, account: AccountRecord) -> AccountRecord:
    accounts = load_accounts(db_url, provider)
    account_id = str(account.get('id') or '').strip() or secrets.token_hex(8)
    account = dict(account)
    account['id'] = account_id
    account['provider'] = provider
    account.setdefault('status', 'active')
    account.setdefault('created_at', int(time.time()))
    account['updated_at'] = int(time.time())

    replaced = False
    for index, current in enumerate(accounts):
        if str(current.get('id')) == account_id:
            accounts[index] = account
            replaced = True
            break
    if not replaced:
        accounts.append(account)
    save_accounts(db_url, provider, accounts)
    return account


def delete_account(db_url: str, provider: str, account_id: str) -> bool:
    accounts = load_accounts(db_url, provider)
    filtered = [item for item in accounts if _record_id(item) != account_id]
    save_accounts(db_url, provider, filtered)
    return len(filtered) != len(accounts)


def mask_token(value: str) -> str:
    if len(value) <= 10:
        return '***'
    return f'{value[:5]}***{value[-4:]}'


def public_account(account: AccountRecord) -> AccountRecord:
    public = {
        'id': _record_id(account),
        'provider': account.get('provider'),
        'label': account.get('label') or account.get('email') or account.get('name') or account.get('id'),
        'name': account.get('name') or '',
        'email': account.get('email') or '',
        'user_id': account.get('user_id') or '',
        'status': account.get('status') or 'active',
        'created_at': account.get('created_at'),
        'updated_at': account.get('updated_at'),
        'last_used_at': account.get('last_used_at'),
        'expires_at': account.get('expires_at'),
        'token': mask_token(str(account.get('access_token') or '')),
    }
    metadata = account.get('metadata')
    if isinstance(metadata, dict):
        public['metadata'] = {k: v for k, v in metadata.items() if k not in {'access_token', 'refresh_token'}}
    return public
