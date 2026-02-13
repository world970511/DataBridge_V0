"""
Ollama REST API 래퍼 모듈.

Ollama의 /api/generate 엔드포인트를 직접 호출하여 LLM 텍스트 생성을 수행합니다.
LangChain 등 외부 프레임워크 없이 requests 라이브러리만으로 동작하며,
config.settings의 OllamaConfig(host, model)을 참조합니다.

주요 함수:
    generate(prompt, system, temperature, timeout) -> str
        - 프롬프트와 시스템 메시지를 Ollama에 전송하여 응답 텍스트를 반환합니다.
        - stream=False 모드로 한 번에 전체 응답을 수신합니다.
        - API 호출 실패 시 빈 문자열("")을 반환하여 호출측에서 안전하게 처리합니다.

의존 모듈:
    - config.settings: get_settings() → OllamaConfig(host, model)
    - requests: HTTP POST 요청

사용 예시:
    from agent._llm import generate
    answer = generate(
        prompt="매출 데이터를 조회하는 SQL을 생성해줘",
        system="당신은 SQL 전문가입니다.",
    )
"""

import logging

import requests

from config.settings import get_settings

logger = logging.getLogger(__name__)


def generate(
    prompt: str,
    system: str = "",
    temperature: float = 0.1,
    timeout: int = 60,
) -> str:
    """
    Ollama REST API를 호출하여 LLM 텍스트를 생성하고 응답 문자열을 반환.

    Settings에서 ollama.host(기본: http://ollama:11434)와 ollama.model(기본: exaone3.5:7.8b)을
    읽어와 POST /api/generate 엔드포인트에 JSON 페이로드를 전송합니다.
    stream=False로 설정하여 한 번에 전체 응답을 수신합니다.

    Args:
        prompt: LLM에 전달할 사용자 프롬프트 텍스트.
        system: 시스템 프롬프트 (모델의 역할·규칙 정의). 빈 문자열이면 생략됩니다.
        temperature: 생성 다양성 조절 (0.0=결정적, 1.0=다양). 기본값 0.1로
                     SQL 생성 등 정확도가 중요한 작업에 적합합니다.
        timeout: HTTP 요청 타임아웃 (초). 기본 60초. 복잡한 쿼리의 경우 늘릴 수 있습니다.

    Returns:
        LLM이 생성한 응답 텍스트 문자열.
        API 호출 실패(네트워크 오류, 타임아웃, JSON 파싱 실패 등) 시 빈 문자열("").

    Note:
        이 함수는 예외를 전파하지 않습니다. 모든 오류는 logger.error()로 기록되고
        빈 문자열을 반환하므로, 호출측에서 반환값의 진위(bool(result))로 성공 여부를
        판단할 수 있습니다.
    """
    settings = get_settings()
    url = f"{settings.ollama.host}/api/generate"

    payload = {
        "model": settings.ollama.model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
        },
    }

    # 시스템 프롬프트가 있으면 페이로드에 포함
    if system:
        payload["system"] = system

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()

        data = response.json()
        result = data.get("response", "")

        logger.debug(
            f"LLM generate completed: model={settings.ollama.model}, "
            f"prompt_len={len(prompt)}, response_len={len(result)}"
        )
        return result

    except requests.exceptions.Timeout:
        logger.error(
            f"Ollama API timeout after {timeout}s: model={settings.ollama.model}"
        )
        return ""
    except requests.exceptions.ConnectionError:
        logger.error(
            f"Ollama API connection failed: url={url}. "
            "Ollama 서버가 실행 중인지 확인하세요."
        )
        return ""
    except requests.exceptions.HTTPError as e:
        logger.error(f"Ollama API HTTP error: {e}")
        return ""
    except (ValueError, KeyError) as e:
        logger.error(f"Ollama API response parsing failed: {e}")
        return ""
