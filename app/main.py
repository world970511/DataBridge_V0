"""
DataBridge 애플리케이션 진입점 모듈.

앱 시작 시 아래 순서로 필수 서비스 상태를 검증한 뒤 파일 감시를 시작합니다:
1. 환경 변수 기반 설정 로드 (config/settings.py)
2. PostgreSQL 연결 확인 → 실패 시 앱 종료
3. ChromaDB 연결 확인 → 실패 시 앱 종료
4. Ollama LLM 연결 및 모델 존재 확인 → 실패 시 경고만 출력 (에이전트 기능 제한)
5. File Watcher를 데몬 스레드로 실행하여 공유 폴더를 감시

메인 스레드는 watcher 스레드가 살아있는 동안 대기하며,
Ctrl+C(KeyboardInterrupt) 입력 시 정상 종료됩니다.
"""

import logging
import sys
import threading

from config.settings import get_settings
from db.connection import check_connection as check_db
from rag.embedder import check_chroma_connection
from scripts.check_ollama import check_ollama
from watcher.file_watcher import start_watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("databridge")


def startup_checks():
    """
    앱 기동 시 PostgreSQL, ChromaDB, Ollama 세 가지 필수 서비스에 대해 연결 상태를 순서대로 확인.

    PostgreSQL 또는 ChromaDB 연결 실패 시 sys.exit(1)로 앱을 종료하고,
    Ollama 실패 시에는 경고 로그만 남기고 계속 진행합니다.
    """
    logger.info("=== DataBridge Startup ===")

    settings = get_settings()
    logger.info(f"Watch dir: {settings.watcher.watch_dir}")
    logger.info(f"LLM model: {settings.ollama.model}")

    # 1. PostgreSQL
    logger.info("Checking PostgreSQL...")
    if not check_db():
        logger.error("PostgreSQL connection failed!")
        sys.exit(1)
    logger.info("PostgreSQL: OK")

    # 2. ChromaDB
    logger.info("Checking ChromaDB...")
    if not check_chroma_connection():
        logger.error("ChromaDB connection failed!")
        sys.exit(1)
    logger.info("ChromaDB: OK")

    # 3. Ollama
    logger.info("Checking Ollama...")
    if not check_ollama():
        logger.warning("Ollama check failed — agent features may not work")
    else:
        logger.info("Ollama: OK")


def main():
    """
    DataBridge 메인 실행 함수.

    startup_checks()로 서비스 상태를 확인한 후, File Watcher를
    데몬 스레드로 실행하여 설정된 watch_dir을 감시합니다.
    메인 스레드는 watcher_thread.join()으로 대기하다가
    KeyboardInterrupt 발생 시 종료됩니다.
    """
    startup_checks()

    settings = get_settings()

    # File Watcher를 별도 스레드에서 실행
    watcher_thread = threading.Thread(
        target=start_watcher,
        kwargs={"watch_dir": settings.watcher.watch_dir, "blocking": True},
        daemon=True,
    )
    watcher_thread.start()
    logger.info("File watcher started in background")

    # 메인 스레드는 watcher가 살아있는 동안 대기
    # (추후 Streamlit UI가 여기에 추가됨)
    logger.info("=== DataBridge is running ===")
    try:
        watcher_thread.join()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
