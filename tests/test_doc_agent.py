"""
문서 에이전트 모듈 테스트.

agent/doc_agent.py의 process() 파이프라인과 내부 함수(_extract_sources)를
mock 기반으로 테스트합니다. 실제 ChromaDB나 LLM 호출 없이 동작을 검증합니다.

실행:
    pytest tests/test_doc_agent.py -v -m unit
"""

from unittest.mock import patch

import pytest

from agent.doc_agent import process, _extract_sources


# ============================================
# _extract_sources() 테스트
# ============================================

@pytest.mark.unit
class TestExtractSources:
    """_extract_sources() — 출처 정보 추출 및 중복 제거 테스트."""

    def test_extracts_sources(self, sample_search_results):
        """검색 결과에서 출처 정보를 추출합니다."""
        sources = _extract_sources(sample_search_results)
        assert len(sources) >= 1
        assert any(s["source"] == "report.pdf" for s in sources)

    def test_deduplicates_sources(self, sample_search_results):
        """동일 출처의 중복을 제거합니다 (report.pdf가 2번 나옴)."""
        sources = _extract_sources(sample_search_results)
        source_names = [s["source"] for s in sources]
        assert source_names.count("report.pdf") == 1

    def test_keeps_highest_similarity(self, sample_search_results):
        """동일 출처에서 가장 높은 유사도를 유지합니다."""
        sources = _extract_sources(sample_search_results)
        report_source = next(s for s in sources if s["source"] == "report.pdf")
        # distance 0.15 → similarity 0.85
        assert report_source["similarity"] >= 0.85

    def test_sorted_by_similarity(self, sample_search_results):
        """출처가 유사도 내림차순으로 정렬됩니다."""
        sources = _extract_sources(sample_search_results)
        if len(sources) >= 2:
            assert sources[0]["similarity"] >= sources[1]["similarity"]

    def test_empty_results(self):
        """빈 검색 결과는 빈 리스트를 반환합니다."""
        assert _extract_sources([]) == []


# ============================================
# process() 파이프라인 테스트
# ============================================

@pytest.mark.unit
class TestDocAgentProcess:
    """process() — 문서 에이전트 전체 파이프라인 mock 테스트."""

    @patch("agent.doc_agent.generate")
    @patch("agent.doc_agent.search")
    @patch("agent.doc_agent.log_action")
    def test_successful_pipeline(self, mock_log, mock_search, mock_gen):
        """정상 파이프라인: 검색 → RAG 응답 생성."""
        mock_search.return_value = [
            {
                "text": "Q1 매출은 15% 증가했습니다.",
                "metadata": {"source": "report.pdf", "chunk_index": 0},
                "distance": 0.15,
            }
        ]
        mock_gen.return_value = "보고서에 따르면 Q1 매출은 15% 증가했습니다."

        result = process("분기별 매출 동향 알려줘")

        assert result["success"] is True
        assert result["agent"] == "document"
        assert "매출" in result["answer"]
        assert len(result["sources"]) >= 1

    @patch("agent.doc_agent.search")
    @patch("agent.doc_agent.log_action")
    def test_no_search_results(self, mock_log, mock_search):
        """검색 결과가 없으면 안내 메시지를 반환합니다."""
        mock_search.return_value = []

        result = process("존재하지 않는 내용 검색")

        assert result["success"] is True
        assert result["search_count"] == 0
        assert "찾지 못했습니다" in result["answer"]

    @patch("agent.doc_agent.generate")
    @patch("agent.doc_agent.search")
    @patch("agent.doc_agent.log_action")
    def test_llm_failure_fallback(self, mock_log, mock_search, mock_gen):
        """LLM 실패 시 검색 결과를 직접 제공합니다."""
        mock_search.return_value = [
            {
                "text": "테스트 내용",
                "metadata": {"source": "test.pdf"},
                "distance": 0.1,
            }
        ]
        mock_gen.return_value = ""  # LLM 실패

        result = process("테스트 질의")

        assert result["success"] is True
        assert "테스트 내용" in result["answer"]

    @patch("agent.doc_agent.generate")
    @patch("agent.doc_agent.search")
    @patch("agent.doc_agent.log_action")
    def test_result_structure(self, mock_log, mock_search, mock_gen):
        """반환 딕셔너리에 필수 키가 모두 포함됩니다."""
        mock_search.return_value = [
            {
                "text": "content",
                "metadata": {"source": "doc.pdf"},
                "distance": 0.2,
            }
        ]
        mock_gen.return_value = "답변"

        result = process("테스트")

        required_keys = {"success", "answer", "sources", "search_count", "agent"}
        assert required_keys.issubset(result.keys())
