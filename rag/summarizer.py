"""
문서 요약 생성 모듈 -- 업로드 시 Tier 1 임베딩용 경량 요약을 생성.

TF-IDF 기반 추출적 요약을 기본으로 사용하여 LLM 호출 없이
문서의 핵심 문장을 선별합니다 (문서당 수 밀리초).
생성된 요약은 ChromaDB에 임베딩되어 "어떤 파일에 관련 내용이 있는가?"를
판별하는 시맨틱 검색에 활용됩니다.

LLM 요약은 generate_summary_with_llm()으로 명시 호출 시에만 사용됩니다.

의존 모듈:
    - sklearn.feature_extraction.text: TfidfVectorizer — TF-IDF 벡터화
    - agent._llm: generate() — Ollama LLM 호출 (선택적)
"""

import logging
import re

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

logger = logging.getLogger(__name__)

# LLM 요약 생성용 시스템 프롬프트 (명시적 LLM 요약 호출 시 사용)
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

    TF-IDF 기반 추출적 요약을 기본으로 사용합니다 (LLM 호출 없음).
    추출적 요약이 실패하면 폴백(첫 500자 + 섹션 제목)을 사용합니다.

    Args:
        text: 전체 문서 텍스트.
        source: 문서 파일명 (로깅용).
        max_chars: 요약 최대 길이 (기본 1000자).

    Returns:
        요약 텍스트 문자열. 빈 텍스트 입력 시 빈 문자열.
    """
    if not text or not text.strip():
        return ""

    summary = generate_extractive_summary(text, source, max_chars)

    if not summary:
        logger.info(f"Extractive summary failed for '{source}', using fallback")
        summary = _summarize_fallback(text, max_chars)

    logger.info(f"Summary generated for '{source}': {len(summary)} chars")
    return summary


def generate_extractive_summary(
    text: str,
    source: str = "",
    max_chars: int = 1000,
    max_sentences: int = 15,
) -> str:
    """
    TF-IDF 기반 추출적 요약 생성 (LLM 불필요, 수 밀리초).

    문서 텍스트를 문장으로 분리한 뒤 TF-IDF 가중치 합이 가장 높은
    문장들을 원본 순서대로 선택하여 max_chars 이내의 요약을 구성합니다.
    섹션 제목도 추출하여 요약 뒤에 추가합니다.

    Args:
        text: 전체 문서 텍스트.
        source: 문서 파일명 (로깅용).
        max_chars: 요약 최대 길이 (기본 1000자).
        max_sentences: 추출할 최대 문장 수 (기본 15).

    Returns:
        추출적 요약 텍스트. 빈 텍스트 입력 시 빈 문자열.
    """
    if not text or not text.strip():
        return ""

    sentences = _split_into_sentences(text)
    if not sentences:
        return text[:max_chars]

    # 너무 짧거나 긴 문장 필터링 + 가비지 문장(반복 단어, 점선 목차) 제거
    valid = [
        (i, s) for i, s in enumerate(sentences)
        if 10 <= len(s.strip()) <= 500 and not _is_noise_sentence(s)
    ]
    if not valid:
        return text[:max_chars]

    indices, sent_texts = zip(*valid)

    # TF-IDF 점수 계산
    try:
        vectorizer = TfidfVectorizer(max_features=5000)
        tfidf_matrix = vectorizer.fit_transform(sent_texts)
        scores = np.array(tfidf_matrix.sum(axis=1)).flatten()
    except ValueError:
        logger.debug(f"TF-IDF failed for '{source}', using fallback")
        return ""

    # 상위 N개 문장 선택
    top_n = min(max_sentences, len(scores))
    top_score_indices = np.argsort(scores)[-top_n:]

    # 원본 문서 순서대로 재정렬 (서사 흐름 유지)
    selected = sorted(top_score_indices, key=lambda x: indices[x])
    summary_sentences = [sent_texts[i] for i in selected]

    # max_chars 이내로 조합
    summary = ""
    for sent in summary_sentences:
        candidate = summary + ("\n" if summary else "") + sent
        if len(candidate) > max_chars:
            break
        summary = candidate

    # 남은 공간에 섹션 제목 추가
    headings = _extract_headings(text)
    if headings:
        headings_text = "\n[주요 섹션]\n" + "\n".join(headings[:10])
        if len(summary) + len(headings_text) <= max_chars:
            summary += headings_text

    if not summary:
        return text[:max_chars]

    logger.debug(f"Extractive summary for '{source}': {len(summary)} chars")
    return summary


def _is_noise_sentence(text: str) -> bool:
    """
    PDF 레이아웃 아티팩트(반복 단어, 목차 점선 등) 노이즈 문장 판별.

    - 고유 단어 비율 < 20% (예: "조달청 조달청 조달청...")
    - 구두점·특수문자가 전체의 50% 이상 (예: "· · · · · ·")
    """
    stripped = text.strip()
    if not stripped:
        return True

    # 구두점·특수문자 비율
    non_content = sum(1 for c in stripped if c in "·.·\t -–—*+=|()[]{}…•●○◎◇◆■□▶▷")
    if non_content / len(stripped) > 0.5:
        return True

    # 고유 단어 비율 (단어 반복 감지)
    words = stripped.split()
    if len(words) >= 5:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.2:
            return True

    return False


def _split_into_sentences(text: str) -> list[str]:
    """
    텍스트를 문장 단위로 분리 (한/영/일 지원).

    마침표·느낌표·물음표와 한국어 종결 어미(다/요/음/니까) 뒤
    공백·줄바꿈을 기준으로 분할합니다.
    """
    raw = re.split(r"(?<=[.!?。])\s+|\n+", text)
    return [s.strip() for s in raw if s.strip()]


# ============================================
# LLM 기반 요약 (선택적 사용)
# ============================================

def generate_summary_with_llm(text: str, source: str = "", max_chars: int = 1000) -> str:
    """
    Ollama LLM을 호출하여 문서 요약을 생성 (명시적 호출 시 사용).

    텍스트가 _LLM_INPUT_MAX_CHARS를 초과하면 앞부분만 전달합니다.

    Returns: LLM 요약 문자열, 실패 시 빈 문자열.
    """
    from agent._llm import generate

    if not text or not text.strip():
        return ""

    truncated = text[:_LLM_INPUT_MAX_CHARS]
    prompt = f"Summarize the following document:\n\n{truncated}"

    result = generate(
        prompt=prompt,
        system=_SUMMARY_SYSTEM_PROMPT,
        temperature=0.1,
    )

    if result:
        summary = result[:max_chars]
        logger.info(f"LLM summary for '{source}': {len(summary)} chars")
        return summary
    return ""


# ============================================
# 폴백 / 유틸리티
# ============================================

def _summarize_fallback(text: str, max_chars: int) -> str:
    """
    규칙 기반 폴백 요약: 첫 500자 + 섹션 제목.

    추출적 요약과 LLM 요약 모두 실패했을 때 사용합니다.
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
