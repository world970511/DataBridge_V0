"""
외부 DB 연결 레지스트리.

외부 DB 커넥터의 등록/조회/스키마 동기화를 관리합니다.
settings의 ExternalDBConfig에서 자동으로 등록하거나,
런타임에 수동으로 추가할 수 있습니다.

스키마 동기화(sync_schema)는 외부 DB의 테이블 정보를
catalog_tables에 등록하여 sql_agent가 자연스럽게 참조하도록 합니다.
"""

import logging
from typing import Optional

from db.external.base import BaseExternalDB
from db.external import create_plugin

logger = logging.getLogger(__name__)

# 등록된 외부 DB 커넥터 (name → instance)
_registry: dict[str, BaseExternalDB] = {}


def register_from_settings() -> bool:
    """
    config/settings.py의 ExternalDBConfig에서 외부 DB를 자동 등록.

    앱 시작 시 호출됩니다. enabled=False이면 아무 작업도 하지 않습니다.
    Returns: 등록 성공 여부.
    """
    try:
        from config.settings import get_settings
        cfg = get_settings().external_db

        if not cfg.enabled:
            logger.info("External DB is disabled")
            return False

        if not cfg.name or not cfg.host:
            logger.warning("External DB config incomplete, skipping")
            return False

        return register_external_db(
            name=cfg.name,
            db_type=cfg.db_type,
            host=cfg.host,
            port=cfg.port,
            database=cfg.database,
            user=cfg.user,
            password=cfg.password,
            max_connections=cfg.max_connections,
        )

    except Exception as e:
        logger.error(f"Failed to register external DB from settings: {e}")
        return False


def register_external_db(
    name: str,
    db_type: str = "postgresql",
    host: str = "",
    port: int = 5432,
    database: str = "",
    user: str = "",
    password: str = "",
    max_connections: int = 3,
) -> bool:
    """
    외부 DB를 레지스트리에 등록하고 연결을 초기화.

    Args:
        name: 연결 이름 (예: "erp").
        db_type: DB 종류 (예: "postgresql").
        host, port, database, user, password: 접속 정보.
        max_connections: 커넥션 풀 최대 크기.

    Returns: 등록 성공 여부.
    """
    if name in _registry:
        logger.info(f"External DB '{name}' already registered, replacing")
        _registry[name].disconnect()

    try:
        plugin = create_plugin(
            db_type=db_type,
            name=name,
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            max_connections=max_connections,
        )
        plugin.connect()

        if not plugin.check_connection():
            logger.error(f"External DB '{name}' connection check failed")
            plugin.disconnect()
            return False

        _registry[name] = plugin
        logger.info(f"External DB '{name}' registered ({db_type} @ {host}:{port}/{database})")
        return True

    except Exception as e:
        logger.error(f"Failed to register external DB '{name}': {e}")
        return False


def get_external_db(name: str) -> Optional[BaseExternalDB]:
    """이름으로 외부 DB 커넥터를 조회."""
    return _registry.get(name)


def list_external_dbs() -> list[dict]:
    """등록된 외부 DB 목록을 반환."""
    return [
        {
            "name": p.name,
            "db_type": p.db_type,
            "connected": p.check_connection(),
        }
        for p in _registry.values()
    ]


def remove_external_db(name: str) -> bool:
    """외부 DB를 레지스트리에서 제거하고 연결을 닫음."""
    plugin = _registry.pop(name, None)
    if plugin:
        plugin.disconnect()
        logger.info(f"External DB '{name}' removed")
        return True
    return False


def sync_schema(name: str) -> int:
    """
    외부 DB의 테이블 스키마를 catalog_tables에 동기화.

    외부 DB에서 테이블 목록과 컬럼 정보를 조회하여
    catalog_tables에 등록합니다. sql_agent가 자연스럽게 외부 테이블을
    참조할 수 있도록 합니다.

    Args:
        name: 동기화할 외부 DB 이름.

    Returns: 등록된 테이블 수.
    """
    plugin = _registry.get(name)
    if not plugin:
        logger.error(f"External DB '{name}' not found in registry")
        return 0

    tables = plugin.get_tables()
    if not tables:
        logger.info(f"No tables found in external DB '{name}'")
        return 0

    from catalog.catalog import register_table

    count = 0
    for t in tables:
        table_name = t["table_name"]
        try:
            register_table(
                table_name=table_name,
                source_file=f"external:{name}:{table_name}",
                file_type="external_db",
                row_count=t.get("row_count", 0),
                column_count=len(t.get("columns", [])),
                columns_json=t.get("columns", []),
                description=f"[외부DB:{name}] {table_name}",
                data_category="external",
                tags=["외부DB", name, table_name],
            )
            count += 1
            logger.info(f"Synced external table: {name}:{table_name}")
        except Exception as e:
            logger.error(f"Failed to sync table {name}:{table_name}: {e}")

    logger.info(f"Schema sync complete: {count}/{len(tables)} tables from '{name}'")

    from notifications.dispatcher import emit_event
    from notifications.events import EXTERNAL_DB_SYNCED
    emit_event(EXTERNAL_DB_SYNCED, {"name": name, "tables": count})

    return count


def is_external_table(table_name: str) -> Optional[str]:
    """
    테이블이 외부 DB에 있는지 확인.

    catalog_tables의 source_file이 'external:{name}:...'이면
    해당 DB 이름을 반환하고, 아니면 None을 반환합니다.

    Args:
        table_name: 확인할 테이블명.

    Returns: 외부 DB 이름 또는 None.
    """
    try:
        from db.connection import execute_query
        rows = execute_query(
            "SELECT source_file FROM catalog_tables WHERE table_name = %s",
            (table_name,),
        )
        if rows and rows[0]["source_file"].startswith("external:"):
            # "external:erp:transactions" → "erp"
            parts = rows[0]["source_file"].split(":")
            return parts[1] if len(parts) >= 2 else None
        return None
    except Exception:
        return None
