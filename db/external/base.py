"""
외부 DB 플러그인 추상 클래스.

모든 외부 DB 커넥터는 이 클래스를 상속하고 필수 메서드를 구현합니다.
새 DB 타입 추가 시: 이 파일을 상속한 클래스를 작성하고 __init__.py의 _PLUGINS에 등록.
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseExternalDB(ABC):
    """외부 DB 연결 추상 클래스."""

    def __init__(self, name: str, db_type: str):
        self.name = name
        self.db_type = db_type
        self.read_only = True

    @abstractmethod
    def connect(self) -> None:
        """커넥션 풀/연결을 초기화."""

    @abstractmethod
    def disconnect(self) -> None:
        """커넥션 풀/연결을 정리."""

    @abstractmethod
    def execute_query(
        self,
        sql: str,
        params: Optional[tuple] = None,
        max_rows: int = 5000,
        timeout: int = 30,
    ) -> dict:
        """
        SELECT 쿼리를 실행하고 결과를 반환.

        Returns:
            {
                "success": bool,
                "data": list[dict],
                "row_count": int,
                "truncated": bool,
                "error": str | None,
            }
        """

    @abstractmethod
    def get_tables(self) -> list[dict]:
        """
        DB의 테이블 목록과 컬럼 정보를 반환.

        Returns:
            [
                {
                    "table_name": str,
                    "row_count": int,
                    "columns": [{"name": str, "type": str}, ...],
                },
                ...
            ]
        """

    @abstractmethod
    def check_connection(self) -> bool:
        """연결 상태 확인."""
