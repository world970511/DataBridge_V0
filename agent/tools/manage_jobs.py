"""
배치 작업 관리 도구.

SQL 기반 배치 작업의 생성·조회·활성화/비활성화·수동 실행·삭제를 담당합니다.
cron 표현식으로 주기적 실행 일정을 설정하며, 실행 이력을 batch_job_history에 기록합니다.

배치 작업은 주로 데이터 마트 갱신, 정기 집계, 데이터 정리 등에 사용됩니다.
실제 스케줄링은 jobs.scheduler 모듈이 담당하며, 이 모듈은 CRUD + 수동 실행 기능을 제공합니다.

DB 스키마 (init.sql에 이미 존재):
    batch_jobs:
        id, job_name, description, sql_text, cron_expr,
        is_active, last_run_at, last_status, created_by, created_at, updated_at

    batch_job_history:
        id, job_id, started_at, finished_at, status,
        rows_affected, error_message, execution_time

의존 모듈:
    - db.connection: execute_query(), execute_command(), get_cursor() — DB 접근
    - agent._audit: log_action() — 감사 로그
    - agent.tools.query_db: execute_write() — SQL 실행 (배치)

사용 예시:
    from agent.tools.manage_jobs import create_job, list_jobs, run_job

    job_id = create_job(
        job_name="daily_sales_refresh",
        description="일별 매출 마트 갱신",
        sql_text="CREATE TABLE mart_daily_sales AS SELECT ...",
        cron_expr="0 7 * * *",
        created_by="kim",
    )

    jobs = list_jobs()
    result = run_job(job_id)
"""

import logging
import time
from typing import Optional

from db.connection import execute_query, execute_command, get_cursor
from agent._audit import log_action

logger = logging.getLogger(__name__)


def create_job(
    job_name: str,
    description: str,
    sql_text: str,
    cron_expr: str,
    created_by: str = "system",
) -> Optional[int]:
    """
    배치 작업을 생성하여 batch_jobs에 등록.

    Args:
        job_name: 고유 작업명 (예: "daily_sales_refresh").
        description: 작업 설명.
        sql_text: 실행할 SQL 문.
        cron_expr: cron 표현식 (예: "0 7 * * *" = 매일 07:00).
        created_by: 생성자 username.

    Returns:
        생성된 작업 ID. 실패 시 None.
    """
    # 기본 검증
    if not job_name or not job_name.strip():
        logger.error("Job name is empty")
        return None

    if not sql_text or not sql_text.strip():
        logger.error("SQL text is empty")
        return None

    if not _validate_cron(cron_expr):
        logger.error(f"Invalid cron expression: {cron_expr}")
        return None

    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(
                """
                INSERT INTO batch_jobs
                    (job_name, description, sql_text, cron_expr, is_active, created_by)
                VALUES (%s, %s, %s, %s, TRUE, %s)
                RETURNING id
                """,
                (job_name.strip(), description, sql_text.strip(), cron_expr.strip(), created_by),
            )
            row = cur.fetchone()
            job_id = row["id"] if row else None

        if job_id:
            log_action(
                action_type="job_create",
                query_text=f"배치 작업 생성: {job_name}",
                sql_generated=sql_text,
                status="success",
                user_id=created_by,
                metadata={
                    "job_id": job_id,
                    "job_name": job_name,
                    "cron_expr": cron_expr,
                },
            )
            logger.info(f"Batch job created: id={job_id}, name={job_name}, cron={cron_expr}")

        return job_id

    except Exception as e:
        logger.error(f"Failed to create batch job: {e}")
        return None


def list_jobs(active_only: bool = False) -> list[dict]:
    """
    등록된 배치 작업 목록을 조회.

    Args:
        active_only: True이면 활성화된 작업만 반환.

    Returns:
        배치 작업 딕셔너리 리스트 (최신순).
    """
    try:
        sql = """
            SELECT id, job_name, description, sql_text, cron_expr,
                   is_active, last_run_at, last_status,
                   created_by, created_at, updated_at
            FROM batch_jobs
        """
        if active_only:
            sql += " WHERE is_active = TRUE"
        sql += " ORDER BY created_at DESC"

        return [dict(row) for row in execute_query(sql)]
    except Exception as e:
        logger.error(f"Failed to list batch jobs: {e}")
        return []


