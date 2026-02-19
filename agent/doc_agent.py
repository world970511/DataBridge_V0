"""
문서 검색(RAG) 에이전트 모듈 — 2-Tier 하이브리드 방식으로 문서를 검색하고 답변 생성.

사용자의 문서 관련 질의를 처리하는 에이전트입니다. 다음 파이프라인으로 동작합니다:

1. **Tier 1 검색**: ChromaDB에 저장된 문서 요약 임베딩에서 관련 문서를 식별합니다.
2. **Tier 2 온디맨드 파싱**: 식별된 문서의 원본 파일을 전체 파싱하여
   LLM 컨텍스트로 사용합니다 (500자 청크가 아닌 전체 텍스트).
3. **RAG 응답 생성**: 전체 문서 텍스트와 질의를 LLM에 전달하여
   근거 기반의 자연어 답변을 생성합니다.

**Lazy Loading**: 대용량 문서(MAX_EMBED_SIZE_MB 초과)는 업로드 시 임베딩을 생략하고
최소 메타데이터만 저장합니다 (chunk_count=0). 질의 시 전체 텍스트를 온디맨드로 파싱합니다.

파일이 디스크에서 삭제된 경우 요약 텍스트를 폴백으로 사용합니다.

의존 모듈:
    - agent._llm: generate() — Ollama LLM 호출
    - agent._audit: log_action() — 감사 로그 기록
    - agent.tools.search_docs: search(), format_search_results() — 문서 검색·포맷
    - catalog.catalog: get_document_by_name() — 문서 경로 조회
    - watcher.loader.document_loader: extract_text() — 온디맨드 텍스트 추출

사용 예시:
    from agent.doc_agent import process
    result = process("보고서에서 분기별 매출 동향을 알려줘")
    print(result["answer"])    # "보고서에 따르면 1분기 매출은..."
    print(result["sources"])   # [{"source": "report.pdf", "similarity": 0.87}, ...]
"""

import logging
from pathlib import Path

from agent._llm import generate
from agent._audit import log_action
from agent.tools.search_docs import search, format_search_results

logger = logging.getLogger(__name__)

# RAG 응답 생성을 위한 시스템 프롬프트.
_RAG_SYSTEM_PROMPT = """당신은 문서 분석 전문가입니다.
제공된 문서 내용을 바탕으로 사용자의 질문에 정확하게 답변합니다.

규칙:
- 제공된 문서 내용에 근거하여 답변합니다
- 문서에 없는 내용은 "문서에서 해당 정보를 찾지 못했습니다"라고 답변합니다
- 여러 문서에서 관련 정보가 있으면 종합하여 답변합니다
- 출처(문서명)를 가능하면 언급합니다
- 한국어로 자연스럽게 답변합니다
"""

# 온디맨드 파싱 시 LLM에 전달할 문서당 최대 문자 수
_MAX_FULL_TEXT_CHARS = 5000

# 온디맨드 파싱할 최대 문서 수 (토큰 절약)
_MAX_DOCUMENTS = 3


def process(question: str, n_results: int = 5) -> dict:
    """
    Tier 2: 요약 검색으로 관련 문서를 식별한 뒤, 원본 파일을 온디맨드 파싱하여 RAG 응답 생성.

    처리 흐름:
    1. ChromaDB에서 요약 임베딩 검색 → 관련 문서 식별
    2. 각 문서의 원본 파일을 온디맨드 파싱하여 전체 텍스트 추출
    3. 전체 텍스트를 LLM에 전달하여 RAG 기반 답변 생성
    4. 파일 부재 시 요약 텍스트 폴백

    Args:
        question: 사용자의 자연어 문서 검색 질의.
        n_results: ChromaDB에서 검색할 최대 청크 수. 기본값 5.

    Returns:
        처리 결과 딕셔너리:
        {
            "success": bool,         — 전체 파이프라인 성공 여부
            "answer": str,           — 사용자에게 표시할 자연어 응답
            "sources": list[dict],   — 참조된 문서 출처 정보
            "search_count": int,     — 검색된 문서 청크 수
            "agent": "document",     — 처리한 에이전트 식별자
        }
    """
    # 1. 질의 접수 로그
    log_action(action_type="query", query_text=question)

    # 2. ChromaDB 요약 임베딩 검색 (Tier 1 결과 활용)
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

    # 3. 고유 문서 식별 (중복 제거, 유사도 내림차순)
    unique_sources = _extract_unique_sources(results)

    # 4. 온디맨드 파싱: 각 문서의 전체 텍스트 추출
    full_texts = []
    for source_info in unique_sources[:_MAX_DOCUMENTS]:
        file_text = _load_full_text(source_info["source"])
        if file_text:
            full_texts.append({
                "source": source_info["source"],
                "text": file_text[:_MAX_FULL_TEXT_CHARS],
                "similarity": source_info["similarity"],
            })

    # 5. 컨텍스트 구성 (전체 텍스트 또는 요약 폴백)
    if full_texts:
        context_text = _format_full_texts(full_texts)
    else:
        # 파일이 모두 삭제된 경우, 요약 텍스트라도 사용
        logger.warning("All source files missing, falling back to summary text")
        context_text = format_search_results(results)

    # 6. LLM으로 RAG 응답 생성
    rag_prompt = (
        f"다음은 검색된 관련 문서 내용입니다:\n\n"
        f"{context_text}\n\n"
        f"위 문서를 참고하여 다음 질문에 답변해 주세요:\n{question}"
    )

    answer = generate(
        prompt=rag_prompt,
        system=_RAG_SYSTEM_PROMPT,
        purpose="agent",  # 에이전트용 모델 (문서 내용 포함)
        temperature=0.3,
    )

    if not answer:
        answer = (
            "LLM 응답 생성에 실패했습니다. 검색된 문서 내용을 직접 확인해 주세요:\n\n"
            f"{context_text}"
        )

    # 7. 출처 정보
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


