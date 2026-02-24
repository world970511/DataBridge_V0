"""
watchdog 라이브러리 기반 공유 폴더 실시간 감시 모듈.

지정된 디렉토리(하위 폴더 포함)에서 파일 생성(on_created), 수정(on_modified),
삭제(on_deleted), 이동(on_moved) 이벤트를 감지합니다.

- 생성/수정: 임시 파일 필터링 + 2초 디바운스 후 classifier.classify_file()로 전달
- 삭제: cleanup.cleanup_file()로 관련 DB/ChromaDB 리소스 정리
- 이동: 기존 경로 정리 + 새 경로로 재처리 (삭제 + 생성)
"""

import logging
import time
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from config.settings import get_settings
from watcher.classifier import classify_file

logger = logging.getLogger(__name__)


class FileEventHandler(FileSystemEventHandler):
    """
    파일 생성, 수정, 삭제, 이동 이벤트를 감지하여 적절한 파이프라인으로 전달하는 핸들러.

    동일 파일에 대해 짧은 시간 내 중복 이벤트가 발생할 수 있으므로
    debounce_seconds(기본 2초) 간격 내 중복 이벤트는 무시합니다.
    임시 파일(~$, . 접두사)도 처리 대상에서 제외합니다.
    """

    def __init__(self, debounce_seconds: float = 2.0):
        super().__init__()
        self._debounce = debounce_seconds
        self._last_seen: dict[str, float] = {}

    def _should_process(self, path: str) -> bool:
        """
        동일 파일에 대해 debounce 시간(기본 2초) 이내의 중복 이벤트를 무시.

        마지막 처리 시각을 _last_seen 딕셔너리에 기록하여 비교합니다.
        Returns: 처리해야 하면 True, 중복이면 False.
        """
        now = time.time()
        last = self._last_seen.get(path, 0)
        if now - last < self._debounce:
            return False
        self._last_seen[path] = now
        return True

    @staticmethod
    def _is_temp_file(file_path: str) -> bool:
        """임시 파일 여부를 판별. ~$ 또는 . 으로 시작하는 파일은 무시 대상."""
        name = Path(file_path).name
        return name.startswith("~$") or name.startswith(".")

    def on_created(self, event: FileSystemEvent):
        if event.is_directory:
            return
        self._handle(event.src_path)

    def on_modified(self, event: FileSystemEvent):
        if event.is_directory:
            return
        self._handle(event.src_path)

    def on_deleted(self, event: FileSystemEvent):
        """파일 삭제 이벤트를 감지하여 정리 파이프라인으로 전달."""
        if event.is_directory:
            return
        self._handle_delete(event.src_path)

    def on_moved(self, event: FileSystemEvent):
        """
        파일 이동/이름변경 이벤트를 감지.

        기존 경로의 리소스를 정리(삭제)하고, 새 경로로 재처리(생성)합니다.
        """
        if event.is_directory:
            return
        self._handle_delete(event.src_path)
        self._handle(event.dest_path)

    def _handle(self, file_path: str):
        """
        파일 생성/수정 이벤트의 실제 처리를 수행.

        디바운스 확인, 실제 파일 존재 여부 확인, 임시 파일 필터링을 거친 후
        classify_file()을 호출하여 파일 유형별 로더로 라우팅합니다.
        """
        if not self._should_process(file_path):
            return

        path = Path(file_path)
        if not path.is_file():
            return

        if self._is_temp_file(file_path):
            return

        logger.info(f"File detected: {file_path}")
        try:
            classify_file(file_path)
        except Exception:
            logger.exception(f"Error processing file: {file_path}")

    def _handle_delete(self, file_path: str):
        """
        파일 삭제 시 관련 DB/ChromaDB 리소스를 정리.

        파일이 이미 삭제된 상태이므로 is_file() 체크를 하지 않고,
        classifier.get_file_action()으로 파일명 패턴 기반 액션을 결정하여
        cleanup 모듈로 전달합니다.
        """
        if self._is_temp_file(file_path):
            return

        logger.info(f"File deleted: {file_path}")
        try:
            from watcher.classifier import get_file_action
            from watcher.cleanup import cleanup_file

            action = get_file_action(file_path)
            if action != "ignore":
                cleanup_file(file_path, action)
        except Exception:
            logger.exception(f"Error cleaning up deleted file: {file_path}")


def start_watcher(watch_dir: Optional[str] = None, blocking: bool = True):
    """
    지정 디렉토리에 대한 파일 감시(Observer)를 시작.

    watch_dir이 None이면 Settings의 watch_dir을 사용하고, 디렉토리가 없으면 자동 생성합니다.
    하위 폴더 포함(recursive=True)으로 감시하며, blocking=True이면 메인 스레드에서
    observer가 종료될 때까지 대기하고, False이면 Observer 객체를 반환합니다.
    Returns: blocking=False일 때 Observer 인스턴스, blocking=True일 때 None.
    """
    if watch_dir is None:
        watch_dir = get_settings().watcher.watch_dir

    watch_path = Path(watch_dir)
    if not watch_path.exists():
        logger.warning(f"Watch directory does not exist, creating: {watch_dir}")
        watch_path.mkdir(parents=True, exist_ok=True)

    handler = FileEventHandler()
    observer = PollingObserver(timeout=5)
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
