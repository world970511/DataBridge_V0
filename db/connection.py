"""PostgreSQL 연결 관리."""

import logging
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from config.settings import get_settings

logger = logging.getLogger(__name__)

_pool: pool.SimpleConnectionPool | None = None


def get_pool() -> pool.SimpleConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        settings = get_settings()
        _pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=settings.db.url,
        )
        logger.info("PostgreSQL connection pool created")
    return _pool


@contextmanager
def get_connection():
    """커넥션 풀에서 연결을 가져와 사용 후 반환."""
    p = get_pool()
    conn = p.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


@contextmanager
def get_cursor(dict_cursor: bool = False):
    """커서를 가져와 사용 후 자동 정리."""
    cursor_factory = RealDictCursor if dict_cursor else None
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
        finally:
            cur.close()


def execute_query(sql: str, params: tuple = None, dict_cursor: bool = True) -> list:
    """SELECT 쿼리 실행 후 결과 리스트 반환."""
    with get_cursor(dict_cursor=dict_cursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def execute_command(sql: str, params: tuple = None) -> int:
    """INSERT/UPDATE/DELETE 등 실행, 영향 받은 행 수 반환."""
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def check_connection() -> bool:
    """DB 연결 상태 확인."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
            return True
    except Exception as e:
        logger.error(f"DB connection check failed: {e}")
        return False
