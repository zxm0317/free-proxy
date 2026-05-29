from __future__ import annotations

import logging
import sqlite3
import urllib.parse
import logging

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
    logger.error(f"psycopg2 import failed: {e}")

try:
    import pg8000.dbapi
    has_pg8000 = True
except ImportError as e:
    has_pg8000 = False
    logger.error(f"pg8000 import failed: {e}")

# Persistent in-memory cache for all keys to ensure 0 database latency on reads.
_local_cache: dict[str, str] = {}
_cache_initialized: bool = False

class DBAdapter:
    def execute(self, query: str, params: tuple) -> None: ...
    def fetchone(self, query: str, params: tuple) -> tuple | None: ...
    def fetchall(self, query: str, params: tuple) -> list[tuple]: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...

class PostgresAdapter(DBAdapter):
    def __init__(self, db_url: str):
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
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            
    def fetchone(self, query: str, params: tuple = ()) -> tuple | None:
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()
            
    def fetchall(self, query: str, params: tuple = ()) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()
            
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

def increment_model_usage(db_url: str, provider: str, model: str) -> None:
    """Increments the usage count for a specific provider and model."""
    adapter = get_adapter(db_url)
    try:
        adapter.execute("""
            INSERT INTO model_usage_stats (provider, model, usage_count)
            VALUES (%s, %s, 1)
            ON CONFLICT (provider, model)
            DO UPDATE SET usage_count = model_usage_stats.usage_count + 1
        """, (provider, model))
        adapter.commit()
    finally:
        adapter.close()

def get_model_usage_stats(db_url: str) -> list[dict[str, object]]:
    """Retrieves usage statistics for all models."""
    adapter = get_adapter(db_url)
    try:
        rows = adapter.fetchall("SELECT provider, model, usage_count FROM model_usage_stats ORDER BY usage_count DESC")
        return [{"provider": row[0], "model": row[1], "usage_count": row[2]} for row in rows]
    finally:
        adapter.close()
