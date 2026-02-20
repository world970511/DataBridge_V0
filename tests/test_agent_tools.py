"""
에이전트 도구 모듈 테스트.

agent/tools/query_db.py, agent/tools/list_tables.py, agent/tools/search_docs.py의
기능을 테스트합니다. LLM이나 외부 서비스에 의존하지 않는 순수 로직 위주로 테스트합니다.

실행:
    pytest tests/test_agent_tools.py -v -m unit
"""

from unittest.mock import patch, MagicMock

import pytest

from agent.tools.query_db import validate_sql
from agent.tools.list_tables import (
    get_all_tables_summary, get_table_names, get_table_tags,
    _format_columns, _format_columns_rich,
)
from agent.tools.search_docs import format_search_results


# ============================================
# query_db.validate_sql() 테스트
# ============================================

@pytest.mark.unit
class TestValidateSql:
    """validate_sql() — SQL 보안 검증 테스트."""

    def test_valid_select(self):
        """일반적인 SELECT 쿼리는 통과합니다."""
        is_valid, msg = validate_sql("SELECT * FROM sales")
        assert is_valid is True

    def test_valid_select_with_where(self):
        """WHERE 절이 포함된 SELECT도 통과합니다."""
        is_valid, msg = validate_sql("SELECT id, name FROM products WHERE price > 100")
        assert is_valid is True

    def test_valid_select_with_join(self):
        """JOIN이 포함된 SELECT도 통과합니다."""
        sql = "SELECT s.id, p.name FROM sales s JOIN products p ON s.product_id = p.id"
        is_valid, msg = validate_sql(sql)
        assert is_valid is True

    def test_valid_aggregate(self):
        """집계 함수가 포함된 SELECT도 통과합니다."""
        sql = "SELECT category, COUNT(*), AVG(price) FROM products GROUP BY category"
        is_valid, msg = validate_sql(sql)
        assert is_valid is True

    def test_trailing_semicolon_allowed(self):
        """마지막 세미콜론은 허용됩니다."""
        is_valid, msg = validate_sql("SELECT * FROM sales;")
        assert is_valid is True

    def test_reject_empty_sql(self):
        """빈 문자열은 거부됩니다."""
        is_valid, msg = validate_sql("")
        assert is_valid is False

    def test_reject_whitespace_only(self):
        """공백만 있는 문자열은 거부됩니다."""
        is_valid, msg = validate_sql("   ")
        assert is_valid is False

    def test_reject_insert(self):
        """INSERT 문은 거부됩니다."""
        is_valid, msg = validate_sql("INSERT INTO sales VALUES (1, 'test', 100)")
        assert is_valid is False
        assert "INSERT" in msg or "SELECT" in msg

    def test_reject_update(self):
        """UPDATE 문은 거부됩니다."""
        is_valid, msg = validate_sql("UPDATE sales SET amount = 0")
        assert is_valid is False

    def test_reject_delete(self):
        """DELETE 문은 거부됩니다."""
        is_valid, msg = validate_sql("DELETE FROM sales")
        assert is_valid is False

    def test_reject_drop_table(self):
        """DROP TABLE은 거부됩니다."""
        is_valid, msg = validate_sql("DROP TABLE sales")
        assert is_valid is False

    def test_reject_alter_table(self):
        """ALTER TABLE은 거부됩니다."""
        is_valid, msg = validate_sql("ALTER TABLE sales ADD COLUMN new_col TEXT")
        assert is_valid is False

    def test_reject_multiple_statements(self):
        """세미콜론으로 구분된 다중 문장은 거부됩니다."""
        is_valid, msg = validate_sql("SELECT 1; DROP TABLE sales")
        assert is_valid is False

    def test_reject_sql_comments_line(self):
        """라인 주석(--)은 거부됩니다."""
        is_valid, msg = validate_sql("SELECT * FROM sales -- WHERE id = 1")
        assert is_valid is False

    def test_reject_sql_comments_block(self):
        """블록 주석(/* */)은 거부됩니다."""
        is_valid, msg = validate_sql("SELECT /* hidden */ * FROM sales")
        assert is_valid is False

    def test_reject_select_into(self):
        """SELECT ... INTO OUTFILE은 거부됩니다."""
        is_valid, msg = validate_sql("SELECT * INTO OUTFILE '/tmp/data' FROM sales")
        assert is_valid is False

    def test_reject_truncate(self):
        """TRUNCATE은 거부됩니다."""
        is_valid, msg = validate_sql("TRUNCATE TABLE sales")
        assert is_valid is False

    def test_reject_grant(self):
        """GRANT는 거부됩니다."""
        is_valid, msg = validate_sql("GRANT ALL ON sales TO public")
        assert is_valid is False


