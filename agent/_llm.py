"""
다중 프로바이더 LLM 래퍼 모듈.

Ollama(로컬), OpenAI, Anthropic, Hugging Face API를 통합하여
LLM 텍스트 생성을 수행합니다.
용도(purpose)에 따라 오케스트레이터용/에이전트용 모델을 자동으로 선택합니다.

지원 프로바이더:
    - ollama: 로컬 Ollama 서버 (폐쇄망 환경 필수)
    - openai: OpenAI API (gpt-4o, gpt-4o-mini 등)
    - anthropic: Anthropic API (claude-3-5-sonnet 등)
    - huggingface: Hugging Face Inference API (Qwen, Llama, Mistral 등)

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
import os
from typing import Literal

import requests

from config.settings import get_settings, LLMProviderConfig

logger = logging.getLogger(__name__)

# 지원하는 프로바이더 목록
SUPPORTED_PROVIDERS = ("ollama", "openai", "anthropic", "huggingface")

# GPU/CPU 상태 캐시 (앱 실행 중 한 번만 감지)
_compute_status_cache: dict | None = None

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

    # 타임아웃 결정: GPU/CPU에 따라 자동 조정
    if timeout is None:
        if config.provider == "ollama":
            timeout = _get_adaptive_timeout(config.base_url, settings.ollama.timeout)
        else:
            timeout = settings.ollama.timeout

    # 프로바이더별 호출
    if config.provider == "ollama":
        return _generate_ollama(prompt, system, config, temperature, timeout)
    elif config.provider == "openai":
        return _generate_openai(prompt, system, config, temperature, timeout)
    elif config.provider == "anthropic":
        return _generate_anthropic(prompt, system, config, temperature, timeout)
    elif config.provider == "huggingface":
        return _generate_huggingface(prompt, system, config, temperature, timeout)
    else:
        logger.error(f"Unsupported LLM provider: {config.provider}")
        return ""


def get_cached_compute_status(base_url: str = "") -> dict:
    """
    GPU/CPU 상태를 캐싱하여 반환. 앱 실행 중 첫 호출 시만 Ollama API를 조회.

    Returns: check_ollama_compute_status()와 동일한 dict.
    """
    global _compute_status_cache
    if _compute_status_cache is None:
        _compute_status_cache = check_ollama_compute_status(base_url)
        device = _compute_status_cache["compute_device"]
        rec_timeout = _compute_status_cache["recommended_timeout"]
        logger.info(
            f"Compute device detected: {device}, "
            f"recommended timeout: {rec_timeout}s"
        )
    return _compute_status_cache


def _get_adaptive_timeout(base_url: str, env_timeout: int) -> int:
    """
    GPU/CPU 상태에 따라 적절한 타임아웃을 반환.

    GPU 사용 시 env_timeout(기본 120s) 유지,
    CPU 모드 시 max(env_timeout, 600s)로 상향.
    """
    try:
        status = get_cached_compute_status(base_url)
        recommended = status.get("recommended_timeout", 600)
        # CPU 모드면 최소 600초 보장, GPU면 env 설정 존중
        if status.get("compute_device") == "cpu":
            return max(env_timeout, recommended)
        return env_timeout
    except Exception:
        return env_timeout


def _generate_ollama(
    prompt: str,
    system: str,
    config: LLMProviderConfig,
    temperature: float,
    timeout: int,
) -> str:
    """Ollama REST API를 호출하여 텍스트 생성."""
    url = f"{config.base_url}/api/generate"

    options = {
        "temperature": temperature,
    }

    # CPU 모드일 때 성능 최적화 옵션 추가
    try:
        status = get_cached_compute_status(config.base_url)
        if status.get("compute_device") == "cpu":
            options["num_ctx"] = 1024   # 컨텍스트 길이 축소 (기본 2048 → 1024)
            options["num_thread"] = os.cpu_count() or 4  # CPU 코어 전부 활용
    except Exception:
        pass

    payload = {
        "model": config.model,
        "prompt": prompt,
        "stream": False,
        "options": options,
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


def _generate_huggingface(
    prompt: str,
    system: str,
    config: LLMProviderConfig,
    temperature: float,
    timeout: int,
) -> str:
    """Hugging Face Inference API를 호출하여 텍스트 생성."""
    try:
        from huggingface_hub import InferenceClient
    except ImportError:
        logger.error(
            "huggingface_hub 패키지가 설치되지 않았습니다. "
            "'pip install huggingface-hub'를 실행하세요."
        )
        return ""

    if not config.api_key:
        logger.error("Hugging Face API 키가 설정되지 않았습니다.")
        return ""

    try:
        client = InferenceClient(
            model=config.model,
            token=config.api_key,
            timeout=timeout,
        )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = client.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=4096,
        )

        result = response.choices[0].message.content or ""

        logger.debug(
            f"HuggingFace generate completed: model={config.model}, "
            f"prompt_len={len(prompt)}, response_len={len(result)}"
        )
        return result

    except Exception as e:
        logger.error(f"HuggingFace API error: {e}")
        return ""


def check_provider_connection(provider: str, config: LLMProviderConfig) -> dict:
    """
    프로바이더 연결 상태를 확인.

    Args:
        provider: 프로바이더 유형 ("ollama", "openai", "anthropic", "huggingface")
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

    elif provider == "huggingface":
        if not config.api_key:
            return {"connected": False, "message": "API 키 미설정", "models": None}
        try:
            from huggingface_hub import InferenceClient
            client = InferenceClient(token=config.api_key, timeout=10)
            # 간단한 연결 테스트: 텍스트 생성 가능 여부 확인
            # list_deployed_models()로 Inference API에서 사용 가능한 모델 확인
            try:
                deployed = client.list_deployed_models(frameworks="text-generation-inference")
                tgi_models = deployed.get("text-generation-inference", [])
                # 인기 모델만 필터링하여 표시
                popular_prefixes = (
                    "Qwen/", "meta-llama/", "mistralai/", "google/",
                    "microsoft/", "HuggingFaceH4/", "bigcode/",
                )
                filtered = [
                    m for m in tgi_models
                    if any(m.startswith(p) for p in popular_prefixes)
                ][:15]
                return {
                    "connected": True,
                    "message": f"연결됨 ({len(tgi_models)} 모델 사용 가능)",
                    "models": filtered if filtered else tgi_models[:15],
                }
            except Exception:
                # list_deployed_models 실패해도 API 키가 유효하면 연결 성공으로 간주
                if config.api_key.startswith("hf_"):
                    return {
                        "connected": True,
                        "message": "API 키 형식 확인됨",
                        "models": [
                            "Qwen/Qwen2.5-72B-Instruct",
                            "meta-llama/Llama-3.1-8B-Instruct",
                            "mistralai/Mistral-7B-Instruct-v0.3",
                        ],
                    }
                return {"connected": False, "message": "API 키 검증 실패", "models": None}
        except ImportError:
            return {"connected": False, "message": "huggingface-hub 패키지 미설치", "models": None}
        except Exception as e:
            return {"connected": False, "message": str(e), "models": None}

    return {"connected": False, "message": f"지원하지 않는 프로바이더: {provider}", "models": None}


