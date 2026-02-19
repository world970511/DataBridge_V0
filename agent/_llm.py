"""
다중 프로바이더 LLM 래퍼 모듈.

Ollama(로컬), OpenAI, Anthropic API를 통합하여 LLM 텍스트 생성을 수행합니다.
용도(purpose)에 따라 오케스트레이터용/에이전트용 모델을 자동으로 선택합니다.

지원 프로바이더:
    - ollama: 로컬 Ollama 서버 (폐쇄망 환경 필수)
    - openai: OpenAI API (gpt-4o, gpt-4o-mini 등)
    - anthropic: Anthropic API (claude-3-5-sonnet 등)

주요 함수:
    generate(prompt, system, purpose, temperature, timeout) -> str
        - purpose="orchestrator": 의도 분류 등 간단한 작업
        - purpose="agent": SQL 생성, RAG 등 데이터 처리

의존 모듈:
    - config.settings: get_settings() -> LLMConfig
    - requests: Ollama HTTP 요청
    - openai (선택): OpenAI API 클라이언트
    - anthropic (선택): Anthropic API 클라이언트

사용 예시:
    from agent._llm import generate

    # 오케스트레이터용 (의도 분류 - 상용 모델 가능)
    intent = generate(
        prompt="이 질문의 의도를 분류해줘",
        system="DATA/DOCUMENT/BOTH 중 하나로 답하세요",
        purpose="orchestrator",
    )

    # 에이전트용 (데이터 처리 - 로컬 모델 권장)
    sql = generate(
        prompt="매출 조회 SQL을 생성해줘",
        system="PostgreSQL 전문가입니다",
        purpose="agent",
    )
"""

import logging
from typing import Literal

import requests

from config.settings import get_settings, LLMProviderConfig

logger = logging.getLogger(__name__)

# 지원하는 프로바이더 목록
SUPPORTED_PROVIDERS = ("ollama", "openai", "anthropic")

# 용도별 역할
Purpose = Literal["orchestrator", "agent"]


def generate(
    prompt: str,
    system: str = "",
    purpose: Purpose = "agent",
    temperature: float = 0.1,
    timeout: int | None = None,
) -> str:
    """
    용도에 맞는 LLM을 호출하여 텍스트를 생성하고 응답 문자열을 반환.

    Settings에서 purpose에 해당하는 LLMProviderConfig를 읽어와
    적절한 프로바이더 API를 호출합니다.

    DB에 저장된 런타임 설정이 환경 변수 설정보다 우선 적용됩니다.

    Args:
        prompt: LLM에 전달할 사용자 프롬프트 텍스트.
        system: 시스템 프롬프트 (모델의 역할/규칙 정의). 빈 문자열이면 생략.
        purpose: 용도 구분.
                 - "orchestrator": 의도 분류 등 간단한 작업 (상용 모델 가능)
                 - "agent": SQL 생성, RAG 등 데이터 처리 (로컬 모델 권장)
        temperature: 생성 다양성 조절 (0.0=결정적, 1.0=다양). 기본값 0.1.
        timeout: HTTP 요청 타임아웃 (초). None이면 settings.ollama.timeout 사용.

    Returns:
        LLM이 생성한 응답 텍스트 문자열.
        API 호출 실패 시 빈 문자열("").

    Note:
        이 함수는 예외를 전파하지 않습니다. 모든 오류는 logger.error()로 기록되고
        빈 문자열을 반환하므로, 호출측에서 반환값의 진위(bool(result))로 성공 여부를
        판단할 수 있습니다.
    """
    # DB 설정 우선 적용 (런타임 변경 지원)
    try:
        from config.llm_settings import apply_db_settings_to_config
        settings = apply_db_settings_to_config()
    except Exception:
        # DB 연결 실패 시 환경 변수 설정 사용
        settings = get_settings()

    # 타임아웃 기본값
    if timeout is None:
        timeout = settings.ollama.timeout

    # 용도에 따른 설정 선택
    if purpose == "orchestrator":
        config = settings.llm.orchestrator
    else:
        config = settings.llm.agent

    # 폐쇄망 모드에서 상용 API 사용 시도 시 경고 및 폴백
    if settings.llm.airgapped_mode and config.provider != "ollama":
        logger.warning(
            f"Airgapped mode enabled but {config.provider} provider configured. "
            f"Falling back to Ollama."
        )
        config = LLMProviderConfig(
            provider="ollama",
            model=settings.ollama.model,
            base_url=settings.ollama.host,
        )

    # 프로바이더별 호출
    if config.provider == "ollama":
        return _generate_ollama(prompt, system, config, temperature, timeout)
    elif config.provider == "openai":
        return _generate_openai(prompt, system, config, temperature, timeout)
    elif config.provider == "anthropic":
        return _generate_anthropic(prompt, system, config, temperature, timeout)
    else:
        logger.error(f"Unsupported LLM provider: {config.provider}")
        return ""


def _generate_ollama(
    prompt: str,
    system: str,
    config: LLMProviderConfig,
    temperature: float,
    timeout: int,
) -> str:
    """Ollama REST API를 호출하여 텍스트 생성."""
    url = f"{config.base_url}/api/generate"

    payload = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
        },
    }

    if system:
        payload["system"] = system

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()

        data = response.json()
        result = data.get("response", "")

        logger.debug(
            f"Ollama generate completed: model={config.model}, "
            f"prompt_len={len(prompt)}, response_len={len(result)}"
        )
        return result

    except requests.exceptions.Timeout:
        logger.error(f"Ollama API timeout after {timeout}s: model={config.model}")
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


