"""
외부 DB 플러그인 팩토리.

db_type 문자열로 적절한 BaseExternalDB 구현체를 반환합니다.
새 DB 타입 추가 시 이 파일의 _PLUGINS에 등록하면 됩니다.
"""

from db.external.base import BaseExternalDB
from db.external.postgresql_plugin import PostgreSQLPlugin

_PLUGINS: dict[str, type[BaseExternalDB]] = {
    "postgresql": PostgreSQLPlugin,
}


def create_plugin(db_type: str, **kwargs) -> BaseExternalDB:
    """
    DB 타입에 맞는 플러그인 인스턴스를 생성.

    Args:
        db_type: 'postgresql' 등.
        **kwargs: 플러그인 생성자에 전달할 파라미터.

    Raises:
        KeyError: 지원하지 않는 DB 타입.
    """
    cls = _PLUGINS.get(db_type)
    if cls is None:
        raise KeyError(f"지원하지 않는 외부 DB 타입: {db_type}")
    return cls(**kwargs)


def supported_types() -> list[str]:
    """지원하는 DB 타입 목록."""
    return list(_PLUGINS.keys())
