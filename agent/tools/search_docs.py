"""
ChromaDB 문서 검색 도구.

rag.embedder 모듈을 래핑하여 AI 에이전트(특히 문서 에이전트)가 사용하기 적합한
인터페이스로 시맨틱 문서 검색을 제공합니다. 검색 결과를 LLM에 전달하기 좋은
텍스트 형식으로 포맷팅하는 기능도 포함합니다.

주요 함수:
    search(query, n_results) -> list[dict]
        - 쿼리 텍스트와 의미적으로 유사한 문서 청크를 ChromaDB에서 검색.
    format_search_results(results) -> str
        - 검색 결과를 LLM 컨텍스트로 삽입하기 적합한 텍스트로 포맷팅.
    get_document_names() -> list[str]
        - 카탈로그에 등록된 문서명 리스트. 의도 분류 시 사용.

의존 모듈:
    - rag.embedder: search_documents() — ChromaDB 코사인 유사도 검색
    - catalog.catalog: list_documents() — 문서 카탈로그 조회

사용 예시:
    from agent.tools.search_docs import search, format_search_results

    results = search("분기별 매출 동향", n_results=3)
    context_text = format_search_results(results)
    # "[문서 1] (출처: report.pdf, 유사도: 0.87)\\n분기별 매출은..."
"""

import logging

from rag.embedder import search_documents
from catalog.catalog import list_documents

logger = logging.getLogger(__name__)


def search(query: str, n_results: int = 5) -> list[dict]:
    """
    쿼리 텍스트와 의미적으로 유사한 문서 청크를 ChromaDB에서 검색하여 반환.

    rag.embedder.search_documents()를 래핑하며, ChromaDB 내장 Sentence Transformers
    (all-MiniLM-L6-v2)가 쿼리를 자동 임베딩하여 코사인 유사도 기반 HNSW 인덱스로 검색합니다.

    ChromaDB 연결 실패 등 예외 발생 시 빈 리스트를 반환하여 에이전트 흐름이 중단되지 않도록 합니다.

    Args:
        query: 자연어 검색 쿼리 텍스트. 예: "분기별 매출 동향 분석"
        n_results: 반환할 최대 결과 수. 기본값 5. ChromaDB에 저장된 청크 수보다
                   크면 가능한 만큼만 반환됩니다.

    Returns:
        검색 결과 딕셔너리 리스트. 각 항목의 구조:
        {
            "text": str,       — 매칭된 문서 청크의 텍스트 내용
            "metadata": dict,  — 출처(source), 청크 인덱스(chunk_index) 등 메타데이터
            "distance": float, — 코사인 거리 (0에 가까울수록 유사)
        }
        검색 실패 시 빈 리스트.
    """
    try:
        results = search_documents(
            query=query,
            collection_name="documents",
            n_results=n_results,
        )
        logger.debug(f"Document search: query='{query[:50]}...', results={len(results)}")
        return results

    except Exception as e:
        logger.error(f"Document search failed: {e}")
        return []


def format_search_results(results: list[dict]) -> str:
    """
    ChromaDB 검색 결과를 LLM 컨텍스트에 삽입하기 적합한 텍스트로 포맷팅.

    각 결과를 번호 매기기로 표시하며, 출처(source)와 유사도(similarity)를 함께 기록합니다.
    코사인 거리(distance)는 유사도(1 - distance)로 변환하여 직관적으로 표시합니다.
    결과가 없으면 안내 메시지를 반환합니다.

    Args:
        results: search() 함수가 반환한 딕셔너리 리스트.

    Returns:
        포맷팅된 텍스트. 예:
        "[문서 1] (출처: report.pdf, 유사도: 0.87)
         분기별 매출은 전년 대비 15% 증가하였으며...

         [문서 2] (출처: analysis.pdf, 유사도: 0.82)
         주요 성장 요인으로는..."

        결과가 없으면 "관련 문서를 찾지 못했습니다."
    """
    if not results:
        return "관련 문서를 찾지 못했습니다."

    parts = []
    for idx, result in enumerate(results, 1):
        text = result.get("text", "")
        metadata = result.get("metadata", {})
        distance = result.get("distance")

        source = metadata.get("source", "알 수 없음")

        # 코사인 거리 → 유사도 변환 (distance=0이면 similarity=1.0)
        if distance is not None:
            similarity = max(0.0, 1.0 - distance)
            similarity_text = f", 유사도: {similarity:.2f}"
        else:
            similarity_text = ""

        parts.append(
            f"[문서 {idx}] (출처: {source}{similarity_text})\n{text}"
        )

    return "\n\n".join(parts)


def get_document_names() -> list[str]:
    """
    카탈로그에 등록된 모든 문서명을 문자열 리스트로 반환.

    의도 분류(orchestrator) 단계에서 사용자 질의에 문서명이 포함되어 있는지
    매칭할 때 활용됩니다. catalog.list_documents()의 결과에서 doc_name 필드만 추출합니다.

    Returns:
        문서명 문자열 리스트. 예: ["report.pdf", "guide.docx"].
        카탈로그가 비어 있거나 조회 실패 시 빈 리스트.
    """
    try:
        docs = list_documents()
        return [d["doc_name"] for d in docs if "doc_name" in d]
    except Exception as e:
        logger.error(f"Failed to get document names: {e}")
        return []
