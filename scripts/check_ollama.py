"""
Ollama LLM 서버 연결 확인 및 모델 자동 다운로드 스크립트.

앱 기동 시 check_ollama()를 호출하여 Ollama 서버가 응답할 때까지 대기(최대 120초)하고,
설정된 LLM 모델(기본: exaone3.5:7.8b)이 설치되어 있는지 확인한 뒤
없으면 자동으로 pull합니다. 독립 실행(__main__)도 가능합니다.
"""

import logging
import sys
import time

import requests

from config.settings import get_settings

logger = logging.getLogger(__name__)


def wait_for_ollama(timeout: int = 120) -> bool:
    """
    Ollama 서버의 /api/tags 엔드포인트에 3초 간격으로 폴링하여 준비 상태를 확인.

    timeout(기본 120초) 이내에 HTTP 200 응답을 받으면 True,
    시간 초과 시 False를 반환합니다.
    Returns: 서버 준비 완료 시 True, 타임아웃 시 False.
    """
    settings = get_settings()
    url = f"{settings.ollama.host}/api/tags"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                logger.info("Ollama is ready")
                return True
        except requests.ConnectionError:
            pass
        logger.info("Waiting for Ollama...")
        time.sleep(3)

    logger.error("Ollama not reachable within timeout")
    return False


def list_models() -> list[str]:
    """
    Ollama /api/tags 엔드포인트를 호출하여 현재 설치된 모델 이름 목록을 조회.

    Returns: 설치된 모델 이름 문자열 리스트 (예: ['exaone3.5:7.8b', 'llama3:8b']).
    """
    settings = get_settings()
    url = f"{settings.ollama.host}/api/tags"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return [m["name"] for m in data.get("models", [])]


def pull_model(model_name: str) -> bool:
    """
    Ollama /api/pull 엔드포인트에 POST 요청을 보내 모델을 다운로드.

    스트리밍 응답으로 다운로드 진행 상황을 받으며, 최대 1시간(3600초) 타임아웃을 적용합니다.
    Returns: 다운로드 성공 시 True, 실패 시 False.
    """
    settings = get_settings()
    url = f"{settings.ollama.host}/api/pull"
    logger.info(f"Pulling model: {model_name} (this may take a while...)")

    resp = requests.post(
        url,
        json={"name": model_name},
        stream=True,
        timeout=3600,
    )

    if resp.status_code != 200:
        logger.error(f"Failed to pull model: {resp.status_code}")
        return False

    for line in resp.iter_lines():
        if line:
            logger.debug(line.decode())

    logger.info(f"Model {model_name} pulled successfully")
    return True


def ensure_model() -> bool:
    """
    Settings에 설정된 LLM 모델이 Ollama에 설치되어 있는지 확인하고, 없으면 pull.

    모델 이름 비교 시 태그(예: :7.8b) 포함/미포함 모두 매칭하여
    유사한 버전이 있으면 사용 가능한 것으로 판단합니다.
    Returns: 모델 사용 가능 시 True, pull 실패 시 False.
    """
    settings = get_settings()
    model = settings.ollama.model
    installed = list_models()

    # 모델 이름은 태그 포함/미포함 모두 매칭
    for m in installed:
        if m == model or m.startswith(model.split(":")[0]):
            logger.info(f"Model already available: {m}")
            return True

    logger.info(f"Model {model} not found. Pulling...")
    return pull_model(model)


def check_ollama() -> bool:
    """
    Ollama 서버 연결 대기와 모델 준비 상태를 한 번에 확인하는 통합 함수.

    wait_for_ollama()로 서버 응답을 확인한 뒤, ensure_model()로 모델 존재를 확인합니다.
    Returns: 서버 연결 및 모델 준비 모두 성공 시 True, 어느 하나라도 실패 시 False.
    """
    if not wait_for_ollama():
        return False
    return ensure_model()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = check_ollama()
    sys.exit(0 if success else 1)
