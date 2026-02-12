"""
DataBridge 앱 진입점.
1. 설정 로드
2. DB 연결 확인
3. ChromaDB 연결 확인
4. Ollama 연결 + 모델 확인
5. File Watcher 시작
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
    """기동 시 필수 서비스 연결 확인."""
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
