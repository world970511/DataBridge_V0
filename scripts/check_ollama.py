"""
Ollama 연결 확인 + 모델 자동 pull 스크립트.
앱 시작 시 또는 독립 실행 가능.
"""

import logging
import sys
import time

import requests

from config.settings import get_settings

logger = logging.getLogger(__name__)


def wait_for_ollama(timeout: int = 120) -> bool:
    """Ollama 서버가 준비될 때까지 대기."""
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
    """현재 Ollama에 설치된 모델 목록."""
    settings = get_settings()
    url = f"{settings.ollama.host}/api/tags"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return [m["name"] for m in data.get("models", [])]


def pull_model(model_name: str) -> bool:
    """모델 pull (없으면 다운로드)."""
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
    """필요한 모델이 있는지 확인하고, 없으면 pull."""
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
    """Ollama 연결 + 모델 준비 상태 전체 확인."""
    if not wait_for_ollama():
        return False
    return ensure_model()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = check_ollama()
    sys.exit(0 if success else 1)
