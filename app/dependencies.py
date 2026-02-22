from __future__ import annotations

from typing import Generator, Optional

import psycopg

from app.database import get_conn
from app.config import Settings, ENV_FILE

# Redis is optional. If REDIS_URL is not set, cache is disabled.
try:
    import redis.asyncio as redis_async
except Exception:  # pragma: no cover
    redis_async = None  # type: ignore


def db_conn() -> Generator[psycopg.Connection, None, None]:
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


_redis_singleton = None


async def redis_client():
    """
    Optional Redis client dependency.
    Returns a connected redis client if REDIS_URL is provided, else None.
    """
    global _redis_singleton

    # If redis library isn't available, disable gracefully.
    if redis_async is None:
        return None

    s = Settings(_env_file=str(ENV_FILE))
    if not s.redis_url:
        return None

    if _redis_singleton is None:
        _redis_singleton = redis_async.Redis.from_url(
            s.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    return _redis_singleton
