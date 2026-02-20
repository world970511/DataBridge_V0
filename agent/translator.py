"""
번역 에이전트 모듈 — 사용자 입력 언어 감지 및 최종 응답 번역.

모든 내부 LLM 프롬프트는 영어로 작성되어 있으므로, LLM의 응답도 영어입니다.
이 모듈은 사용자의 입력 언어를 감지하고, 영어가 아닌 경우에만
최종 응답을 해당 언어로 번역합니다.

번역 모델은 오케스트레이터와 동일한 모델(purpose="orchestrator")을 사용합니다.

의존 모듈:
    - agent._llm: generate() — LLM 호출
"""

import logging

logger = logging.getLogger(__name__)

_TRANSLATE_SYSTEM_PROMPT = """You are a professional translator.
Translate the given text to {target_language}.

Rules:
- Translate naturally while preserving the original meaning
- Keep technical terms, table names, column names, SQL code, and numbers as-is
- Keep emoji and markdown formatting intact
- Do NOT add any explanations or commentary — output ONLY the translated text
"""

_LANG_NAMES = {
    "ko": "Korean",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
}


def detect_language(text: str) -> str:
    """
    사용자 입력 텍스트의 언어를 유니코드 문자 분석으로 감지.

    Args:
        text: 분석할 텍스트.

    Returns:
        언어 코드: "ko", "ja", "zh", 또는 "en" (기본값).
    """
    if not text or not text.strip():
        return "en"

    hangul = 0
    kana = 0
    cjk = 0
    latin = 0

    for ch in text:
        cp = ord(ch)
        if (0xAC00 <= cp <= 0xD7A3) or (0x1100 <= cp <= 0x11FF) or (0x3130 <= cp <= 0x318F):
            hangul += 1
        elif (0x3040 <= cp <= 0x309F) or (0x30A0 <= cp <= 0x30FF):
            kana += 1
        elif 0x4E00 <= cp <= 0x9FFF:
            cjk += 1
        elif ch.isascii() and ch.isalpha():
            latin += 1

    total = hangul + kana + cjk + latin
    if total == 0:
        return "en"

    if hangul / total > 0.1:
        return "ko"
    if kana / total > 0.1:
        return "ja"
    if cjk / total > 0.2:
        return "zh"

    return "en"


def translate_if_needed(text: str, query_lang: str) -> str:
    """
    query_lang이 영어가 아닌 경우에만 LLM으로 번역.

    번역 실패 시 원본 텍스트를 그대로 반환합니다.

    Args:
        text: 번역할 텍스트 (영어 LLM 응답).
        query_lang: 사용자 입력 언어 코드 ("ko", "ja", "zh", "en" 등).

    Returns:
        번역된 텍스트, 또는 원본 텍스트 (영어이거나 번역 실패 시).
    """
    if query_lang == "en":
        return text

    if not text or not text.strip():
        return text

    target_name = _LANG_NAMES.get(query_lang, "Korean")

    try:
        from agent._llm import generate

        system = _TRANSLATE_SYSTEM_PROMPT.format(target_language=target_name)

        translated = generate(
            prompt=f"Translate to {target_name}:\n\n{text}",
            system=system,
            purpose="orchestrator",
            temperature=0.1,
            timeout=60,
        )

        if translated and translated.strip():
            logger.debug(
                f"Translation done: lang={query_lang}, "
                f"in={len(text)} chars, out={len(translated)} chars"
            )
            return translated.strip()

        logger.warning(f"Translation returned empty, using original: lang={query_lang}")
        return text

    except Exception as e:
        logger.warning(f"Translation failed (lang={query_lang}): {e}")
        return text
