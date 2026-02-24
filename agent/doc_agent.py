"""
문서 검색(RAG) 에이전트 모듈 — 캐시 기반 2-Tier 하이브리드 검색 + 답변 생성.

사용자의 문서 관련 질의를 처리하는 에이전트입니다. 다음 파이프라인으로 동작합니다:

1. **Tier 1 검색**: ChromaDB에 저장된 요약 임베딩에서 관련 문서를 식별합니다.
2. **Tier 2 청크 선별**: PostgreSQL에 캐시된 원문 청크를 로드하고
   TF-IDF 코사인 유사도로 질의와 관련된 청크를 선별합니다.
   (파일 재파싱 불필요, 문서 내 위치를 정밀하게 찾음)
3. **RAG 응답 생성**: 선별된 청크와 질의를 LLM에 전달하여
   근거 기반의 자연어 답변을 생성합니다.

**하위 호환**: 캐시되지 않은 기존 문서는 첫 질의 시 자동으로 파싱+캐싱됩니다.

의존 모듈:
    - agent._llm: generate() — LLM 호출
    - agent._audit: log_action() — 감사 로그 기록
    - agent.tools.search_docs: search(), format_search_results() — 문서 검색·포맷
    - catalog.catalog: get_document_by_name(), get_document_chunks() — 문서/청크 조회
    - sklearn: TfidfVectorizer, cosine_similarity — 청크 재순위화
"""

import logging
from collections import defaultdict
from pathlib import Path

from agent._llm import generate
from agent._audit import log_action
from agent.tools.search_docs import search, format_search_results

logger = logging.getLogger(__name__)

# RAG 응답 생성을 위한 시스템 프롬프트.
_RAG_SYSTEM_PROMPT = """You are a document analysis expert.
Answer the user's question accurately based on the provided document content.

Rules:
- Base your answers on the provided document content
- If the information is not found in the documents, state "The requested information was not found in the documents"
- If relevant information exists across multiple documents, synthesize them into a comprehensive answer
- Mention the source (document name) whenever possible
"""

# LLM에 전달할 최대 컨텍스트 문자 수 (관련 청크만 선별하므로 기존 5000 → 8000)
_MAX_CONTEXT_CHARS = 8000

# 재순위화 후 LLM에 전달할 최대 청크 수
_TOP_K_CHUNKS = 10

# 검색할 최대 문서 수
_MAX_DOCUMENTS = 3


def _find_exact_match_document(question: str) -> str | None:
    """
    질의에 정확한 문서 파일명이 포함되어 있으면 해당 파일명을 반환.

    카탈로그에 등록된 문서명과 대조하여 질의 텍스트에 파일명(확장자 포함/제거)이
    포함되어 있으면 해당 문서를 직접 타겟으로 식별합니다.
    """
    from agent.tools.search_docs import get_document_names

    question_lower = question.lower()
    doc_names = get_document_names()

    for name in sorted(doc_names, key=len, reverse=True):
        if name.lower() in question_lower:
            return name
        base_name = name.rsplit(".", 1)[0] if "." in name else name
        if base_name.lower() in question_lower:
            return name

    return None


