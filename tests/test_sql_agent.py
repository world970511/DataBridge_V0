"""
SQL 에이전트 모듈 테스트.

agent/sql_agent.py의 process() 파이프라인과 내부 함수(_extract_sql, _summarize_results)를
mock 기반으로 테스트합니다. 실제 LLM이나 DB 호출 없이 각 단계의 동작을 검증합니다.

실행:
    pytest tests/test_sql_agent.py -v -m unit
"""

from unittest.mock import patch, MagicMock

import pytest

from agent.sql_agent import process, _extract_sql


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


# ============================================
# process() 파이프라인 테스트
# ============================================

@pytest.mark.unit
class TestSqlAgentProcess:
    """process() — SQL 에이전트 전체 파이프라인 mock 테스트."""

    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.execute_select")
    @patch("agent.sql_agent.log_action")
    def test_successful_pipeline(self, mock_log, mock_exec, mock_schema, mock_gen):
        """정상 파이프라인: 스키마 조회 → SQL 생성 → 실행 → 요약."""
        # 스키마 요약
        mock_schema.return_value = "## 사용 가능한 테이블\n### 1. sales (100행)\n컬럼: id(BIGINT)"

        # LLM 응답 (SQL 생성 + 요약)
        mock_gen.side_effect = [
            "```sql\nSELECT SUM(amount) AS total FROM sales\n```",
            "총 매출은 1,234,000원입니다.",
        ]

        # SQL 실행 결과
        mock_exec.return_value = {
            "success": True,
            "data": [{"total": 1234000}],
            "row_count": 1,
            "truncated": False,
            "error": None,
            "sql": "SELECT SUM(amount) AS total FROM sales",
        }

        result = process("총 매출 보여줘")

        assert result["success"] is True
        assert result["agent"] == "sql"
        assert result["sql"] is not None
        assert "1,234,000" in result["answer"]

    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.log_action")
    def test_no_tables_available(self, mock_log, mock_schema, mock_gen):
        """테이블이 없으면 안내 메시지를 반환합니다."""
        mock_schema.return_value = "등록된 테이블이 없습니다."

        result = process("매출 보여줘")

        assert result["success"] is False
        assert "등록된 테이블이 없습니다" in result["answer"]

    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.log_action")
    def test_llm_connection_failure(self, mock_log, mock_schema, mock_gen):
        """LLM 연결 실패 시 에러 메시지를 반환합니다."""
        mock_schema.return_value = "## 테이블\n### sales"
        mock_gen.return_value = ""  # LLM 실패

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

    @patch("agent.sql_agent.generate")
    @patch("agent.sql_agent.get_all_tables_summary")
    @patch("agent.sql_agent.log_action")
    def test_invalid_sql_generated(self, mock_log, mock_schema, mock_gen):
        """LLM이 위험한 SQL을 생성하면 보안 검증에서 차단됩니다."""
        mock_schema.return_value = "## 테이블\n### sales"
        mock_gen.return_value = "```sql\nDROP TABLE sales\n```"

        result = process("테이블 삭제해줘")

        assert result["success"] is False
        assert "보안 검증" in result["answer"] or "SELECT" in result["answer"]
