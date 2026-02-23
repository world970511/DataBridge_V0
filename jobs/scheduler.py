"""
배치 작업 스케줄러.

batch_jobs 테이블에 등록된 활성 작업을 cron 표현식에 따라 주기적으로 실행합니다.
스레드 기반 스케줄러로 동작하며, 앱 시작 시 start()로 시작하고 종료 시 stop()으로 중지합니다.

외부 의존 없이 threading + time 기반으로 동작합니다.
(APScheduler가 설치되어 있으면 고급 스케줄링 사용 가능하지만, 폐쇄망 환경을 고려하여 기본 구현 제공)

동작 방식:
    1. 매 60초마다 batch_jobs에서 활성 작업 목록을 조회
    2. 각 작업의 cron_expr과 현재 시각을 비교
    3. 실행 시각이 맞으면 executor.execute_job()으로 실행
    4. 동시 실행 방지: 이미 실행 중인 작업은 건너뜀

사용 예시:
    from jobs.scheduler import start, stop

    # 앱 시작 시
    start()

    # 앱 종료 시
    stop()
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 스케줄러 상태
_scheduler_thread: Optional[threading.Thread] = None
_running = threading.Event()
_running_jobs: set[int] = set()  # 현재 실행 중인 job_id
_running_lock = threading.Lock()

# 체크 간격 (초)
_CHECK_INTERVAL = 60


def start():
    """
    배치 스케줄러를 시작.

    별도 데몬 스레드로 실행되어 앱이 종료되면 자동으로 중지됩니다.
    이미 실행 중이면 아무 동작도 하지 않습니다.
    """
    global _scheduler_thread

    if _running.is_set():
        logger.warning("Batch scheduler is already running")
        return

    _running.set()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        name="batch-scheduler",
        daemon=True,
    )
    _scheduler_thread.start()
    logger.info("Batch scheduler started")


def stop():
    """
    배치 스케줄러를 중지.

    현재 실행 중인 작업이 완료될 때까지 기다리지 않습니다.
    """
    global _scheduler_thread

    if not _running.is_set():
        return

    _running.clear()
    if _scheduler_thread and _scheduler_thread.is_alive():
        _scheduler_thread.join(timeout=10)
    _scheduler_thread = None
    logger.info("Batch scheduler stopped")


def is_running() -> bool:
    """스케줄러 실행 여부를 반환."""
    return _running.is_set()


def _scheduler_loop():
    """
    스케줄러 메인 루프.

    매 _CHECK_INTERVAL초마다 활성 작업을 확인하고 실행 시각이 맞으면 실행합니다.
    """
    logger.info(f"Scheduler loop started (check interval: {_CHECK_INTERVAL}s)")

    # 시작 후 첫 체크 전 대기 (DB 초기화 여유)
    time.sleep(5)

    while _running.is_set():
        try:
            _check_and_run_jobs()
        except Exception as e:
            logger.error(f"Scheduler check failed: {e}")

        # _CHECK_INTERVAL 동안 1초 단위로 대기 (stop() 시 빠른 응답)
        for _ in range(_CHECK_INTERVAL):
            if not _running.is_set():
                break
            time.sleep(1)


def _check_and_run_jobs():
    """활성 작업을 확인하고 실행 시각이 맞는 작업을 실행."""
    from db.connection import execute_query

    try:
        jobs = execute_query(
            """
            SELECT id, job_name, cron_expr, last_run_at
            FROM batch_jobs
            WHERE is_active = TRUE
            """
        )
    except Exception as e:
        logger.error(f"Failed to fetch active jobs: {e}")
        return

    now = datetime.now()

    for job in jobs:
        job_id = job["id"]
        job_name = job["job_name"]
        cron_expr = job["cron_expr"]
        last_run = job["last_run_at"]

        # cron 매칭 확인
        if not _cron_matches(cron_expr, now):
            continue

        # 같은 분에 이미 실행했는지 확인
        if last_run:
            # timezone-aware 비교를 위해 분 단위로 비교
            if hasattr(last_run, 'replace'):
                last_run_naive = last_run.replace(tzinfo=None) if last_run.tzinfo else last_run
                if (last_run_naive.year == now.year and
                    last_run_naive.month == now.month and
                    last_run_naive.day == now.day and
                    last_run_naive.hour == now.hour and
                    last_run_naive.minute == now.minute):
                    continue  # 이미 이 분에 실행됨

        # 동시 실행 방지
        with _running_lock:
            if job_id in _running_jobs:
                logger.debug(f"Job already running: {job_name} (id={job_id})")
                continue
            _running_jobs.add(job_id)

        # 별도 스레드에서 실행
        thread = threading.Thread(
            target=_run_job_thread,
            args=(job_id, job_name),
            name=f"job-{job_id}-{job_name}",
            daemon=True,
        )
        thread.start()
        logger.info(f"Scheduled job started: {job_name} (id={job_id})")


def _run_job_thread(job_id: int, job_name: str):
    """작업 실행 스레드."""
    try:
        from jobs.executor import execute_job
        result = execute_job(job_id)

        if result["success"]:
            logger.info(f"Scheduled job completed: {job_name} (id={job_id})")
        else:
            logger.warning(
                f"Scheduled job failed: {job_name} (id={job_id}), "
                f"error={result['message']}"
            )
    except Exception as e:
        logger.error(f"Scheduled job exception: {job_name} (id={job_id}), error={e}")
    finally:
        with _running_lock:
            _running_jobs.discard(job_id)


def _cron_matches(cron_expr: str, dt: datetime) -> bool:
    """
    간단한 cron 표현식 매칭.

    5개 필드: 분 시 일 월 요일 (0=일요일 또는 7=일요일)
    지원 형식: 숫자, *, */N, N-M, N,M,...

    Args:
        cron_expr: cron 표현식 (예: "0 7 * * *")
        dt: 비교할 datetime.

    Returns:
        현재 시각이 cron 표현식과 매칭되면 True.
    """
    try:
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return False

        minute, hour, day, month, dow = parts

        return (
            _field_matches(minute, dt.minute, 0, 59) and
            _field_matches(hour, dt.hour, 0, 23) and
            _field_matches(day, dt.day, 1, 31) and
            _field_matches(month, dt.month, 1, 12) and
            _field_matches(dow, dt.weekday(), 0, 6)
            # Python weekday: 0=월요일, cron 표준: 0=일요일
            # 여기서는 Python 기준으로 매칭 (0=월, 6=일)
        )
    except Exception:
        return False


def _field_matches(field: str, value: int, min_val: int, max_val: int) -> bool:
    """
    cron 필드 하나가 값과 매칭되는지 확인.

    지원 형식:
    - "*"       → 모든 값
    - "5"       → 정확히 5
    - "*/10"    → 0, 10, 20, 30, ...
    - "1-5"     → 1, 2, 3, 4, 5
    - "1,3,5"   → 1, 3, 5
    """
    if field == "*":
        return True

    # 콤마로 구분된 복수 값
    if "," in field:
        return any(_field_matches(sub.strip(), value, min_val, max_val) for sub in field.split(","))

    # 범위 (N-M)
    if "-" in field and "/" not in field:
        try:
            start, end = field.split("-")
            return int(start) <= value <= int(end)
        except ValueError:
            return False

    # 스텝 (*/N 또는 N-M/S)
    if "/" in field:
        try:
            base, step = field.split("/")
            step = int(step)
            if step <= 0:
                return False

            if base == "*":
                return value % step == 0
            elif "-" in base:
                start, end = base.split("-")
                start, end = int(start), int(end)
                if start <= value <= end:
                    return (value - start) % step == 0
                return False
            else:
                start = int(base)
                return value >= start and (value - start) % step == 0
        except (ValueError, ZeroDivisionError):
            return False

    # 정확한 값
    try:
        return value == int(field)
    except ValueError:
        return False