def process(question: str, n_results: int = 5) -> dict:
    """
    캐시 기반 RAG: 요약 검색 → 캐시 청크 로드 → TF-IDF 재순위화 → LLM 응답.

    처리 흐름:
    1. ChromaDB에서 요약 임베딩 검색 → 관련 문서 식별
    2. PostgreSQL에서 캐시된 원문 청크 로드 (재파싱 없음)
    3. TF-IDF 코사인 유사도로 질의 관련 청크 선별
    4. 선별된 청크를 LLM에 전달하여 RAG 기반 답변 생성
    """
    # 1. 질의 접수 로그
    log_action(action_type="query", query_text=question)

    # 2-A. 정확 매칭 숏컷: 질의에 파일명이 포함되어 있으면 해당 문서만 처리
    exact_match = _find_exact_match_document(question)
    if exact_match:
        logger.info(f"Exact document match found: '{exact_match}', skipping ChromaDB search")
        ranked_chunks = _load_relevant_chunks(exact_match, question)
        if ranked_chunks:
            for c in ranked_chunks:
                c["source"] = exact_match
                c["similarity"] = 1.0
            context_text = _format_chunk_context(ranked_chunks)
            results = [{"metadata": {"source": exact_match}, "distance": 0.0, "text": ""}]
        else:
            logger.warning(f"No chunks found for exact match '{exact_match}', falling back to search")
            exact_match = None

    # 2-B. 일반 경로: ChromaDB 시맨틱 검색
    if not exact_match:
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
                "answer": "관련 문서를 찾지 못했습니다. 아직 문서가 업로드되지 않았거나, "
                          "저장된 문서에 질문과 관련된 내용이 없을 수 있습니다.",
                "sources": [],
                "search_count": 0,
                "agent": "document",
            }

        # 3. 고유 문서 식별 (중복 제거, 유사도 내림차순)
        unique_sources = _extract_unique_sources(results)

        # 4. 캐시 청크 로드 + TF-IDF 재순위화
        all_ranked_chunks = []
        for source_info in unique_sources[:_MAX_DOCUMENTS]:
            ranked = _load_relevant_chunks(source_info["source"], question)
            for chunk in ranked:
                chunk["source"] = source_info["source"]
                chunk["similarity"] = source_info["similarity"]
            all_ranked_chunks.extend(ranked)

        # 5. 컨텍스트 예산 내에서 최고 점수 청크 선별
        if all_ranked_chunks:
            # 전체 문서에서 점수 높은 순으로 선택
            all_ranked_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
            selected = []
            total_chars = 0
            for chunk in all_ranked_chunks:
                if total_chars + len(chunk["text"]) > _MAX_CONTEXT_CHARS:
                    break
                selected.append(chunk)
                total_chars += len(chunk["text"])

            context_text = _format_chunk_context(selected) if selected else format_search_results(results)
        else:
            logger.warning("No cached chunks found, falling back to summary text")
            context_text = format_search_results(results)

    # 6. LLM으로 RAG 응답 생성
    rag_prompt = (
        f"Below is the content of related documents found:\n\n"
        f"{context_text}\n\n"
        f"Based on the documents above, please answer the following question:\n{question}"
    )

    answer = generate(
        prompt=rag_prompt,
        system=_RAG_SYSTEM_PROMPT,
        purpose="agent",
        temperature=0.3,
    )

    if not answer:
        answer = (
            "LLM 응답을 생성하지 못했습니다. 검색된 문서 내용을 직접 확인해 주세요:\n\n"
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


# ============================================
# 캐시 청크 로드 + TF-IDF 재순위화
# ============================================

def _load_relevant_chunks(source_name: str, query: str) -> list[dict]:
    """
    PostgreSQL에서 캐시된 청크를 로드하고 TF-IDF로 질의 관련성 순으로 재순위화.

    캐시된 청크가 없으면 온디맨드로 파싱하여 캐시한 뒤 재순위화합니다 (lazy caching).
    document_chunks 테이블이 없거나 조회 실패 시에도 온디맨드 파싱으로 폴백합니다.

    Returns:
        [{"text": str, "chunk_index": int, "score": float}, ...] 관련성 내림차순.
    """
    from catalog.catalog import get_document_by_name, get_document_chunks

    doc_info = get_document_by_name(source_name)
    if not doc_info:
        logger.warning(f"Document not found in catalog: {source_name}")
        return []

    doc_id = doc_info.get("id")
    if not doc_id:
        return []

    # 캐시된 청크 로드 (테이블 미존재 등 예외 시 빈 리스트로 폴백)
    chunks = []
    try:
        chunks = get_document_chunks(doc_id)
    except Exception as e:
        logger.warning(f"Failed to load cached chunks (table may not exist): {e}")

    # 캐시가 없으면 온디맨드 파싱 + 캐싱 (하위 호환)
    if not chunks:
        chunks = _parse_and_cache_chunks(doc_info)

    if not chunks:
        return []

    return _rerank_chunks_tfidf(chunks, query)


def _is_low_quality_chunk(text: str) -> bool:
    """
    정보 밀도가 낮은 가비지 청크 판별.

    PDF 레이아웃 아티팩트(반복 단어, 목차 점선 등)를 필터링합니다.
    - 고유 단어 비율 < 20% (예: "조달청" 24회 반복)
    - 구두점/특수문자가 전체의 50% 이상 (예: "· · · · · ·")
    - 실제 텍스트가 30자 미만
    """
    import re

    stripped = text.strip()
    if len(stripped) < 30:
        return True

    # 구두점·특수문자 비율
    non_content = len(re.findall(r"[·\.\-\s\*\+\=\|\(\)\[\]\{\}…]", stripped))
    if non_content / len(stripped) > 0.5:
        return True

    # 고유 단어 비율 (단어 반복 감지)
    words = stripped.split()
    if len(words) >= 5:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.2:
            return True

    return False


def _rerank_chunks_tfidf(
    chunks: list[dict],
    query: str,
    top_k: int = _TOP_K_CHUNKS,
) -> list[dict]:
    """
    TF-IDF 코사인 유사도로 청크를 질의에 대한 관련성 순으로 재순위화.

    가비지 청크(반복 단어, 목차 점선 등)를 사전 필터링합니다.

    Returns:
        관련성 내림차순 정렬된 청크 리스트 (score 필드 추가).
    """
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np

    # 가비지 청크 필터링
    filtered = [(i, c) for i, c in enumerate(chunks) if not _is_low_quality_chunk(c["chunk_text"])]

    if not filtered:
        # 전부 필터링되면 원본 그대로 사용
        filtered = list(enumerate(chunks))

    orig_indices, filtered_chunks = zip(*filtered)
    chunk_texts = [c["chunk_text"] for c in filtered_chunks]

    try:
        vectorizer = TfidfVectorizer(max_features=5000)
        all_texts = chunk_texts + [query]
        tfidf_matrix = vectorizer.fit_transform(all_texts)

        query_vec = tfidf_matrix[-1:]
        chunk_vecs = tfidf_matrix[:-1]

        similarities = cosine_similarity(query_vec, chunk_vecs).flatten()

        ranked_indices = np.argsort(similarities)[::-1][:top_k]

        result = []
        for idx in ranked_indices:
            if similarities[idx] > 0:
                orig_idx = orig_indices[idx]
                result.append({
                    "text": chunk_texts[idx],
                    "chunk_index": chunks[orig_idx].get("chunk_index", orig_idx),
                    "score": float(similarities[idx]),
                })
        return result

    except Exception as e:
        logger.warning(f"TF-IDF re-ranking failed: {e}, returning first {top_k} chunks")
        return [
            {"text": c["chunk_text"], "chunk_index": c.get("chunk_index", i), "score": 0.0}
            for i, c in enumerate(chunks[:top_k])
        ]


def _parse_and_cache_chunks(doc_info: dict) -> list[dict]:
    """
    캐시되지 않은 문서를 온디맨드로 파싱하여 PostgreSQL에 캐시 저장.

    기존 문서(document_chunks 테이블 도입 전 등록된 문서)의 하위 호환을 위해
    첫 질의 시 자동으로 파싱+캐싱합니다.
    캐시 저장 실패(테이블 미존재 등)에도 파싱된 청크는 반환합니다.
    """
    from watcher.loader.document_loader import extract_text
    from rag.chunker import chunk_text

    file_path = doc_info.get("source_file", "")
    file_type = doc_info.get("file_type", "")
    doc_id = doc_info.get("id")
    doc_name = doc_info.get("doc_name", "")

    if not file_path or not Path(file_path).is_file():
        logger.warning(f"Source file missing from disk for lazy caching: {file_path}")
        return []

    text = extract_text(file_path, file_type)
    if not text:
        return []

    chunks = chunk_text(text, source=doc_name, chunk_size=1000, chunk_overlap=100)

    # 캐시 저장 시도 (실패해도 파싱된 청크는 그대로 반환)
    if doc_id and chunks:
        try:
            from catalog.catalog import replace_document_chunks
            replace_document_chunks(doc_id, chunks)
            logger.info(f"Lazy-cached {len(chunks)} chunks for document '{doc_name}'")
        except Exception as e:
            logger.warning(f"Failed to cache chunks for '{doc_name}' (table may not exist): {e}")

    return [
        {"chunk_text": c.text, "chunk_index": c.metadata.get("chunk_index", i)}
        for i, c in enumerate(chunks)
    ]


# ============================================
# 포맷팅 유틸리티
# ============================================

def _format_chunk_context(chunks: list[dict]) -> str:
    """
    재순위화된 청크를 LLM 컨텍스트 형식으로 포맷팅.

    출처별로 그룹화하고 chunk_index 순으로 정렬하여 서사 흐름을 유지합니다.
    """
    by_source = defaultdict(list)
    for c in chunks:
        by_source[c.get("source", "unknown")].append(c)

    parts = []
    for idx, (source, source_chunks) in enumerate(by_source.items(), 1):
        source_chunks.sort(key=lambda x: x.get("chunk_index", 0))
        similarity = source_chunks[0].get("similarity", 0)
        chunks_text = "\n---\n".join(c["text"] for c in source_chunks)
        parts.append(
            f"[문서 {idx}] (출처: {source}, 유사도: {similarity:.2f}, "
            f"관련 구간 {len(source_chunks)}개)\n{chunks_text}"
        )
    return "\n\n".join(parts)


def _extract_unique_sources(results: list[dict]) -> list[dict]:
    """검색 결과에서 중복 없는 source 파일 목록을 추출하여 유사도 내림차순 정렬."""
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


def _extract_sources(results: list[dict]) -> list[dict]:
    """검색 결과에서 출처와 유사도 정보를 추출하여 중복 제거."""
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
