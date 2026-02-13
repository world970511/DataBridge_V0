"""
오케스트레이터 모듈 테스트.

agent/orchestrator.py의 classify_intent(), process_query() 함수를
mock 기반으로 테스트합니다. 의도 분류 로직(키워드 점수, 카탈로그 매칭, LLM 폴백)과
에이전트 라우팅을 검증합니다.

실행:
    pytest tests/test_orchestrator.py -v -m unit
"""

from unittest.mock import patch

import pytest

from agent.orchestrator import classify_intent, process_query


# ============================================
# classify_intent() 테스트
# ============================================

@pytest.mark.unit
class TestClassifyIntent:
    """classify_intent() — 의도 분류 테스트."""

    @patch("agent.orchestrator.get_table_names", return_value=["sales", "products"])
    @patch("agent.orchestrator.get_document_names", return_value=[])
    def test_data_keywords(self, mock_docs, mock_tables):
        """데이터 관련 키워드가 많으면 'data'로 분류됩니다."""
        intent = classify_intent("sales 테이블에서 총 매출 합계 조회해줘")
        assert intent == "data"

    @patch("agent.orchestrator.get_table_names", return_value=[])
    @patch("agent.orchestrator.get_document_names", return_value=["report.pdf"])
    def test_document_keywords(self, mock_docs, mock_tables):
        """문서 관련 키워드가 많으면 'document'로 분류됩니다."""
        intent = classify_intent("report 문서에서 보고서 내용 검색해줘")
        assert intent == "document"

    @patch("agent.orchestrator.get_table_names", return_value=["sales"])
    @patch("agent.orchestrator.get_document_names", return_value=[])
    def test_table_name_match_strong_signal(self, mock_docs, mock_tables):
        """카탈로그 테이블명이 질의에 포함되면 강력한 데이터 신호입니다."""
        intent = classify_intent("sales 보여줘")
        assert intent == "data"

    @patch("agent.orchestrator.get_table_names", return_value=[])
    @patch("agent.orchestrator.get_document_names", return_value=["guide.docx"])
    def test_document_name_match_strong_signal(self, mock_docs, mock_tables):
        """카탈로그 문서명이 질의에 포함되면 강력한 문서 신호입니다."""
        intent = classify_intent("guide 문서 요약해줘")
        assert intent == "document"

    @patch("agent.orchestrator.get_table_names", return_value=[])
    @patch("agent.orchestrator.get_document_names", return_value=[])
    @patch("agent.orchestrator.generate")
    def test_llm_fallback_data(self, mock_gen, mock_docs, mock_tables):
        """키워드 점수가 모호하면 LLM 폴백으로 분류합니다."""
        mock_gen.return_value = "DATA"
        intent = classify_intent("이번 달 현황 알려줘")
        # LLM이 DATA라고 응답하므로 data
        assert intent == "data"

    @patch("agent.orchestrator.get_table_names", return_value=[])
    @patch("agent.orchestrator.get_document_names", return_value=[])
    @patch("agent.orchestrator.generate")
    def test_llm_fallback_document(self, mock_gen, mock_docs, mock_tables):
        """LLM이 DOCUMENT라고 응답하면 document로 분류합니다."""
        mock_gen.return_value = "DOCUMENT"
        intent = classify_intent("뭔가 알려줘")
        assert intent == "document"

    @patch("agent.orchestrator.get_table_names", return_value=[])
    @patch("agent.orchestrator.get_document_names", return_value=[])
    @patch("agent.orchestrator.generate")
    def test_llm_fallback_composite(self, mock_gen, mock_docs, mock_tables):
        """LLM이 BOTH라고 응답하면 composite로 분류합니다."""
        mock_gen.return_value = "BOTH"
        intent = classify_intent("데이터와 문서 모두 보여줘")
        assert intent == "composite"

    @patch("agent.orchestrator.get_table_names", return_value=[])
    @patch("agent.orchestrator.get_document_names", return_value=[])
    @patch("agent.orchestrator.generate")
    def test_llm_failure_defaults_to_data(self, mock_gen, mock_docs, mock_tables):
        """LLM 호출 실패 시 기본값 'data'를 반환합니다."""
        mock_gen.return_value = ""
        intent = classify_intent("뭔가 해줘")
        assert intent == "data"


# ============================================
# process_query() 라우팅 테스트
# ============================================

@pytest.mark.unit
class TestProcessQuery:
    """process_query() — 의도 분류 후 에이전트 라우팅 테스트."""

    @patch("agent.orchestrator.classify_intent", return_value="data")
    @patch("agent.orchestrator.sql_agent")
    @patch("agent.orchestrator.log_action")
    def test_routes_to_sql_agent(self, mock_log, mock_sql, mock_classify):
        """'data' 의도는 SQL 에이전트로 라우팅됩니다."""
        mock_sql.process.return_value = {
            "success": True,
            "answer": "매출 합계: 1000",
            "sql": "SELECT SUM(amount) FROM sales",
            "data": [{"total": 1000}],
            "row_count": 1,
            "truncated": False,
            "agent": "sql",
        }

        result = process_query("매출 합계 보여줘")

        mock_sql.process.assert_called_once()
        assert result["intent"] == "data"
        assert result["agent"] == "sql"

    @patch("agent.orchestrator.classify_intent", return_value="document")
    @patch("agent.orchestrator.doc_agent")
    @patch("agent.orchestrator.log_action")
    def test_routes_to_doc_agent(self, mock_log, mock_doc, mock_classify):
        """'document' 의도는 문서 에이전트로 라우팅됩니다."""
        mock_doc.process.return_value = {
            "success": True,
            "answer": "보고서에 따르면...",
            "sources": [{"source": "report.pdf", "similarity": 0.85}],
            "search_count": 3,
            "agent": "document",
        }

        result = process_query("보고서 내용 알려줘")

        mock_doc.process.assert_called_once()
        assert result["intent"] == "document"
        assert result["agent"] == "document"

    @patch("agent.orchestrator.classify_intent", return_value="composite")
    @patch("agent.orchestrator.sql_agent")
    @patch("agent.orchestrator.doc_agent")
    @patch("agent.orchestrator.log_action")
    def test_routes_composite(self, mock_log, mock_doc, mock_sql, mock_classify):
        """'composite' 의도는 두 에이전트 모두 호출합니다."""
        mock_sql.process.return_value = {
            "success": True,
            "answer": "데이터 결과",
            "agent": "sql",
        }
        mock_doc.process.return_value = {
            "success": True,
            "answer": "문서 결과",
            "agent": "document",
        }

        result = process_query("데이터와 문서 모두")

        mock_sql.process.assert_called_once()
        mock_doc.process.assert_called_once()
        assert result["intent"] == "composite"
        assert "데이터" in result["answer"]
        assert "문서" in result["answer"]

    @patch("agent.orchestrator.classify_intent", return_value="data")
    @patch("agent.orchestrator.sql_agent")
    @patch("agent.orchestrator.log_action")
    def test_result_includes_intent(self, mock_log, mock_sql, mock_classify):
        """반환 딕셔너리에 'intent' 키가 포함됩니다."""
        mock_sql.process.return_value = {
            "success": True,
            "answer": "결과",
            "agent": "sql",
        }

        result = process_query("테스트")
        assert "intent" in result
