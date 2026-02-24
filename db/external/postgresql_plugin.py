"""
PostgreSQL 외부 DB 플러그인.

psycopg2 SimpleConnectionPool을 사용하여 외부 PostgreSQL에 읽기 전용으로 접속합니다.
기존 db/connection.py의 패턴을 그대로 따르되, 별도 커넥션 풀을 관리합니다.
"""

import logging
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from db.external.base import BaseExternalDB

logger = logging.getLogger(__name__)


class PostgreSQLPlugin(BaseExternalDB):
    """외부 PostgreSQL 읽기 전용 커넥터."""

    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        max_connections: int = 3,
    ):
        super().__init__(name=name, db_type="postgresql")
        self._host = host
        self._port = port
        self._database = database
        self._user = user
        self._password = password
        self._max_connections = max_connections
        self._pool: Optional[pool.SimpleConnectionPool] = None

    def connect(self) -> None:
        if self._pool is None or self._pool.closed:
            self._pool = pool.SimpleConnectionPool(
                minconn=1,
                maxconn=self._max_connections,
                host=self._host,
                port=self._port,
                dbname=self._database,
                user=self._user,
                password=self._password,
            )
            logger.info(
                f"External PostgreSQL pool created: {self.name} "
                f"({self._host}:{self._port}/{self._database})"
            )

    def disconnect(self) -> None:
        if self._pool and not self._pool.closed:
            self._pool.closeall()
            self._pool = None
            logger.info(f"External PostgreSQL pool closed: {self.name}")

    @contextmanager
    def _get_cursor(self):
        """RealDictCursor를 획득하는 컨텍스트 매니저."""
        self.connect()
        conn = self._pool.getconn()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            try:
                yield cur
            finally:
                cur.close()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def execute_query(
        self,
        sql: str,
        params: Optional[tuple] = None,
        max_rows: int = 5000,
        timeout: int = 30,
    ) -> dict:
        try:
            with self._get_cursor() as cur:
                cur.execute(f"SET statement_timeout = {timeout * 1000}")
                cur.execute(sql, params)
                rows = cur.fetchmany(max_rows + 1)

                truncated = len(rows) > max_rows
                if truncated:
                    rows = rows[:max_rows]

                data = [dict(row) for row in rows]

                logger.info(
                    f"External query [{self.name}]: {len(data)} rows, "
                    f"truncated={truncated}"
                )

                return {
                    "success": True,
                    "data": data,
                    "row_count": len(data),
                    "truncated": truncated,
                    "error": None,
                }

        except Exception as e:
            error_msg = str(e).strip()
            logger.error(f"External query [{self.name}] failed: {error_msg}")
            return {
                "success": False,
                "data": [],
                "row_count": 0,
                "truncated": False,
                "error": error_msg,
            }

    def get_tables(self) -> list[dict]:
        """information_schema에서 테이블/컬럼 정보 조회."""
        try:
            with self._get_cursor() as cur:
                # public 스키마의 일반 테이블만
                cur.execute("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                """)
                tables_rows = cur.fetchall()

                result = []
                for t in tables_rows:
                    tname = t["table_name"]

                    # 컬럼 정보
                    cur.execute("""
                        SELECT column_name, data_type, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = %s
                        ORDER BY ordinal_position
                    """, (tname,))
                    cols = [
                        {
                            "name": c["column_name"],
                            "type": c["data_type"].upper(),
                        }
                        for c in cur.fetchall()
                    ]

                    # 대략적 행 수 (pg_stat 사용, 정확하지 않으면 COUNT)
                    cur.execute("""
                        SELECT reltuples::BIGINT AS estimate
                        FROM pg_class
                        WHERE relname = %s
                    """, (tname,))
                    row = cur.fetchone()
                    row_count = row["estimate"] if row and row["estimate"] >= 0 else 0

                    if row_count == 0:
                        cur.execute(f"SELECT COUNT(*) AS cnt FROM {tname}")
                        row_count = cur.fetchone()["cnt"]

                    result.append({
                        "table_name": tname,
                        "row_count": row_count,
                        "columns": cols,
                    })

                return result

        except Exception as e:
            logger.error(f"Failed to get tables from [{self.name}]: {e}")
            return []

    def check_connection(self) -> bool:
        try:
            with self._get_cursor() as cur:
                cur.execute("SELECT 1")
                return True
        except Exception as e:
            logger.error(f"External DB [{self.name}] connection check failed: {e}")
            return False