def _extract_unique_sources(results: list[dict]) -> list[dict]:
    """
    검색 결과에서 중복 없는 source 파일 목록을 추출하여 유사도 내림차순 정렬.

    동일 source에서 여러 청크가 검색될 수 있으므로 가장 높은 유사도를 대표값으로 사용합니다.

    Returns: [{"source": 파일명, "similarity": 유사도}, ...] 리스트.
    """
    source_map: dict[str, float] = {}

    for result in results:
        metadata = result.get("metadata", {})
        source = metadata.get("source", "")
        if not source:
            continue
        distance = result.get("distance")
        similarity = max(0.0, 1.0 - distance) if distance is not None else 0.0

        if source not in source_map or similarity > source_map[source]:
            source_map[source] = similarity

    sources = [
        {"source": src, "similarity": sim}
        for src, sim in source_map.items()
    ]
    sources.sort(key=lambda x: x["similarity"], reverse=True)
    return sources


def _load_full_text(source_name: str) -> str:
    """
    문서 파일명으로 카탈로그에서 경로를 조회하고 전체 텍스트를 온디맨드 추출.

    Lazy Loading된 문서(chunk_count=0)의 경우에도 동일하게 동작하며,
    이 시점에 전체 텍스트가 처음으로 파싱됩니다.

    카탈로그에 문서가 없거나 파일이 디스크에 존재하지 않으면 빈 문자열을 반환합니다.

    Args:
        source_name: 문서 파일명 (ChromaDB metadata의 source 값).

    Returns: 전체 문서 텍스트 문자열. 실패 시 빈 문자열.
    """
    try:
        from catalog.catalog import get_document_by_name
        from watcher.loader.document_loader import extract_text

        doc_info = get_document_by_name(source_name)
        if not doc_info:
            logger.warning(f"Document not found in catalog: {source_name}")
            return ""

        file_path = doc_info.get("source_file", "")
        file_type = doc_info.get("file_type", "")
        chunk_count = doc_info.get("chunk_count", 0)

        if not file_path or not Path(file_path).is_file():
            logger.warning(f"Source file missing from disk: {file_path}")
            return ""

        # Lazy Loading 문서인 경우 로깅
        if chunk_count == 0:
            logger.info(f"Lazy-loaded document '{source_name}': starting on-demand parsing")

        text = extract_text(file_path, file_type)
        logger.debug(f"On-demand parsed '{source_name}': {len(text)} chars")
        return text

    except Exception as e:
        logger.error(f"Failed to load full text for '{source_name}': {e}")
        return ""


def _format_full_texts(full_texts: list[dict]) -> str:
    """
    온디맨드 파싱된 전체 텍스트를 LLM 컨텍스트 형식으로 포맷팅.

    Args:
        full_texts: [{"source": 파일명, "text": 텍스트, "similarity": 유사도}] 리스트.

    Returns: 포맷팅된 텍스트 문자열.
    """
    parts = []
    for idx, item in enumerate(full_texts, 1):
        similarity = item.get("similarity", 0)
        parts.append(
            f"[문서 {idx}] (출처: {item['source']}, 유사도: {similarity:.2f})\n"
            f"{item['text']}"
        )
    return "\n\n".join(parts)


def _extract_sources(results: list[dict]) -> list[dict]:
    """
    검색 결과에서 출처(source)와 유사도(similarity) 정보를 추출하여 중복 제거.

    동일한 source 파일에서 여러 청크가 검색될 수 있으므로, source 기준으로
    중복을 제거하고 가장 높은 유사도를 대표값으로 사용합니다.

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

        if source not in source_map or similarity > source_map[source]:
            source_map[source] = similarity

    sources = [
        {"source": src, "similarity": round(sim, 2)}
        for src, sim in source_map.items()
    ]

    sources.sort(key=lambda x: x["similarity"], reverse=True)

    return sources