# ============================================
# list_tables 테스트
# ============================================

@pytest.mark.unit
class TestFormatColumns:
    """_format_columns() — 컬럼 정보 포맷팅 테스트."""

    def test_list_of_dicts(self):
        """딕셔너리 리스트 형태의 컬럼 정보를 포맷팅합니다."""
        columns = [
            {"name": "id", "type": "BIGINT"},
            {"name": "name", "type": "TEXT"},
        ]
        result = _format_columns(columns)
        assert "id(BIGINT)" in result
        assert "name(TEXT)" in result

    def test_json_string_input(self):
        """JSON 문자열 형태의 컬럼 정보도 처리합니다."""
        import json
        columns = json.dumps([{"name": "price", "type": "DOUBLE PRECISION"}])
        result = _format_columns(columns)
        assert "price(DOUBLE PRECISION)" in result

    def test_none_input(self):
        """None 입력 시 안내 메시지를 반환합니다."""
        result = _format_columns(None)
        assert "No column info" in result

    def test_empty_list(self):
        """빈 리스트 입력 시 안내 메시지를 반환합니다."""
        result = _format_columns([])
        assert "No column info" in result


@pytest.mark.unit
class TestFormatColumnsRich:
    """_format_columns_rich() — Rich Catalog 컬럼 포맷팅 테스트."""

    def test_with_descriptions(self):
        """column_descriptions가 있으면 '컬럼명(타입) - 설명' 형식입니다."""
        columns = [
            {"name": "id", "type": "BIGINT"},
            {"name": "name", "type": "TEXT"},
        ]
        descs = {"id": "고유 식별자", "name": "제품명"}
        result = _format_columns_rich(columns, descs)
        assert "id(BIGINT) - 고유 식별자" in result
        assert "name(TEXT) - 제품명" in result

    def test_partial_descriptions(self):
        """일부 컬럼만 설명이 있으면 해당 컬럼만 설명이 붙습니다."""
        columns = [
            {"name": "id", "type": "BIGINT"},
            {"name": "name", "type": "TEXT"},
        ]
        descs = {"name": "제품명"}  # id는 설명 없음
        result = _format_columns_rich(columns, descs)
        assert "id(BIGINT)" in result
        assert "- 고유 식별자" not in result  # id에는 설명 없음
        assert "name(TEXT) - 제품명" in result

    def test_json_string_descriptions(self):
        """JSON 문자열 형태의 column_descriptions도 처리합니다."""
        import json
        columns = [{"name": "amount", "type": "DOUBLE PRECISION"}]
        descs = json.dumps({"amount": "매출액(원)"})
        result = _format_columns_rich(columns, descs)
        assert "amount(DOUBLE PRECISION) - 매출액(원)" in result

    def test_no_descriptions(self):
        """column_descriptions가 None이면 기존 형식으로 폴백합니다."""
        columns = [{"name": "id", "type": "BIGINT"}]
        result = _format_columns_rich(columns, None)
        assert "id(BIGINT)" in result

    def test_backward_compatible_alias(self):
        """_format_columns는 _format_columns_rich의 별칭입니다."""
        assert _format_columns is _format_columns_rich


