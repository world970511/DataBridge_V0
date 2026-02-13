"""
문서 검색(RAG) 에이전트 모듈 — 자연어 질의로 문서를 검색하고 LLM이 답변을 생성.

사용자의 문서 관련 질의를 처리하는 에이전트입니다. 다음 파이프라인으로 동작합니다:

1. **문서 검색**: search_docs.search()로 ChromaDB에서 쿼리와 의미적으로 유사한
   문서 청크를 코사인 유사도 기반으로 검색합니다.
2. **컨텍스트 구성**: 검색된 청크들을 search_docs.format_search_results()로
   LLM에 전달하기 적합한 텍스트로 포맷팅합니다.
3. **RAG 응답 생성**: Ollama LLM에 검색된 문서 컨텍스트와 질의를 함께 전달하여
   근거 기반의 자연어 답변을 생성합니다 (Retrieval-Augmented Generation).

모든 단계에서 _audit.log_action()으로 감사 로그를 기록합니다.

의존 모듈:
    - agent._llm: generate() — Ollama LLM 호출
    - agent._audit: log_action() — 감사 로그 기록
    - agent.tools.search_docs: search(), format_search_results() — 문서 검색·포맷

사용 예시:
    from agent.doc_agent import process
    result = process("보고서에서 분기별 매출 동향을 알려줘")
    print(result["answer"])    # "보고서에 따르면 1분기 매출은..."
    print(result["sources"])   # [{"source": "report.pdf", "similarity": 0.87}, ...]
"""

import logging

from agent._llm import generate
from agent._audit import log_action
from agent.tools.search_docs import search, format_search_results

logger = logging.getLogger(__name__)

# RAG 응답 생성을 위한 시스템 프롬프트.
# 검색된 문서 컨텍스트를 참고하여 답변하도록 지시합니다.
_RAG_SYSTEM_PROMPT = """당신은 문서 분석 전문가입니다.
제공된 문서 내용을 바탕으로 사용자의 질문에 정확하게 답변합니다.

규칙:
- 제공된 문서 내용에 근거하여 답변합니다
- 문서에 없는 내용은 "문서에서 해당 정보를 찾지 못했습니다"라고 답변합니다
- 여러 문서에서 관련 정보가 있으면 종합하여 답변합니다
- 출처(문서명)를 가능하면 언급합니다
- 한국어로 자연스럽게 답변합니다
"""


def process(question: str, n_results: int = 5) -> dict:
    """
    자연어 질의로 문서를 검색하고 LLM으로 RAG 기반 답변을 생성하는 전체 파이프라인.

    처리 흐름:
    1. 사용자 질의를 audit_log에 기록 (action_type='query')
    2. ChromaDB에서 의미적으로 유사한 문서 청크 검색
    3. 검색 결과가 있으면 LLM에 컨텍스트와 함께 전달하여 답변 생성 (RAG)
    4. 검색 결과가 없으면 안내 메시지 반환
    5. 각 단계의 결과를 audit_log에 기록

    Args:
        question: 사용자의 자연어 문서 검색 질의.
                  예: "보고서에서 주요 리스크 요인을 정리해 줘"
        n_results: ChromaDB에서 검색할 최대 청크 수. 기본값 5.
                   많을수록 컨텍스트가 풍부해지지만 LLM 토큰 사용량이 증가합니다.

    Returns:
        처리 결과 딕셔너리:
        {
            "success": bool,         — 전체 파이프라인 성공 여부
            "answer": str,           — 사용자에게 표시할 자연어 응답
            "sources": list[dict],   — 참조된 문서 출처 정보
                                       [{"source": "파일명", "similarity": 0.87}, ...]
            "search_count": int,     — 검색된 문서 청크 수
            "agent": "document",     — 처리한 에이전트 식별자
        }
    """
    # 1. 질의 접수 로그
    log_action(action_type="query", query_text=question)

    # 2. ChromaDB 문서 검색
    results = search(query=question, n_results=n_results)

    log_action(
        action_type="doc_search",
        query_text=question,
        status="success" if results else "failed",
        metadata={"search_count": len(results)},
    )

    if not results:
        return {
            "success": True,
            "answer": "관련 문서를 찾지 못했습니다. 문서가 아직 업로드되지 않았거나, "
                      "질문과 관련된 내용이 저장된 문서에 없을 수 있습니다.",
            "sources": [],
            "search_count": 0,
            "agent": "document",
        }

    # 3. 검색 결과를 텍스트로 포맷팅
    context_text = format_search_results(results)

    # 4. LLM으로 RAG 응답 생성
    rag_prompt = (
        f"다음은 검색된 관련 문서 내용입니다:\n\n"
        f"{context_text}\n\n"
        f"위 문서를 참고하여 다음 질문에 답변해 주세요:\n{question}"
    )

    answer = generate(
        prompt=rag_prompt,
        system=_RAG_SYSTEM_PROMPT,
        temperature=0.3,
    )

    if not answer:
        # LLM 호출 실패 시 검색 결과만 직접 제공
        answer = (
            "LLM 응답 생성에 실패했습니다. 검색된 문서 내용을 직접 확인해 주세요:\n\n"
            f"{context_text}"
        )

    # 5. 출처 정보 추출
    sources = _extract_sources(results)

    log_action(
        action_type="doc_answer",
        query_text=question,
        result_summary=answer[:500],
        status="success",
        metadata={"sources": [s["source"] for s in sources]},
    )

    return {
        "success": True,
        "answer": answer,
        "sources": sources,
        "search_count": len(results),
        "agent": "document",
    }


def _extract_sources(results: list[dict]) -> list[dict]:
    """
    검색 결과에서 출처(source)와 유사도(similarity) 정보를 추출하여 중복 제거.

    동일한 source 파일에서 여러 청크가 검색될 수 있으므로, source 기준으로
    중복을 제거하고 가장 높은 유사도를 대표값으로 사용합니다.

    Args:
        results: search() 함수가 반환한 딕셔너리 리스트.

    Returns:
        중복 제거된 출처 정보 리스트. 유사도 내림차순 정렬.
        예: [{"source": "report.pdf", "similarity": 0.87}, ...]
    """
    source_map: dict[str, float] = {}

    for result in results:
        metadata = result.get("metadata", {})
        source = metadata.get("source", "알 수 없음")
        distance = result.get("distance")

        similarity = max(0.0, 1.0 - distance) if distance is not None else 0.0

        # 동일 출처에서 가장 높은 유사도를 유지
        if source not in source_map or similarity > source_map[source]:
            source_map[source] = similarity

    sources = [
        {"source": src, "similarity": round(sim, 2)}
        for src, sim in source_map.items()
    ]

    # 유사도 내림차순 정렬
    sources.sort(key=lambda x: x["similarity"], reverse=True)

    return sources
