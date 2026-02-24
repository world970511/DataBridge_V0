"""
외부 DB 플러그인 시스템 단위 테스트.

테스트 대상:
    - 플러그인 팩토리 (create_plugin, supported_types)
    - BaseExternalDB 속성
    - PostgreSQLPlugin: connect, disconnect, execute_query, get_tables, check_connection
    - registry: register, get, list, remove, sync_schema, is_external_table
    - query_db 라우팅: _extract_table_names, _detect_query_target, _execute_external_select
    - ExternalDBConfig 설정 로드
"""

from unittest.mock import patch, MagicMock, PropertyMock
import pytest


# ── 팩토리 테스트 ──

from db.external import create_plugin, supported_types
from db.external.base import BaseExternalDB
from db.external.postgresql_plugin import PostgreSQLPlugin


class TestPluginFactory:
    """create_plugin() 팩토리 테스트."""

    def test_create_postgresql(self):
        plugin = create_plugin(
            db_type="postgresql",
            name="test",
            host="localhost",
            port=5432,
            database="testdb",
            user="user",
            password="pass",
        )
        assert isinstance(plugin, PostgreSQLPlugin)
        assert isinstance(plugin, BaseExternalDB)

    def test_unsupported_db_type_raises(self):
        with pytest.raises(KeyError):
            create_plugin(db_type="mysql", name="test")

    def test_supported_types(self):
        types = supported_types()
        assert "postgresql" in types


class TestBaseExternalDB:
    """BaseExternalDB 속성 테스트."""

    def test_plugin_attributes(self):
        plugin = create_plugin(
            db_type="postgresql",
            name="erp",
            host="localhost",
            port=5433,
            database="company_erp",
            user="readonly",
            password="readonly1234",
        )
        assert plugin.name == "erp"
        assert plugin.db_type == "postgresql"
        assert plugin.read_only is True


# ── PostgreSQLPlugin 테스트 ──

class TestPostgreSQLPlugin:
    """PostgreSQLPlugin 동작 테스트 (mock 기반)."""

    def _make_plugin(self) -> PostgreSQLPlugin:
        return PostgreSQLPlugin(
            name="test_db",
            host="localhost",
            port=5433,
            database="testdb",
            user="user",
            password="pass",
        )

    @patch("db.external.postgresql_plugin.pool.SimpleConnectionPool")
    def test_connect_creates_pool(self, mock_pool_cls):
        plugin = self._make_plugin()
        plugin.connect()
        mock_pool_cls.assert_called_once()
        assert plugin._pool is not None

    @patch("db.external.postgresql_plugin.pool.SimpleConnectionPool")
    def test_disconnect_closes_pool(self, mock_pool_cls):
        plugin = self._make_plugin()
        mock_pool_instance = MagicMock()
        mock_pool_instance.closed = False
        mock_pool_cls.return_value = mock_pool_instance

        plugin.connect()
        plugin.disconnect()
        mock_pool_instance.closeall.assert_called_once()
        assert plugin._pool is None

    @patch("db.external.postgresql_plugin.pool.SimpleConnectionPool")
    def test_execute_query_success(self, mock_pool_cls):
        """정상 쿼리 실행 시 success=True, 데이터 반환."""
        plugin = self._make_plugin()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.return_value = [
            {"id": 1, "amount": 100},
            {"id": 2, "amount": 200},
        ]

        mock_pool_instance = MagicMock()
        mock_pool_instance.closed = False
        mock_pool_instance.getconn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_pool_cls.return_value = mock_pool_instance

        plugin.connect()
        result = plugin.execute_query("SELECT * FROM test", max_rows=100)

        assert result["success"] is True
        assert result["row_count"] == 2
        assert result["truncated"] is False
        assert result["error"] is None
        assert len(result["data"]) == 2

    @patch("db.external.postgresql_plugin.pool.SimpleConnectionPool")
    def test_execute_query_truncation(self, mock_pool_cls):
        """max_rows 초과 시 truncated=True."""
        plugin = self._make_plugin()

        # max_rows=2, 결과 3행 반환
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchmany.return_value = [
            {"id": 1}, {"id": 2}, {"id": 3},
        ]

        mock_pool_instance = MagicMock()
        mock_pool_instance.closed = False
        mock_pool_instance.getconn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_pool_cls.return_value = mock_pool_instance

        plugin.connect()
        result = plugin.execute_query("SELECT * FROM test", max_rows=2)

        assert result["success"] is True
        assert result["row_count"] == 2
        assert result["truncated"] is True

    @patch("db.external.postgresql_plugin.pool.SimpleConnectionPool")
    def test_execute_query_error(self, mock_pool_cls):
        """DB 에러 시 success=False."""
        plugin = self._make_plugin()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("relation does not exist")

        mock_pool_instance = MagicMock()
        mock_pool_instance.closed = False
        mock_pool_instance.getconn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_pool_cls.return_value = mock_pool_instance

        plugin.connect()
        result = plugin.execute_query("SELECT * FROM nonexistent")

        assert result["success"] is False
        assert "relation does not exist" in result["error"]

    @patch("db.external.postgresql_plugin.pool.SimpleConnectionPool")
    def test_check_connection_ok(self, mock_pool_cls):
        plugin = self._make_plugin()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        mock_pool_instance = MagicMock()
        mock_pool_instance.closed = False
        mock_pool_instance.getconn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_pool_cls.return_value = mock_pool_instance

        plugin.connect()
        assert plugin.check_connection() is True

    @patch("db.external.postgresql_plugin.pool.SimpleConnectionPool")
    def test_check_connection_fail(self, mock_pool_cls):
        plugin = self._make_plugin()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("connection refused")

        mock_pool_instance = MagicMock()
        mock_pool_instance.closed = False
        mock_pool_instance.getconn.return_value = mock_conn
        mock_conn.cursor.return_value = mock_cursor
        mock_pool_cls.return_value = mock_pool_instance

        plugin.connect()
        assert plugin.check_connection() is False


