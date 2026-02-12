"""
watchdog 기반 공유 폴더 감시.
파일 생성/수정 이벤트 → classifier → loader 파이프라인.
"""

import logging
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from config.settings import get_settings
from watcher.classifier import classify_file

logger = logging.getLogger(__name__)


class FileEventHandler(FileSystemEventHandler):
    """파일 생성·수정 이벤트를 처리."""

    def __init__(self, debounce_seconds: float = 2.0):
        super().__init__()
        self._debounce = debounce_seconds
        self._last_seen: dict[str, float] = {}

    def _should_process(self, path: str) -> bool:
        """짧은 시간 내 중복 이벤트 무시 (debounce)."""
        now = time.time()
        last = self._last_seen.get(path, 0)
        if now - last < self._debounce:
            return False
        self._last_seen[path] = now
        return True

    def on_created(self, event: FileSystemEvent):
        if event.is_directory:
            return
        self._handle(event.src_path)

    def on_modified(self, event: FileSystemEvent):
        if event.is_directory:
            return
        self._handle(event.src_path)

    def _handle(self, file_path: str):
        if not self._should_process(file_path):
            return

        path = Path(file_path)
        if not path.is_file():
            return

        # 임시 파일 무시
        if path.name.startswith("~$") or path.name.startswith("."):
            return

        logger.info(f"File detected: {file_path}")
        try:
            classify_file(file_path)
        except Exception:
            logger.exception(f"Error processing file: {file_path}")


def start_watcher(watch_dir: str | None = None, blocking: bool = True):
    """폴더 감시 시작."""
    if watch_dir is None:
        watch_dir = get_settings().watcher.watch_dir

    watch_path = Path(watch_dir)
    if not watch_path.exists():
        logger.warning(f"Watch directory does not exist, creating: {watch_dir}")
        watch_path.mkdir(parents=True, exist_ok=True)

    handler = FileEventHandler()
    observer = Observer()
    observer.schedule(handler, str(watch_path), recursive=True)
    observer.start()
    logger.info(f"File watcher started: {watch_dir}")

    if blocking:
        try:
            while observer.is_alive():
                observer.join(timeout=1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
    else:
        return observer