def get_job(job_id: int) -> Optional[dict]:
    """
    특정 배치 작업의 상세 정보를 조회.

    Args:
        job_id: 작업 ID.

    Returns:
        작업 딕셔너리. 없으면 None.
    """
    try:
        rows = execute_query(
            "SELECT * FROM batch_jobs WHERE id = %s", (job_id,)
        )
        return dict(rows[0]) if rows else None
    except Exception as e:
        logger.error(f"Failed to get job {job_id}: {e}")
        return None


def get_job_by_name(job_name: str) -> Optional[dict]:
    """
    작업명으로 배치 작업을 조회.

    Args:
        job_name: 작업명.

    Returns:
        작업 딕셔너리. 없으면 None.
    """
    try:
        rows = execute_query(
            "SELECT * FROM batch_jobs WHERE job_name = %s", (job_name,)
        )
        return dict(rows[0]) if rows else None
    except Exception as e:
        logger.error(f"Failed to get job by name '{job_name}': {e}")
        return None


def update_job(
    job_id: int,
    description: Optional[str] = None,
    sql_text: Optional[str] = None,
    cron_expr: Optional[str] = None,
) -> bool:
    """
    배치 작업을 수정.

    None인 필드는 변경하지 않습니다.

    Args:
        job_id: 수정할 작업 ID.
        description: 새 설명 (None이면 변경 안 함).
        sql_text: 새 SQL (None이면 변경 안 함).
        cron_expr: 새 cron 표현식 (None이면 변경 안 함).

    Returns:
        성공 시 True.
    """
    if cron_expr is not None and not _validate_cron(cron_expr):
        logger.error(f"Invalid cron expression: {cron_expr}")
        return False

    updates = []
    params = []

    if description is not None:
        updates.append("description = %s")
        params.append(description)
    if sql_text is not None:
        updates.append("sql_text = %s")
        params.append(sql_text.strip())
    if cron_expr is not None:
        updates.append("cron_expr = %s")
        params.append(cron_expr.strip())

    if not updates:
        return True  # 변경할 것 없음

    updates.append("updated_at = NOW()")
    params.append(job_id)

    try:
        affected = execute_command(
            f"UPDATE batch_jobs SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        if affected > 0:
            logger.info(f"Batch job updated: id={job_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to update job {job_id}: {e}")
        return False


def toggle_job(job_id: int, active: bool) -> bool:
    """
    배치 작업 활성화/비활성화.

    Args:
        job_id: 작업 ID.
        active: True면 활성화, False면 비활성화.

    Returns:
        성공 시 True.
    """
    try:
        affected = execute_command(
            "UPDATE batch_jobs SET is_active = %s, updated_at = NOW() WHERE id = %s",
            (active, job_id),
        )
        if affected > 0:
            state = "activated" if active else "deactivated"
            logger.info(f"Batch job {state}: id={job_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to toggle job {job_id}: {e}")
        return False


def delete_job(job_id: int) -> bool:
    """
    배치 작업을 삭제 (batch_job_history도 CASCADE 삭제).

    Args:
        job_id: 삭제할 작업 ID.

    Returns:
        성공 시 True.
    """
    try:
        affected = execute_command(
            "DELETE FROM batch_jobs WHERE id = %s", (job_id,)
        )
        if affected > 0:
            logger.info(f"Batch job deleted: id={job_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Failed to delete job {job_id}: {e}")
        return False


def run_job(job_id: int) -> dict:
    """
    배치 작업을 수동으로 즉시 실행.

    batch_job_history에 실행 이력을 기록하고,
    batch_jobs의 last_run_at, last_status를 갱신합니다.

    Args:
        job_id: 실행할 작업 ID.

    Returns:
        실행 결과:
        {
            "success": bool,
            "message": str,
            "rows_affected": int,
            "execution_time": float,  — 초 단위
            "history_id": int | None,
        }
    """
    job = get_job(job_id)
    if not job:
        return {
            "success": False,
            "message": f"작업을 찾을 수 없습니다: id={job_id}",
            "rows_affected": 0,
            "execution_time": 0.0,
            "history_id": None,
        }

    job_name = job["job_name"]
    sql_text = job["sql_text"]

    # 이력 레코드 생성 (status=running)
    history_id = _create_history(job_id, status="running")

    start_time = time.time()

    try:
        # SQL 실행
        from agent.tools.query_db import execute_write
        exec_result = execute_write(sql_text)

        elapsed = time.time() - start_time
        rows_affected = exec_result.get("rows_affected", 0)

        if exec_result["success"]:
            # 성공: 이력 + 작업 상태 갱신
            _finish_history(history_id, "success", rows_affected, elapsed)
            _update_job_status(job_id, "success")

            message = f"배치 작업 '{job_name}' 실행 완료: {rows_affected}행 영향, {elapsed:.1f}초"
            logger.info(message)

            log_action(
                action_type="job_execute",
                query_text=f"배치 작업 실행: {job_name}",
                sql_generated=sql_text,
                result_summary=message,
                status="success",
                metadata={"job_id": job_id, "rows_affected": rows_affected},
            )

            return {
                "success": True,
                "message": message,
                "rows_affected": rows_affected,
                "execution_time": elapsed,
                "history_id": history_id,
            }
        else:
            # SQL 실행 실패
            elapsed = time.time() - start_time
            error_msg = exec_result.get("error", "Unknown error")
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

    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = str(e).strip()
        _finish_history(history_id, "failed", 0, elapsed, error_msg)
        _update_job_status(job_id, "failed")

        logger.error(f"Batch job execution error: job_id={job_id}, error={error_msg}")

        return {
            "success": False,
            "message": f"배치 작업 실행 중 오류: {error_msg}",
            "rows_affected": 0,
            "execution_time": elapsed,
            "history_id": history_id,
        }


def get_job_history(job_id: int, limit: int = 20) -> list[dict]:
    """
    특정 작업의 실행 이력을 조회.

    Args:
        job_id: 작업 ID.
        limit: 최대 반환 건수.

    Returns:
        실행 이력 딕셔너리 리스트 (최신순).
    """
    try:
        return [
            dict(row) for row in execute_query(
                """
                SELECT id, job_id, started_at, finished_at,
                       status, rows_affected, error_message, execution_time
                FROM batch_job_history
                WHERE job_id = %s
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (job_id, limit),
            )
        ]
    except Exception as e:
        logger.error(f"Failed to get job history for job_id={job_id}: {e}")
        return []


def get_recent_history(limit: int = 50) -> list[dict]:
    """
    전체 배치 작업의 최근 실행 이력을 조회.

    Returns:
        실행 이력 딕셔너리 리스트 (최신순).
    """
    try:
        return [
            dict(row) for row in execute_query(
                """
                SELECT h.id, h.job_id, j.job_name, h.started_at, h.finished_at,
                       h.status, h.rows_affected, h.error_message, h.execution_time
                FROM batch_job_history h
                JOIN batch_jobs j ON h.job_id = j.id
                ORDER BY h.started_at DESC
                LIMIT %s
                """,
                (limit,),
            )
        ]
    except Exception as e:
        logger.error(f"Failed to get recent job history: {e}")
        return []


# ── 내부 헬퍼 함수들 ──


def _create_history(job_id: int, status: str = "running") -> Optional[int]:
    """batch_job_history에 실행 이력 레코드를 생성."""
    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(
                """
                INSERT INTO batch_job_history (job_id, status)
                VALUES (%s, %s)
                RETURNING id
                """,
                (job_id, status),
            )
            row = cur.fetchone()
            return row["id"] if row else None
    except Exception as e:
        logger.error(f"Failed to create job history: {e}")
        return None


def _finish_history(
    history_id: Optional[int],
    status: str,
    rows_affected: int,
    execution_time: float,
    error_message: str = None,
):
    """batch_job_history의 실행 이력 레코드를 완료 상태로 갱신."""
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
        logger.error(f"Failed to finish job history {history_id}: {e}")


def _update_job_status(job_id: int, status: str):
    """batch_jobs의 last_run_at, last_status를 갱신."""
    try:
        execute_command(
            """
            UPDATE batch_jobs
            SET last_run_at = NOW(), last_status = %s, updated_at = NOW()
            WHERE id = %s
            """,
            (status, job_id),
        )
    except Exception as e:
        logger.error(f"Failed to update job status {job_id}: {e}")


def _validate_cron(cron_expr: str) -> bool:
    """
    cron 표현식의 기본 형식을 검증.

    5개 필드(분 시 일 월 요일)를 기대합니다.
    각 필드는 숫자, *, /, -, , 를 허용합니다.

    Args:
        cron_expr: 검증할 cron 표현식.

    Returns:
        유효하면 True.
    """
    if not cron_expr or not cron_expr.strip():
        return False

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False

    import re
    cron_field_re = re.compile(r"^[\d\*\/\-\,]+$")

    for part in parts:
        if not cron_field_re.match(part):
            return False

    return True
