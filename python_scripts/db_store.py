from __future__ import annotations

import logging
import sqlite3
import urllib.parse
import time

logger = logging.getLogger(__name__)

try:
    import psycopg
    has_psycopg3 = True
except ImportError as e:
    has_psycopg3 = False
    logger.error(f"psycopg3 import failed: {e}")

try:
    import psycopg2
    has_psycopg2 = True
except ImportError as e:
    has_psycopg2 = False
    logger.debug(f"psycopg2 import failed: {e}")

try:
    import pg8000.dbapi
    has_pg8000 = True
except ImportError as e:
    has_pg8000 = False
    logger.debug(f"pg8000 import failed: {e}")

# Persistent in-memory cache for all keys to ensure 0 database latency on reads.
_local_cache: dict[str, str] = {}
_cache_initialized: bool = False

_usage_stats_cache: list[dict[str, object]] | None = None
_usage_stats_cache_ts: float = 0

class DBAdapter:
    def execute(self, query: str, params: tuple) -> None: ...
    def fetchone(self, query: str, params: tuple) -> tuple | None: ...
    def fetchall(self, query: str, params: tuple) -> list[tuple]: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...

class PostgresAdapter(DBAdapter):
    def __init__(self, db_url: str):
        if 'supabase.com' in db_url and 'sslmode' not in db_url:
            db_url += ('&' if '?' in db_url else '?') + 'sslmode=require'
            
        self.is_psycopg2 = False
        if has_psycopg3:
            self.conn = psycopg.connect(db_url)
        elif has_psycopg2:
            self.is_psycopg2 = True
            self.conn = psycopg2.connect(db_url)
        elif has_pg8000:
            self.is_psycopg2 = True # pg8000 also uses %s safely for dbapi
            parsed = urllib.parse.urlparse(db_url)
            self.conn = pg8000.dbapi.connect(
                user=urllib.parse.unquote(parsed.username) if parsed.username else None,
                password=urllib.parse.unquote(parsed.password) if parsed.password else None,
                host=parsed.hostname,
                port=parsed.port or 5432,
                database=parsed.path.lstrip('/')
            )
        else:
            raise ImportError("No Postgres driver (psycopg, psycopg2, or pg8000) is installed. Cannot connect to Postgres.")

        
    def execute(self, query: str, params: tuple = ()) -> None:
        if self.is_psycopg2:
            query = query.replace("%s", "%s") # psycopg2 uses %s just like psycopg3
        cur = self.conn.cursor()
        try:
            cur.execute(query, params)
        finally:
            cur.close()
            
    def fetchone(self, query: str, params: tuple = ()) -> tuple | None:
        cur = self.conn.cursor()
        try:
            cur.execute(query, params)
            return cur.fetchone()
        finally:
            cur.close()
            
    def fetchall(self, query: str, params: tuple = ()) -> list[tuple]:
        cur = self.conn.cursor()
        try:
            cur.execute(query, params)
            return cur.fetchall()
        finally:
            cur.close()
            
    def commit(self) -> None:
        self.conn.commit()
        
    def close(self) -> None:
        self.conn.close()

class SqliteAdapter(DBAdapter):
    def __init__(self, db_url: str):
        path = db_url.replace('sqlite:///', '')
        self.conn = sqlite3.connect(path)
        
    def _convert_query(self, query: str) -> str:
        # Just replace %s with ? and EXCLUDED with excluded for SQLite compatibility if needed
        return query.replace('%s', '?').replace('EXCLUDED.', 'excluded.')
        
    def execute(self, query: str, params: tuple = ()) -> None:
        self.conn.execute(self._convert_query(query), params)
            
    def fetchone(self, query: str, params: tuple = ()) -> tuple | None:
        cur = self.conn.execute(self._convert_query(query), params)
        return cur.fetchone()
            
    def fetchall(self, query: str, params: tuple = ()) -> list[tuple]:
        cur = self.conn.execute(self._convert_query(query), params)
        return cur.fetchall()
            
    def commit(self) -> None:
        self.conn.commit()
        
    def close(self) -> None:
        self.conn.close()

def get_adapter(db_url: str) -> DBAdapter:
    if db_url.startswith('sqlite:///'):
        return SqliteAdapter(db_url)
    return PostgresAdapter(db_url)