# ── Registry 테스트 ──

import db.external.registry as registry


class TestRegistry:
    """외부 DB 레지스트리 테스트."""

    def setup_method(self):
        """각 테스트 전 레지스트리 초기화."""
        registry._registry.clear()

    @patch("db.external.registry.create_plugin")
    def test_register_and_get(self, mock_create):
        """등록 후 조회."""
        mock_plugin = MagicMock()
        mock_plugin.name = "erp"
        mock_plugin.db_type = "postgresql"
        mock_plugin.check_connection.return_value = True
        mock_create.return_value = mock_plugin

        ok = registry.register_external_db(
            name="erp", db_type="postgresql",
            host="localhost", port=5433,
            database="erp_db", user="u", password="p",
        )
        assert ok is True

        plugin = registry.get_external_db("erp")
        assert plugin is not None
        assert plugin.name == "erp"

    @patch("db.external.registry.create_plugin")
    def test_register_connection_fail(self, mock_create):
        """연결 확인 실패 시 등록 안 됨."""
        mock_plugin = MagicMock()
        mock_plugin.check_connection.return_value = False
        mock_create.return_value = mock_plugin

        ok = registry.register_external_db(
            name="bad", db_type="postgresql",
            host="bad", port=5432,
            database="x", user="u", password="p",
        )
        assert ok is False
        assert registry.get_external_db("bad") is None

    @patch("db.external.registry.create_plugin")
    def test_list_external_dbs(self, mock_create):
        mock_plugin = MagicMock()
        mock_plugin.name = "erp"
        mock_plugin.db_type = "postgresql"
        mock_plugin.check_connection.return_value = True
        mock_create.return_value = mock_plugin

        registry.register_external_db(
            name="erp", db_type="postgresql",
            host="h", port=5432, database="d", user="u", password="p",
        )

        dbs = registry.list_external_dbs()
        assert len(dbs) == 1
        assert dbs[0]["name"] == "erp"
        assert dbs[0]["connected"] is True

    @patch("db.external.registry.create_plugin")
    def test_remove_external_db(self, mock_create):
        mock_plugin = MagicMock()
        mock_plugin.name = "erp"
        mock_plugin.db_type = "postgresql"
        mock_plugin.check_connection.return_value = True
        mock_create.return_value = mock_plugin

        registry.register_external_db(
            name="erp", db_type="postgresql",
            host="h", port=5432, database="d", user="u", password="p",
        )

        ok = registry.remove_external_db("erp")
        assert ok is True
        mock_plugin.disconnect.assert_called_once()
        assert registry.get_external_db("erp") is None

    def test_remove_nonexistent(self):
        assert registry.remove_external_db("none") is False

    def test_get_nonexistent(self):
        assert registry.get_external_db("none") is None


