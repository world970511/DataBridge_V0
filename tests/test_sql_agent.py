"""
SQL 에이전트 모듈 테스트.

agent/sql_agent.py의 process() 파이프라인과 내부 함수(_extract_sql, _summarize_results)를
mock 기반으로 테스트합니다. 실제 LLM이나 DB 호출 없이 각 단계의 동작을 검증합니다.

Phase 4에서 추가된 4단계 SQL 분류(classify_sql) 분기도 테스트합니다.

실행:
    pytest tests/test_sql_agent.py -v -m unit
"""

from unittest.mock import patch, MagicMock

import pytest

from agent.sql_agent import process, _extract_sql
from approval.sql_classifier import SqlCategory


# ============================================
# _extract_sql() 테스트
# ============================================

@pytest.mark.unit
class TestExtractSql:
    """_extract_sql() — LLM 응답에서 SQL 추출 테스트."""

    def test_extract_from_sql_code_block(self):
        """```sql 코드블록에서 SQL을 추출합니다."""
        response = "다음은 SQL입니다:\n```sql\nSELECT * FROM sales\n```\n설명입니다."
        sql = _extract_sql(response)
        assert "SELECT" in sql
        assert "sales" in sql

    def test_extract_from_generic_code_block(self):
        """``` 코드블록에서 SELECT SQL을 추출합니다."""
        response = "쿼리:\n```\nSELECT COUNT(*) FROM products\n```"
        sql = _extract_sql(response)
        assert "SELECT" in sql
        assert "products" in sql

    def test_extract_bare_select(self):
        """코드블록 없이 SELECT로 시작하는 텍스트에서 추출합니다."""
        response = "결과:\nSELECT id, name FROM sales\nWHERE amount > 100;"
        sql = _extract_sql(response)
        assert "SELECT" in sql

    def test_empty_response(self):
        """빈 응답은 빈 문자열을 반환합니다."""
        assert _extract_sql("") == ""

    def test_no_sql_in_response(self):
        """SQL이 없는 응답은 빈 문자열을 반환합니다."""
        assert _extract_sql("이것은 일반 텍스트입니다.") == ""

    def test_multiline_sql(self):
        """여러 줄의 SQL을 올바르게 추출합니다."""
        response = """```sql
SELECT
    category,
    SUM(amount) AS total
FROM sales
GROUP BY category
ORDER BY total DESC
```"""
        sql = _extract_sql(response)
        assert "SELECT" in sql
        assert "GROUP BY" in sql
        assert "ORDER BY" in sql

    def test_extract_delete_from_code_block(self):
        """```sql 코드블록에서 DELETE SQL을 추출합니다."""
        response = "삭제 SQL:\n```sql\nDELETE FROM sales WHERE year < 2020\n```"
        sql = _extract_sql(response)
        assert "DELETE" in sql

    def test_extract_create_from_code_block(self):
        """```sql 코드블록에서 CREATE SQL을 추출합니다."""
        response = "```sql\nCREATE TABLE test (id SERIAL)\n```"
        sql = _extract_sql(response)
        assert "CREATE" in sql


# ============================================
# process() 파이프라인 테스트 — SAFE (SELECT)
# ============================================

@pytest.mark.unit
class TestSqlAgentProcessSafe:
    """process() — SAFE(SELECT) SQL 파이프라인 테스트."""

    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.execute_select")
    @patch("agent.sql_agent.log_action")
    def test_successful_select_pipeline(self, mock_log, mock_exec, mock_schema, mock_gen):
        """정상 SELECT 파이프라인: 스키마 → SQL 생성 → 실행 → 요약."""
        mock_schema.return_value = "## 테이블\n### 1. sales (100행)\n컬럼: id(BIGINT)"
        mock_gen.side_effect = [
            "```sql\nSELECT SUM(amount) AS total FROM sales\n```",
            "총 매출은 1,234,000원입니다.",
        ]
        mock_exec.return_value = {
            "success": True,
            "data": [{"total": 1234000}],
            "row_count": 1,
            "truncated": False,
            "error": None,
            "sql": "SELECT SUM(amount) AS total FROM sales",
        }

        result = process("총 매출 보여줘", user_id="testuser")

        assert result["success"] is True
        assert result["agent"] == "sql"
        assert result["sql"] is not None
        assert result["sql_category"] == SqlCategory.SAFE.value
        assert "1,234,000" in result["answer"]

    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.log_action")
    def test_no_tables_available(self, mock_log, mock_schema, mock_gen):
        """테이블이 없으면 안내 메시지를 반환합니다."""
        mock_schema.return_value = "No tables registered."

        result = process("매출 보여줘")

        assert result["success"] is False
        assert "등록된 테이블이 없습니다" in result["answer"]

    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.log_action")
    def test_llm_connection_failure(self, mock_log, mock_schema, mock_gen):
        """LLM 연결 실패 시 에러 메시지를 반환합니다."""
        mock_schema.return_value = "## 테이블\n### sales"
        mock_gen.return_value = ""

        result = process("매출 보여줘")

        assert result["success"] is False
        assert "LLM" in result["answer"] or "연결" in result["answer"]

    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.execute_select")
    @patch("agent.sql_agent.log_action")
    def test_sql_execution_failure(self, mock_log, mock_exec, mock_schema, mock_gen):
        """SQL 실행 실패 시 에러 메시지를 반환합니다."""
        mock_schema.return_value = "## 테이블\n### sales"
        mock_gen.return_value = "```sql\nSELECT * FROM nonexistent\n```"
        mock_exec.return_value = {
            "success": False,
            "data": [],
            "row_count": 0,
            "truncated": False,
            "error": 'relation "nonexistent" does not exist',
            "sql": "SELECT * FROM nonexistent",
        }

        result = process("없는 테이블 조회")

        assert result["success"] is False
        assert "오류" in result["answer"] or "error" in result["answer"].lower()