def _generate_openai(
    prompt: str,
    system: str,
    config: LLMProviderConfig,
    temperature: float,
    timeout: int,
) -> str:
    """OpenAI API를 호출하여 텍스트 생성."""
    try:
        from openai import OpenAI
    except ImportError:
        logger.error(
            "OpenAI 패키지가 설치되지 않았습니다. "
            "'pip install openai'를 실행하세요."
        )
        return ""

    if not config.api_key:
        logger.error("OpenAI API key가 설정되지 않았습니다.")
        return ""

    try:
        client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url if config.base_url else None,
            timeout=timeout,
        )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = client.chat.completions.create(
            model=config.model,
            messages=messages,
            temperature=temperature,
        )

        result = response.choices[0].message.content or ""

        logger.debug(
            f"OpenAI generate completed: model={config.model}, "
            f"prompt_len={len(prompt)}, response_len={len(result)}"
        )
        return result

    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return ""


def _generate_anthropic(
    prompt: str,
    system: str,
    config: LLMProviderConfig,
    temperature: float,
    timeout: int,
) -> str:
    """Anthropic API를 호출하여 텍스트 생성."""
    try:
        import anthropic
    except ImportError:
        logger.error(
            "Anthropic 패키지가 설치되지 않았습니다. "
            "'pip install anthropic'를 실행하세요."
        )
        return ""

    if not config.api_key:
        logger.error("Anthropic API key가 설정되지 않았습니다.")
        return ""

    try:
        client = anthropic.Anthropic(
            api_key=config.api_key,
            timeout=timeout,
        )

        response = client.messages.create(
            model=config.model,
            max_tokens=4096,
            system=system if system else "",
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
        )

        result = response.content[0].text if response.content else ""

        logger.debug(
            f"Anthropic generate completed: model={config.model}, "
            f"prompt_len={len(prompt)}, response_len={len(result)}"
        )
        return result

    except Exception as e:
        logger.error(f"Anthropic API error: {e}")
        return ""


def check_provider_connection(provider: str, config: LLMProviderConfig) -> dict:
    """
    프로바이더 연결 상태를 확인.

    Args:
        provider: 프로바이더 유형 ("ollama", "openai", "anthropic")
        config: 프로바이더 설정

    Returns:
        {
            "connected": bool,
            "message": str,
            "models": list[str] | None,  # ollama의 경우 사용 가능한 모델 목록
        }
    """
    if provider == "ollama":
        try:
            resp = requests.get(f"{config.base_url}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                return {
                    "connected": True,
                    "message": f"연결됨 ({len(models)} 모델)",
                    "models": models,
                }
            return {"connected": False, "message": f"HTTP {resp.status_code}", "models": None}
        except requests.ConnectionError:
            return {"connected": False, "message": "연결 실패", "models": None}
        except Exception as e:
            return {"connected": False, "message": str(e), "models": None}

    elif provider == "openai":
        if not config.api_key:
            return {"connected": False, "message": "API 키 미설정", "models": None}
        try:
            from openai import OpenAI
            client = OpenAI(api_key=config.api_key, timeout=10)
            models = client.models.list()
            return {
                "connected": True,
                "message": "연결됨",
                "models": [m.id for m in models.data[:10]],  # 상위 10개만
            }
        except ImportError:
            return {"connected": False, "message": "openai 패키지 미설치", "models": None}
        except Exception as e:
            return {"connected": False, "message": str(e), "models": None}

    elif provider == "anthropic":
        if not config.api_key:
            return {"connected": False, "message": "API 키 미설정", "models": None}
        # Anthropic은 모델 목록 API가 없으므로 간단한 검증만 수행
        try:
            import anthropic
            # API 키 형식 검증 (실제 호출 없이)
            if config.api_key.startswith("sk-ant-"):
                return {
                    "connected": True,
                    "message": "API 키 형식 확인됨",
                    "models": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"],
                }
            return {"connected": False, "message": "잘못된 API 키 형식", "models": None}
        except ImportError:
            return {"connected": False, "message": "anthropic 패키지 미설치", "models": None}

    return {"connected": False, "message": f"지원하지 않는 프로바이더: {provider}", "models": None}


def check_network_connectivity() -> dict:
    """
    외부 네트워크 연결 상태를 확인하여 폐쇄망 여부를 감지.

    Returns:
        {
            "internet_available": bool,
            "openai_reachable": bool,
            "anthropic_reachable": bool,
            "recommended_mode": str,  # "airgapped" 또는 "hybrid"
        }
    """
    result = {
        "internet_available": False,
        "openai_reachable": False,
        "anthropic_reachable": False,
        "recommended_mode": "airgapped",
    }

    # 일반 인터넷 연결 확인
    try:
        requests.get("https://www.google.com", timeout=5)
        result["internet_available"] = True
    except Exception:
        pass

    # OpenAI API 접근 가능 여부
    try:
        requests.get("https://api.openai.com", timeout=5)
        result["openai_reachable"] = True
    except Exception:
        pass

    # Anthropic API 접근 가능 여부
    try:
        requests.get("https://api.anthropic.com", timeout=5)
        result["anthropic_reachable"] = True
    except Exception:
        pass

    # 권장 모드 결정
    if result["openai_reachable"] or result["anthropic_reachable"]:
        result["recommended_mode"] = "hybrid"

    return result
