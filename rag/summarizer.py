"""
문서 요약 생성 모듈 -- 업로드 시 Tier 1 임베딩용 경량 요약을 생성.

LLM을 사용하여 문서 텍스트의 간결한 요약을 생성하되,
LLM 호출이 실패하면 폴백으로 첫 1000자 + 추출된 섹션 제목을 사용합니다.
생성된 요약은 ChromaDB에 임베딩되어 "어떤 파일에 관련 내용이 있는가?"를
판별하는 시맨틱 검색에 활용됩니다.

의존 모듈:
    - agent._llm: generate() — Ollama LLM 호출
"""

import logging
import re

logger = logging.getLogger(__name__)

# LLM 요약 생성용 시스템 프롬프트
_SUMMARY_SYSTEM_PROMPT = """You are a document summarization expert.
Summarize the given document text in 300 characters or less.

Rules:
- Include the document's topic and key content
- Must include important keywords
- Write concisely and clearly
"""

# LLM에 전달할 텍스트 최대 길이 (토큰/지연 절감)
_LLM_INPUT_MAX_CHARS = 2000

# 폴백 요약에서 사용할 본문 앞부분 길이
_FALLBACK_HEAD_CHARS = 500


def generate_summary(text: str, source: str = "", max_chars: int = 1000) -> str:
    """
    문서 텍스트로부터 검색용 요약을 생성.

    1차: LLM에 요약 요청 (300자 이내 요약 + 주요 키워드).
    폴백: LLM 실패 시 첫 500자 + 섹션 제목 추출 결과를 결합.

    Args:
        text: 전체 문서 텍스트.
        source: 문서 파일명 (로깅용).
        max_chars: 요약 최대 길이 (기본 1000자).

    Returns:
        요약 텍스트 문자열. 빈 텍스트 입력 시 빈 문자열.
    """
    if not text or not text.strip():
        return ""

    summary = _summarize_with_llm(text, max_chars)

    if not summary:
        logger.info(f"LLM summary failed for '{source}', using fallback")
        summary = _summarize_fallback(text, max_chars)

    logger.info(f"Summary generated for '{source}': {len(summary)} chars")
    return summary


def _summarize_with_llm(text: str, max_chars: int) -> str:
    """
    Ollama LLM을 호출하여 문서 요약을 생성.

    텍스트가 _LLM_INPUT_MAX_CHARS를 초과하면 앞부분만 전달합니다.

    Returns: LLM 요약 문자열, 실패 시 빈 문자열.
    """
    from agent._llm import generate

    truncated = text[:_LLM_INPUT_MAX_CHARS]
    prompt = f"Summarize the following document:\n\n{truncated}"

    result = generate(
        prompt=prompt,
        system=_SUMMARY_SYSTEM_PROMPT,
        temperature=0.1,
        timeout=30,
    )

    if result:
        return result[:max_chars]
    return ""


def _summarize_fallback(text: str, max_chars: int) -> str:
    """
    LLM 없이 규칙 기반으로 요약을 생성 (폴백).

    첫 1000자 + 문서에서 추출한 제목/헤더 라인을 합쳐서 max_chars 이내로 반환합니다.
    """
    head = text[:_FALLBACK_HEAD_CHARS].strip()

    headings = _extract_headings(text)
    if headings:
        headings_text = "\n[주요 섹션]\n" + "\n".join(headings)
    else:
        headings_text = ""

    summary = head + headings_text
    return summary[:max_chars]


def _extract_headings(text: str, max_headings: int = 20) -> list[str]:
    """
    텍스트에서 제목/소제목으로 보이는 줄을 휴리스틱으로 추출.

    기준: 줄 길이 80자 이하, 마침표로 끝나지 않음,
    숫자·대문자·특수기호로 시작하는 짧은 줄.

    Returns: 추출된 제목 문자열 리스트 (최대 max_headings개).
    """
    headings = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or len(line) > 80:
            continue
        # 마침표로 끝나는 일반 문장은 제외
        if line.endswith(".") or line.endswith("다.") or line.endswith("요."):
            continue
        # 제목 패턴: 숫자 시작 (예: "1. 개요"), 대문자 시작, 특수기호 (예: "## 제목")
        if re.match(r"^(\d+[\.\)]\s|#{1,3}\s|[A-Z]|[가-힣]{1,4}\s?\d|제\d|Chapter)", line):
            headings.append(line)
            if len(headings) >= max_headings:
                break
    return headings