class TestIsExternalTable:
    """is_external_table() 테스트."""

    @patch("db.connection.execute_query")
    def test_external_table_detected(self, mock_query):
        mock_query.return_value = [{"source_file": "external:erp:transactions"}]
        result = registry.is_external_table("transactions")
        assert result == "erp"

    @patch("db.connection.execute_query")
    def test_internal_table(self, mock_query):
        mock_query.return_value = [{"source_file": "/data/sales.csv"}]
        result = registry.is_external_table("sales")
        assert result is None

    @patch("db.connection.execute_query")
    def test_table_not_found(self, mock_query):
        mock_query.return_value = []
        result = registry.is_external_table("nonexistent")
        assert result is None

    @patch("db.connection.execute_query")
    def test_db_error_returns_none(self, mock_query):
        mock_query.side_effect = Exception("DB error")
        result = registry.is_external_table("anything")
        assert result is None


# ── Query Routing 테스트 ──

from agent.tools.query_db import (
    _extract_table_names,
    _detect_query_target,
    validate_sql,
)


class TestExtractTableNames:
    """SQL에서 테이블명 추출 테스트."""

    def test_simple_from(self):
        tables = _extract_table_names("SELECT * FROM transactions")
        assert "transactions" in tables

    def test_from_with_alias(self):
        tables = _extract_table_names("SELECT t.id FROM transactions t")
        assert "transactions" in tables

    def test_join(self):
        tables = _extract_table_names(
            "SELECT * FROM transactions t JOIN partners p ON t.partner_id = p.id"
        )
        assert "transactions" in tables
        assert "partners" in tables

    def test_multiple_joins(self):
        sql = (
            "SELECT * FROM transactions t "
            "JOIN partners p ON t.partner_id = p.id "
            "JOIN products pr ON t.product_id = pr.id"
        )
        tables = _extract_table_names(sql)
        assert "transactions" in tables
        assert "partners" in tables
        assert "products" in tables

    def test_subquery_tables(self):
        sql = "SELECT * FROM (SELECT * FROM transactions) sub"
        tables = _extract_table_names(sql)
        assert "transactions" in tables

    def test_no_tables(self):
        tables = _extract_table_names("SELECT 1")
        assert tables == []


class TestDetectQueryTarget:
    """_detect_query_target() 라우팅 판별 테스트."""

    @patch("db.connection.execute_query")
    def test_all_internal(self, mock_query):
        """내부 테이블만 참조 → 'internal'."""
        mock_query.return_value = [{"source_file": "/data/sales.csv"}]
        target = _detect_query_target("SELECT * FROM sales")
        assert target == "internal"

    @patch("db.connection.execute_query")
    def test_all_external(self, mock_query):
        """외부 테이블만 참조 → 'external:{name}'."""
        mock_query.return_value = [{"source_file": "external:erp:transactions"}]
        target = _detect_query_target("SELECT * FROM transactions")
        assert target == "external:erp"

    @patch("db.connection.execute_query")
    def test_mixed_tables(self, mock_query):
        """내부+외부 혼합 → 'mixed'."""
        def side_effect(sql, params):
            table = params[0]
            if table == "transactions":
                return [{"source_file": "external:erp:transactions"}]
            return [{"source_file": "/data/sales.csv"}]

        mock_query.side_effect = side_effect
        target = _detect_query_target(
            "SELECT * FROM transactions JOIN sales ON TRUE"
        )
        assert target == "mixed"

    def test_no_tables_defaults_internal(self):
        target = _detect_query_target("SELECT 1")
        assert target == "internal"


# ── ExternalDBConfig 설정 테스트 ──

from config.settings import ExternalDBConfig


class TestExternalDBConfig:
    """ExternalDBConfig 데이터클래스 테스트."""

    def test_defaults(self):
        cfg = ExternalDBConfig()
        assert cfg.enabled is False
        assert cfg.name == ""
        assert cfg.db_type == "postgresql"
        assert cfg.port == 5432
        assert cfg.max_connections == 3
        assert cfg.query_timeout == 30

    def test_custom_values(self):
        cfg = ExternalDBConfig(
            enabled=True,
            name="erp",
            host="external-postgres",
            port=5433,
            database="company_erp",
            user="readonly",
            password="readonly1234",
        )
        assert cfg.enabled is True
        assert cfg.name == "erp"
        assert cfg.port == 5433

    @patch.dict("os.environ", {
        "EXTERNAL_DB_ENABLED": "true",
        "EXTERNAL_DB_NAME_ALIAS": "erp",
        "EXTERNAL_DB_HOST": "testhost",
        "EXTERNAL_DB_PORT": "5433",
        "EXTERNAL_DB_NAME": "testdb",
    })
    def test_load_from_env(self):
        from config.settings import load_settings
        settings = load_settings()
        assert settings.external_db.enabled is True
        assert settings.external_db.name == "erp"
        assert settings.external_db.host == "testhost"
        assert settings.external_db.port == 5433
        assert settings.external_db.database == "testdb"