def init_db(db_url: str) -> None:
    """Initializes the database schema and loads data into local cache."""
    global _local_cache, _cache_initialized
    adapter = get_adapter(db_url)
    try:
        adapter.execute("""
            CREATE TABLE IF NOT EXISTS config_keys (
                key_name VARCHAR(255) PRIMARY KEY,
                key_value TEXT NOT NULL
            )
        """)
        adapter.execute("""
            CREATE TABLE IF NOT EXISTS model_usage_stats (
                provider VARCHAR(255) NOT NULL,
                model VARCHAR(255) NOT NULL,
                usage_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (provider, model)
            )
        """)
        if db_url.startswith('sqlite:///'):
            adapter.execute("""
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform VARCHAR(255) NOT NULL,
                    model_id VARCHAR(255) NOT NULL,
                    key_id INTEGER,
                    status VARCHAR(255) NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        else:
            adapter.execute("""
                CREATE TABLE IF NOT EXISTS requests (
                    id SERIAL PRIMARY KEY,
                    platform VARCHAR(255) NOT NULL,
                    model_id VARCHAR(255) NOT NULL,
                    key_id INTEGER,
                    status VARCHAR(255) NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        adapter.execute("CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at)")
        adapter.execute("CREATE INDEX IF NOT EXISTS idx_requests_platform ON requests(platform)")
        adapter.execute("CREATE INDEX IF NOT EXISTS idx_requests_status_created_at ON requests(status, created_at)")
        adapter.execute("CREATE INDEX IF NOT EXISTS idx_requests_platform_created_at ON requests(platform, created_at)")
        adapter.execute("CREATE INDEX IF NOT EXISTS idx_requests_model_created_at ON requests(model_id, created_at)")
        adapter.execute("CREATE INDEX IF NOT EXISTS idx_requests_platform_model_created_at ON requests(platform, model_id, created_at)")
        adapter.execute("""
            CREATE TABLE IF NOT EXISTS model_probe_results (
                model_key VARCHAR(512) PRIMARY KEY,
                ok BOOLEAN NOT NULL,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                status INTEGER,
                error TEXT,
                checked_at INTEGER NOT NULL
            )
        """)
        adapter.commit()
        
        rows = adapter.fetchall("SELECT key_name, key_value FROM config_keys")
        _local_cache = {row[0]: row[1] for row in rows}
        _cache_initialized = True
    finally:
        adapter.close()

def upsert_key(db_url: str, key_name: str, value: str) -> None:
    """Inserts or updates a key-value pair in the database and local cache."""
    adapter = get_adapter(db_url)
    try:
        adapter.execute("""
            INSERT INTO config_keys (key_name, key_value)
            VALUES (%s, %s)
            ON CONFLICT (key_name)
            DO UPDATE SET key_value = EXCLUDED.key_value
        """, (key_name, value))
        adapter.commit()
    finally:
        adapter.close()
    
    _local_cache[key_name] = value

def delete_key(db_url: str, key_name: str) -> None:
    """Deletes a key from the database and local cache."""
    adapter = get_adapter(db_url)
    try:
        adapter.execute("DELETE FROM config_keys WHERE key_name = %s", (key_name,))
        adapter.commit()
    finally:
        adapter.close()
    if key_name in _local_cache:
        del _local_cache[key_name]

def get_key(db_url: str, key_name: str) -> str | None:
    """Retrieves a single key from the local cache or database."""
    if _cache_initialized:
        return _local_cache.get(key_name)
        
    adapter = get_adapter(db_url)
    try:
        row = adapter.fetchone("SELECT key_value FROM config_keys WHERE key_name = %s", (key_name,))
        if row:
            return row[0]
        return None
    finally:
        adapter.close()

def get_all_keys(db_url: str) -> dict[str, str]:
    """Retrieves all keys from the local cache or database."""
    if _cache_initialized:
        return _local_cache.copy()
        
    adapter = get_adapter(db_url)
    try:
        rows = adapter.fetchall("SELECT key_name, key_value FROM config_keys")
        return {row[0]: row[1] for row in rows}
    finally:
        adapter.close()

import json
import os

USAGE_STATS_PATH = '/tmp/model_usage_stats.json'

def _load_local_usage_stats() -> dict[str, int]:
    try:
        if os.path.exists(USAGE_STATS_PATH):
            with open(USAGE_STATS_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_local_usage_stats(data: dict[str, int]):
    try:
        with open(USAGE_STATS_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception:
        pass

def increment_model_usage(db_url: str, provider: str, model: str) -> None:
    """Increments the usage count for a specific provider and model using local file."""
    data = _load_local_usage_stats()
    key = f"{provider}/{model}"
    data[key] = data.get(key, 0) + 1
    _save_local_usage_stats(data)

def get_model_usage_stats(db_url: str) -> list[dict[str, object]]:
    """Retrieves usage statistics for all models from local file."""
    data = _load_local_usage_stats()
    stats = []
    for k, v in data.items():
        if '/' in k:
            p, m = k.split('/', 1)
            stats.append({"provider": p, "model": m, "usage_count": v})
    stats.sort(key=lambda x: x["usage_count"], reverse=True)
    return stats

MODEL_PROBE_RESULTS_KEY = 'model_probe_results'

def get_model_probe_results(db_url: str) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    raw = get_key(db_url, MODEL_PROBE_RESULTS_KEY)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                results.update({str(k): v for k, v in data.items() if isinstance(v, dict)})
        except Exception:
            pass

    adapter = get_adapter(db_url)
    try:
        rows = adapter.fetchall(
            "SELECT model_key, ok, latency_ms, status, error, checked_at FROM model_probe_results",
            (),
        )
    except Exception:
        return results
    finally:
        adapter.close()

    for model_key, ok, latency_ms, status, error, checked_at in rows:
        results[str(model_key)] = {
            'ok': bool(ok),
            'latency_ms': int(latency_ms or 0),
            'status': status,
            'error': str(error or ''),
            'checked_at': int(checked_at or 0),
        }
    return results

def save_model_probe_result(
    db_url: str,
    model_key: str,
    *,
    ok: bool,
    latency_ms: int,
    status: int | None = None,
    error: str = '',
) -> None:
    adapter = get_adapter(db_url)
    try:
        adapter.execute(
            """
            INSERT INTO model_probe_results (model_key, ok, latency_ms, status, error, checked_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (model_key)
            DO UPDATE SET
                ok = EXCLUDED.ok,
                latency_ms = EXCLUDED.latency_ms,
                status = EXCLUDED.status,
                error = EXCLUDED.error,
                checked_at = EXCLUDED.checked_at
            """,
            (
                model_key,
                bool(ok),
                max(0, int(latency_ms)),
                status,
                str(error or '')[:500],
                int(time.time()),
            ),
        )
        adapter.commit()
    finally:
        adapter.close()

def delete_model_probe_results_for_provider(db_url: str, provider_id: str) -> None:
    raw = get_key(db_url, MODEL_PROBE_RESULTS_KEY)
    if raw:
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        if isinstance(data, dict):
            filtered = {str(key): value for key, value in data.items() if not str(key).startswith(f'{provider_id}/')}
            if len(filtered) != len(data):
                upsert_key(db_url, MODEL_PROBE_RESULTS_KEY, json.dumps(filtered))
    prefix = f'{provider_id}/%'
    adapter = get_adapter(db_url)
    try:
        adapter.execute("DELETE FROM model_probe_results WHERE model_key LIKE %s", (prefix,))
        adapter.commit()
    finally:
        adapter.close()

_manual_order_cache: list[str] | None = None
_manual_order_cache_ts: float = 0

def save_manual_order(db_url: str, order: list[str]) -> None:
    """Saves the manual model order to the database."""
    global _manual_order_cache, _manual_order_cache_ts
    adapter = get_adapter(db_url)
    try:
        import json
        adapter.execute("""
            INSERT INTO config_keys (key_name, key_value)
            VALUES (%s, %s)
            ON CONFLICT (key_name)
            DO UPDATE SET key_value = EXCLUDED.key_value
        """, ('manual_model_order', json.dumps(order)))
        adapter.commit()
    finally:
        adapter.close()
    
    _manual_order_cache = order
    _manual_order_cache_ts = time.time()

def get_manual_order(db_url: str, bypass_cache: bool = False) -> list[str]:
    """Retrieves the manual model order from the database."""
    global _manual_order_cache, _manual_order_cache_ts
    if not bypass_cache and _manual_order_cache is not None and time.time() - _manual_order_cache_ts < 10:
        return _manual_order_cache

    adapter = get_adapter(db_url)
    try:
        row = adapter.fetchone("SELECT key_value FROM config_keys WHERE key_name = %s", ('manual_model_order',))
        if row:
            import json
            data = json.loads(row[0])
            order = [str(x) for x in data] if isinstance(data, list) else []
        else:
            order = []
            
        _manual_order_cache = order
        _manual_order_cache_ts = time.time()
        return order
    except Exception:
        return []
    finally:
        adapter.close()

def log_request(
    db_url: str,
    platform: str,
    model_id: str,
    status: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    error: str | None = None,
    ttfb_ms: int | None = None
) -> None:
    """Logs a request attempt to the database for analytics."""
    adapter = get_adapter(db_url)
    try:
        adapter.execute(
            """
            INSERT INTO requests (platform, model_id, key_id, status, input_tokens, output_tokens, latency_ms, error)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (platform, model_id, 1, status, input_tokens, output_tokens, latency_ms, error)
        )
        adapter.commit()
    except Exception as e:
        print(f"Failed to log request: {e}")
    finally:
        adapter.close()