# ============================================
# process() 파이프라인 테스트 — NEEDS_APPROVAL
# ============================================

@pytest.mark.unit
class TestSqlAgentProcessApproval:
    """process() — NEEDS_APPROVAL SQL 분기 테스트."""

    @patch("agent.sql_agent.create_request")
    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.log_action")
    def test_delete_triggers_approval(self, mock_log, mock_schema, mock_gen, mock_create):
        """DELETE SQL은 승인 요청을 생성합니다."""
        mock_schema.return_value = "## 테이블\n### sales"
        mock_gen.return_value = "```sql\nDELETE FROM sales WHERE year < 2020\n```"
        mock_create.return_value = 42

        result = process("2020년 이전 데이터 삭제해줘", user_id="kim")

        assert result["success"] is True
        assert result["sql_category"] == SqlCategory.NEEDS_APPROVAL.value
        assert result["approval_id"] == 42
        assert "승인" in result["answer"]
        mock_create.assert_called_once()

    @patch("agent.sql_agent.create_request")
    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.log_action")
    def test_drop_triggers_approval(self, mock_log, mock_schema, mock_gen, mock_create):
        """DROP SQL은 승인 요청을 생성합니다."""
        mock_schema.return_value = "## 테이블\n### sales"
        mock_gen.return_value = "```sql\nDROP TABLE sales\n```"
        mock_create.return_value = 99

        result = process("테이블 삭제해줘", user_id="admin")

        assert result["success"] is True
        assert result["sql_category"] == SqlCategory.NEEDS_APPROVAL.value
        assert result["approval_id"] == 99


# ============================================
# process() 파이프라인 테스트 — FORBIDDEN
# ============================================

@pytest.mark.unit
class TestSqlAgentProcessForbidden:
    """process() — FORBIDDEN SQL 분기 테스트."""

    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.log_action")
    def test_grant_is_blocked(self, mock_log, mock_schema, mock_gen):
        """GRANT SQL은 차단됩니다."""
        mock_schema.return_value = "## 테이블\n### sales"
        mock_gen.return_value = "```sql\nGRANT ALL ON sales TO public\n```"

        result = process("모든 권한 줘")

        assert result["success"] is False
        assert result["sql_category"] == SqlCategory.FORBIDDEN.value
        assert "차단" in result["answer"]


# ============================================
# process() 파이프라인 테스트 — AUTO_ALLOWED
# ============================================

@pytest.mark.unit
class TestSqlAgentProcessAutoAllowed:
    """process() — AUTO_ALLOWED SQL 분기 테스트."""

    @patch("agent.sql_agent.execute_write")
    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.log_action")
    def test_insert_auto_executed(self, mock_log, mock_schema, mock_gen, mock_write):
        """INSERT SQL은 승인 없이 자동 실행됩니다."""
        mock_schema.return_value = "## 테이블\n### sales"
        mock_gen.return_value = "```sql\nINSERT INTO sales (name) VALUES ('test')\n```"
        mock_write.return_value = {
            "success": True,
            "rows_affected": 1,
            "error": None,
            "sql": "INSERT INTO sales (name) VALUES ('test')",
        }

        result = process("테스트 데이터 넣어줘", user_id="user1")

        assert result["success"] is True
        assert result["sql_category"] == SqlCategory.AUTO_ALLOWED.value
        assert "실행" in result["answer"]
        mock_write.assert_called_once()
