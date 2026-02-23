"""
배치 작업 실행 엔진.

개별 배치 작업의 SQL을 실행하고 결과를 batch_job_history에 기록합니다.
스케줄러(scheduler.py)에서 호출되거나, manage_jobs.run_job()에서 수동 실행됩니다.

실행 흐름:
    1. batch_jobs에서 작업 정보 조회
    2. batch_job_history에 실행 시작 기록 (status=running)
    3. SQL 실행 (execute_command)
    4. 결과 기록 (성공/실패, rows_affected, execution_time)
    5. batch_jobs의 last_run_at, last_status 갱신

주의:
    - 마트 갱신 SQL (DROP + CREATE)은 단일 트랜잭션으로 처리
    - statement_timeout을 별도 설정 (배치는 일반 쿼리보다 길게)
    - 에러 발생 시 자동 rollback
"""

import logging
import time
from typing import Optional

from db.connection import get_cursor, execute_command, execute_query

logger = logging.getLogger(__name__)

# 배치 작업의 기본 statement_timeout (초)
_BATCH_TIMEOUT = 300  # 5분


def execute_job(job_id: int) -> dict:
    """
    배치 작업을 실행하고 이력을 기록.

    batch_jobs 테이블에서 SQL을 가져와 실행한 후,
    batch_job_history에 결과를 기록합니다.

    마트 갱신 패턴을 지원합니다:
    - 단일 SQL 실행 (CREATE TABLE AS SELECT, INSERT, UPDATE 등)
    - DROP + CREATE 복합 실행은 별도 처리 불필요 (execute_command가 트랜잭션 관리)

    Args:
        job_id: 실행할 작업 ID.

    Returns:
        {
            "success": bool,
            "message": str,
            "rows_affected": int,
            "execution_time": float,
            "history_id": int | None,
        }
    """
    # 작업 정보 조회
    try:
        rows = execute_query(
            "SELECT id, job_name, sql_text, is_active FROM batch_jobs WHERE id = %s",
            (job_id,),
        )
    except Exception as e:
        return _exec_error(f"작업 조회 실패: {e}", job_id)

    if not rows:
        return _exec_error(f"작업을 찾을 수 없습니다: id={job_id}", job_id)

    job = rows[0]
    job_name = job["job_name"]
    sql_text = job["sql_text"]

    if not job["is_active"]:
        logger.info(f"Skipping inactive job: {job_name} (id={job_id})")
        return {
            "success": False,
            "message": f"비활성화된 작업입니다: {job_name}",
            "rows_affected": 0,
            "execution_time": 0.0,
            "history_id": None,
        }

    # 이력 레코드 생성
    history_id = _create_history(job_id)

    # SQL 실행
    start_time = time.time()

    try:
        with get_cursor() as cur:
            # 배치 전용 타임아웃 설정
            cur.execute(f"SET statement_timeout = {_BATCH_TIMEOUT * 1000}")
            cur.execute(sql_text)
            rows_affected = cur.rowcount if cur.rowcount >= 0 else 0

        elapsed = time.time() - start_time

        # 성공 기록
        _finish_history(history_id, "success", rows_affected, elapsed)
        _update_job_status(job_id, "success")

        message = f"배치 작업 '{job_name}' 완료: {rows_affected}행, {elapsed:.1f}초"
        logger.info(message)

        return {
            "success": True,
            "message": message,
            "rows_affected": rows_affected,
            "execution_time": elapsed,
            "history_id": history_id,
        }

    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = str(e).strip()

        # 실패 기록
        _finish_history(history_id, "failed", 0, elapsed, error_msg)
        _update_job_status(job_id, "failed")

        message = f"배치 작업 '{job_name}' 실패: {error_msg}"
        logger.error(message)

        return {
            "success": False,
            "message": message,
            "rows_affected": 0,
            "execution_time": elapsed,
            "history_id": history_id,
        }


def _create_history(job_id: int) -> Optional[int]:
    """실행 이력 레코드 생성."""
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(
                "INSERT INTO batch_job_history (job_id, status) VALUES (%s, 'running') RETURNING id",
                (job_id,),
            )
            row = cur.fetchone()
            return row["id"] if row else None
    except Exception as e:
        logger.error(f"Failed to create history: {e}")
        return None


def _finish_history(
    history_id: Optional[int],
    status: str,
    rows_affected: int,
    execution_time: float,
    error_message: str = None,
):
    """실행 이력 레코드를 완료 상태로 갱신."""
    if not history_id:
        return
    try:
        execute_command(
            """
            UPDATE batch_job_history
            SET finished_at = NOW(), status = %s,
                rows_affected = %s, execution_time = %s, error_message = %s
            WHERE id = %s
            """,
            (status, rows_affected, execution_time, error_message, history_id),
        )
    except Exception as e:
        logger.error(f"Failed to finish history {history_id}: {e}")


def _update_job_status(job_id: int, status: str):
    """작업의 마지막 실행 상태를 갱신."""
    try:
        execute_command(
            "UPDATE batch_jobs SET last_run_at = NOW(), last_status = %s, updated_at = NOW() WHERE id = %s",
            (status, job_id),
        )
    except Exception as e:
        logger.error(f"Failed to update job status {job_id}: {e}")


def _exec_error(message: str, job_id: int) -> dict:
    """에러 결과 딕셔너리."""
    logger.error(f"Job execution error: job_id={job_id}, {message}")
    return {
        "success": False,
        "message": message,
        "rows_affected": 0,
        "execution_time": 0.0,
        "history_id": None,
    }