@pytest.mark.unit
class TestGetAllTablesSummary:
    """get_all_tables_summary() — 스키마 요약 테스트."""

    @patch("agent.tools.list_tables.list_tables")
    def test_returns_markdown_format(self, mock_list, sample_catalog_tables):
        """카탈로그 데이터가 있으면 마크다운 형식의 요약을 반환합니다."""
        mock_list.return_value = sample_catalog_tables
        result = get_all_tables_summary()
        assert "Available Tables" in result
        assert "sales" in result
        assert "products" in result

    @patch("agent.tools.list_tables.list_tables")
    def test_empty_catalog(self, mock_list):
        """카탈로그가 비어있으면 안내 메시지를 반환합니다."""
        mock_list.return_value = []
        result = get_all_tables_summary()
        assert "No tables registered" in result

    @patch("agent.tools.list_tables.list_tables")
    def test_includes_row_count(self, mock_list, sample_catalog_tables):
        """요약에 행 수 정보가 포함됩니다."""
        mock_list.return_value = sample_catalog_tables
        result = get_all_tables_summary()
        assert "15,230" in result

    @patch("agent.tools.list_tables.list_tables")
    def test_rich_catalog_includes_description(self, mock_list, sample_rich_catalog_tables):
        """Rich Catalog 데이터가 있으면 설명이 포함됩니다."""
        mock_list.return_value = sample_rich_catalog_tables
        result = get_all_tables_summary()
        assert "Description:" in result
        assert "2024년 2월" in result

    @patch("agent.tools.list_tables.list_tables")
    def test_rich_catalog_includes_tags(self, mock_list, sample_rich_catalog_tables):
        """Rich Catalog 데이터가 있으면 태그가 포함됩니다."""
        mock_list.return_value = sample_rich_catalog_tables
        result = get_all_tables_summary()
        assert "Tags:" in result
        assert "매출" in result

    @patch("agent.tools.list_tables.list_tables")
    def test_rich_catalog_includes_column_descriptions(self, mock_list, sample_rich_catalog_tables):
        """Rich Catalog 데이터가 있으면 컬럼 설명이 포함됩니다."""
        mock_list.return_value = sample_rich_catalog_tables
        result = get_all_tables_summary()
        assert "매출액(원)" in result
        assert "제품명" in result


@pytest.mark.unit
class TestGetTableNames:
    """get_table_names() — 테이블명 리스트 조회 테스트."""

    @patch("agent.tools.list_tables.list_tables")
    def test_returns_name_list(self, mock_list, sample_catalog_tables):
        """카탈로그에서 테이블명만 추출한 리스트를 반환합니다."""
        mock_list.return_value = sample_catalog_tables
        names = get_table_names()
        assert names == ["sales", "products"]

    @patch("agent.tools.list_tables.list_tables")
    def test_empty_catalog_returns_empty(self, mock_list):
        """카탈로그가 비어있으면 빈 리스트를 반환합니다."""
        mock_list.return_value = []
        names = get_table_names()
        assert names == []


@pytest.mark.unit
class TestGetTableTags:
    """get_table_tags() — 태그 매핑 조회 테스트."""

    @patch("catalog.catalog.get_table_tags")
    def test_returns_tag_mapping(self, mock_tags):
        """태그 매핑 딕셔너리를 반환합니다."""
        mock_tags.return_value = {
            "sales": ["매출", "제품"],
            "products": ["제품", "마스터"],
        }
        tags = get_table_tags()
        assert "sales" in tags
        assert "매출" in tags["sales"]

    @patch("catalog.catalog.get_table_tags")
    def test_empty_tags(self, mock_tags):
        """태그가 없으면 빈 딕셔너리를 반환합니다."""
        mock_tags.return_value = {}
        tags = get_table_tags()
        assert tags == {}


# ============================================
# search_docs 테스트
# ============================================

@pytest.mark.unit
class TestFormatSearchResults:
    """format_search_results() — 검색 결과 포맷팅 테스트."""

    def test_formats_with_sources(self, sample_search_results):
        """검색 결과에 출처와 유사도가 포함됩니다."""
        result = format_search_results(sample_search_results)
        assert "문서 1" in result
        assert "report.pdf" in result
        assert "유사도" in result

    def test_empty_results(self):
        """빈 결과 시 안내 메시지를 반환합니다."""
        result = format_search_results([])
        assert "찾지 못했습니다" in result

    def test_similarity_calculation(self):
        """코사인 거리가 유사도로 올바르게 변환됩니다."""
        results = [{"text": "test", "metadata": {"source": "a.pdf"}, "distance": 0.2}]
        result = format_search_results(results)
        # distance 0.2 → similarity 0.80
        assert "0.80" in result

    def test_missing_distance(self):
        """distance가 None인 경우에도 정상 포맷팅됩니다."""
        results = [{"text": "test", "metadata": {"source": "a.pdf"}, "distance": None}]
        result = format_search_results(results)
        assert "문서 1" in result