def check_ollama_compute_status(base_url: str = "") -> dict:
    """
    Ollama 서버의 GPU/CPU 실행 상태를 확인.

    /api/ps 엔드포인트로 현재 로드된 모델의 VRAM 사용량을 확인하고,
    /api/tags로 설치된 모델 목록과 크기를 조회합니다.

    Args:
        base_url: Ollama 서버 URL. 비어있으면 settings에서 로드.

    Returns:
        {
            "connected": bool,
            "compute_device": "gpu" | "cpu" | "unknown",
            "gpu_name": str | None,
            "vram_total_mb": int | None,
            "vram_used_mb": int | None,
            "loaded_models": list[dict],  # name, size_mb, size_vram_mb
            "installed_models": list[dict],  # name, size_mb, parameter_size
            "recommended_timeout": int,  # GPU=120, CPU=300
            "message": str,
        }
    """
    if not base_url:
        try:
            settings = get_settings()
            base_url = settings.ollama.host
        except Exception:
            base_url = "http://localhost:11434"

    result = {
        "connected": False,
        "compute_device": "unknown",
        "gpu_name": None,
        "vram_total_mb": None,
        "vram_used_mb": None,
        "loaded_models": [],
        "installed_models": [],
        "recommended_timeout": 600,
        "message": "",
    }

    # 1. 설치된 모델 목록 (/api/tags)
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        if resp.status_code != 200:
            result["message"] = f"Ollama 연결 실패 (HTTP {resp.status_code})"
            return result

        result["connected"] = True
        models_data = resp.json().get("models", [])
        result["installed_models"] = [
            {
                "name": m.get("name", ""),
                "size_mb": round(m.get("size", 0) / (1024 * 1024)),
                "parameter_size": m.get("details", {}).get("parameter_size", ""),
                "family": m.get("details", {}).get("family", ""),
            }
            for m in models_data
        ]
    except requests.ConnectionError:
        result["message"] = "Ollama 서버에 연결할 수 없습니다"
        return result
    except Exception as e:
        result["message"] = f"Ollama 상태 확인 실패: {e}"
        return result

    # 2. 로드된 모델 상태 (/api/ps) — GPU/CPU 판별
    try:
        resp = requests.get(f"{base_url}/api/ps", timeout=5)
        if resp.status_code == 200:
            ps_data = resp.json().get("models", [])
            total_vram = 0
            for m in ps_data:
                size_mb = round(m.get("size", 0) / (1024 * 1024))
                vram_mb = round(m.get("size_vram", 0) / (1024 * 1024))
                total_vram += vram_mb
                result["loaded_models"].append({
                    "name": m.get("name", ""),
                    "size_mb": size_mb,
                    "size_vram_mb": vram_mb,
                })

            if ps_data:
                result["vram_used_mb"] = total_vram
                if total_vram > 0:
                    result["compute_device"] = "gpu"
                    result["recommended_timeout"] = 120
                    result["message"] = f"GPU 가속 사용 중 (VRAM {total_vram}MB 사용)"
                else:
                    result["compute_device"] = "cpu"
                    result["recommended_timeout"] = 600
                    result["message"] = "CPU 모드로 실행 중 (GPU 미사용, 응답 느림)"
            else:
                # 로드된 모델이 없으면 판별 불가, CPU로 간주
                result["compute_device"] = "cpu"
                result["recommended_timeout"] = 300
                result["message"] = "현재 로드된 모델 없음 (첫 요청 시 로드)"
    except Exception:
        # /api/ps 실패해도 연결 자체는 되어 있으므로 CPU로 간주
        result["compute_device"] = "cpu"
        result["recommended_timeout"] = 300
        result["message"] = "실행 상태 확인 불가 (CPU 모드로 간주)"

    return result


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
        "huggingface_reachable": False,
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

    # Hugging Face API 접근 가능 여부
    try:
        requests.get("https://huggingface.co", timeout=5)
        result["huggingface_reachable"] = True
    except Exception:
        pass

    # 권장 모드 결정
    if result["openai_reachable"] or result["anthropic_reachable"] or result["huggingface_reachable"]:
        result["recommended_mode"] = "hybrid"

    return result
