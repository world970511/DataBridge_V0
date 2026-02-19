"""
PostgreSQL 연결 풀 관리 모듈.

psycopg2의 SimpleConnectionPool을 사용하여 최소 1개 ~ 최대 10개의
커넥션을 풀로 관리하며, 컨텍스트 매니저를 통해 연결과 커서를
안전하게 획득/반환합니다. 정상 종료 시 자동 commit, 예외 발생 시 rollback을 수행합니다.
"""

import logging
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from config.settings import get_settings

logger = logging.getLogger(__name__)

_pool: Optional[pool.SimpleConnectionPool] = None


def get_pool() -> pool.SimpleConnectionPool:
    """
    PostgreSQL 커넥션 풀 싱글톤을 반환.

    풀이 아직 생성되지 않았거나 닫힌 경우, Settings의 DB URL을 사용하여
    minconn=1, maxconn=10으로 새 풀을 생성합니다.
    Returns: psycopg2 SimpleConnectionPool 인스턴스.
    """
    global _pool
    if _pool is None or _pool.closed:
        settings = get_settings()
        _pool = pool.SimpleConnectionPool(
            minconn=1,
            maxconn=10,
            host=settings.db.host,
            port=settings.db.port,
            dbname=settings.db.name,
            user=settings.db.user,
            password=settings.db.password,
        )
        logger.info("PostgreSQL connection pool created")
    return _pool


@contextmanager
def get_connection():
    """
    커넥션 풀에서 연결을 하나 획득하여 사용 후 자동 반환하는 컨텍스트 매니저.

    with 블록 안에서 정상 종료 시 commit, 예외 발생 시 rollback을 수행하고,
    사용이 끝난 연결은 풀에 반환합니다.
    Yields: psycopg2 connection 객체.
    """
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
    """
    DB 커서를 획득하여 사용 후 자동으로 닫아주는 컨텍스트 매니저.

    dict_cursor=True이면 RealDictCursor를 사용하여 결과를 딕셔너리로 반환하고,
    False이면 기본 튜플 커서를 사용합니다.
    Yields: psycopg2 cursor 객체.
    """
    cursor_factory = RealDictCursor if dict_cursor else None
    with get_connection() as conn:
        cur = conn.cursor(cursor_factory=cursor_factory)
        try:
            yield cur
        finally:
            cur.close()


def execute_query(sql: str, params: tuple = None, dict_cursor: bool = True) -> list:
    """
    SELECT 쿼리를 실행하고 전체 결과를 리스트로 반환.

    기본적으로 RealDictCursor를 사용하여 각 행을 딕셔너리 형태로 반환합니다.
    Returns: 쿼리 결과 행들의 리스트 (기본: list[dict]).
    """
    with get_cursor(dict_cursor=dict_cursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()

def execute_command(sql: str, params: tuple = None) -> int:
    """
    INSERT, UPDATE, DELETE 등 데이터 변경 쿼리를 실행.

    실행 완료 후 자동으로 commit되며, 실패 시 rollback됩니다.
    Returns: 영향을 받은 행의 수 (int).
    """
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def check_connection() -> bool:
    """
    PostgreSQL 연결 상태를 확인하기 위해 'SELECT 1' 쿼리를 실행.

    앱 기동 시 DB가 정상적으로 응답하는지 검증하는 용도로 사용됩니다.
    Returns: 연결 성공 시 True, 실패 시 False.
    """
    try:
        with get_cursor() as cur:
            cur.execute("SELECT 1")
            return True
    except Exception as e:
        logger.error(f"DB connection check failed: {e}")
        return False
