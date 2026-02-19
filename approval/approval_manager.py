"""
SQL 승인 요청 관리 모듈.

위험 SQL(DROP/DELETE/TRUNCATE/ALTER)에 대한 승인 요청을 생성·조회·처리하고,
승인된 SQL을 실행하는 Human-in-the-Loop 워크플로우를 관리합니다.

approval_requests 테이블 스키마:
    id              SERIAL PRIMARY KEY
    request_type    VARCHAR(50) NOT NULL   -- 'sql_needs_approval' 등
    title           VARCHAR(500)           -- 요청 제목 (사용자 질의 요약)
    sql_text        TEXT NOT NULL          -- 실행할 SQL 문
    sql_category    VARCHAR(30)            -- SQL 위험도 분류 (SqlCategory)
    status          VARCHAR(20)            -- 'pending', 'approved', 'denied', 'executed'
    requested_by    VARCHAR(100)           -- 요청자 username
    reviewed_by     VARCHAR(100)           -- 승인/거부한 admin username
    reviewed_at     TIMESTAMPTZ            -- 승인/거부 시각
    result_summary  TEXT                   -- 실행 결과 요약
    metadata        JSONB                  -- 추가 컨텍스트
    created_at      TIMESTAMPTZ

의존 모듈:
    - db.connection: execute_query(), execute_command(), get_cursor() — PostgreSQL 접근
    - agent._audit: log_action() — 감사 로그 기록

사용 예시:
    from approval.approval_manager import create_request, approve_request, execute_approved

    # 유저가 위험 SQL 요청
    req_id = create_request(
        sql="DELETE FROM sales WHERE year < 2020",
        title="2020년 이전 매출 삭제",
        requested_by="kim",
        sql_category="NEEDS_APPROVAL",
    )

    # 관리자가 승인
    approve_request(req_id, reviewed_by="admin")

    # 승인된 SQL 실행
    result = execute_approved(req_id)
"""

import json
import logging
from typing import Optional

from db.connection import execute_query, execute_command, get_cursor

logger = logging.getLogger(__name__)


def _log_action(**kwargs):
    """
    agent._audit.log_action()을 지연 import하여 호출하는 래퍼.

    순환 import 방지를 위해 함수 호출 시점에 import합니다.
    (approval → agent._audit → agent.__init__ → agent.orchestrator → agent.sql_agent → approval 순환)
    log_action 실패 시에도 예외를 전파하지 않습니다.
    """
    try:
        from agent._audit import log_action
        log_action(**kwargs)
    except Exception:
        logger.debug("Audit log recording skipped (lazy import or DB unavailable)")


def create_request(
    sql: str,
    title: str = "",
    requested_by: str = "system",
    sql_category: str = "NEEDS_APPROVAL",
    request_type: str = "sql_needs_approval",
    metadata: Optional[dict] = None,
) -> Optional[int]:
    """
    승인 요청을 생성하여 approval_requests 테이블에 INSERT.

    Args:
        sql: 실행할 SQL 문.
        title: 요청 제목 (사용자 질의 요약 등).
        requested_by: 요청자 username.
        sql_category: SQL 위험도 분류 (SqlCategory 값).
        request_type: 요청 유형 ('sql_needs_approval' 등).
        metadata: 추가 컨텍스트 딕셔너리.

    Returns:
        생성된 요청의 ID (int). 실패 시 None.
    """
    try:
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

        with get_cursor(dict_cursor=True) as cur:
            cur.execute(
                """
                INSERT INTO approval_requests
                    (request_type, title, sql_text, sql_category, status, requested_by, metadata)
                VALUES (%s, %s, %s, %s, 'pending', %s, %s)
                RETURNING id
                """,
                (request_type, title, sql, sql_category, requested_by, metadata_json),
            )
            row = cur.fetchone()
            req_id = row["id"] if row else None

        if req_id:
            _log_action(
                action_type="approval_request",
                query_text=title,
                sql_generated=sql,
                status="pending",
                user_id=requested_by,
                metadata={"request_id": req_id, "sql_category": sql_category},
            )
            logger.info(
                f"Approval request created: id={req_id}, by={requested_by}, "
                f"category={sql_category}"
            )

        return req_id

    except Exception as e:
        logger.error(f"Failed to create approval request: {e}")
        return None


def list_pending() -> list[dict]:
    """
    대기 중(pending)인 승인 요청 목록을 조회.

    관리자 UI에서 승인/거부할 요청 목록을 표시할 때 사용합니다.

    Returns:
        승인 대기 중인 요청 딕셔너리 리스트 (최신순 정렬).
    """
    try:
        return [
            dict(row) for row in execute_query(
                """
                SELECT id, request_type, title, sql_text, sql_category,
                       status, requested_by, created_at, metadata
                FROM approval_requests
                WHERE status = 'pending'
                ORDER BY created_at DESC
                """
            )
        ]
    except Exception as e:
        logger.error(f"Failed to list pending requests: {e}")
        return []


def list_user_requests(username: str, limit: int = 20) -> list[dict]:
    """
    특정 사용자의 승인 요청 이력을 조회.

    일반 유저가 자신의 요청 상태를 확인할 때 사용합니다.

    Args:
        username: 조회할 사용자의 username.
        limit: 최대 반환 건수. 기본값 20.

    Returns:
        해당 사용자의 요청 딕셔너리 리스트 (최신순 정렬).
    """
    try:
        return [
            dict(row) for row in execute_query(
                """
                SELECT id, request_type, title, sql_text, sql_category,
                       status, requested_by, reviewed_by, reviewed_at,
                       result_summary, created_at
                FROM approval_requests
                WHERE requested_by = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (username, limit),
            )
        ]
    except Exception as e:
        logger.error(f"Failed to list user requests: {e}")
        return []


def approve_request(request_id: int, reviewed_by: str = "admin") -> bool:
    """
    승인 요청을 승인 상태로 변경.

    status를 'pending' → 'approved'로 업데이트하고 승인자 정보를 기록합니다.
    이미 pending이 아닌 요청은 승인되지 않습니다.

    Args:
        request_id: 승인할 요청 ID.
        reviewed_by: 승인하는 관리자 username.

    Returns:
        성공 시 True, 실패 시 False.
    """
    try:
        affected = execute_command(
            """
            UPDATE approval_requests
            SET status = 'approved', reviewed_by = %s, reviewed_at = NOW()
            WHERE id = %s AND status = 'pending'
            """,
            (reviewed_by, request_id),
        )

        if affected > 0:
            _log_action(
                action_type="approval_granted",
                status="success",
                user_id=reviewed_by,
                metadata={"request_id": request_id},
            )
            logger.info(f"Request approved: id={request_id}, by={reviewed_by}")
            return True
        else:
            logger.warning(
                f"Approve failed: id={request_id} — not found or not pending"
            )
            return False

    except Exception as e:
        logger.error(f"Failed to approve request {request_id}: {e}")
        return False


def deny_request(
    request_id: int,
    reviewed_by: str = "admin",
    reason: str = "",
) -> bool:
    """
    승인 요청을 거부 상태로 변경.

    status를 'pending' → 'denied'로 업데이트하고 거부 사유를 기록합니다.

    Args:
        request_id: 거부할 요청 ID.
        reviewed_by: 거부하는 관리자 username.
        reason: 거부 사유.

    Returns:
        성공 시 True, 실패 시 False.
    """
    try:
        affected = execute_command(
            """
            UPDATE approval_requests
            SET status = 'denied', reviewed_by = %s, reviewed_at = NOW(),
                result_summary = %s
            WHERE id = %s AND status = 'pending'
            """,
            (reviewed_by, reason or "관리자에 의해 거부됨", request_id),
        )

        if affected > 0:
            _log_action(
                action_type="approval_denied",
                status="success",
                user_id=reviewed_by,
                metadata={"request_id": request_id, "reason": reason},
            )
            logger.info(f"Request denied: id={request_id}, by={reviewed_by}")
            return True
        else:
            logger.warning(
                f"Deny failed: id={request_id} — not found or not pending"
            )
            return False

    except Exception as e:
        logger.error(f"Failed to deny request {request_id}: {e}")
        return False


def execute_approved(request_id: int) -> dict:
    """
    승인된 SQL 요청을 실행하고 결과를 기록.

    status가 'approved'인 요청만 실행합니다.
    실행 후 status를 'executed'로 변경하고 결과 요약을 저장합니다.

    Args:
        request_id: 실행할 요청 ID.

    Returns:
        실행 결과 딕셔너리:
        {
            "success": bool,
            "message": str,          — 결과 메시지
            "rows_affected": int,    — 영향받은 행 수 (해당 시)
        }
    """
    try:
        # 승인 상태 확인
        rows = execute_query(
            "SELECT id, sql_text, status, requested_by FROM approval_requests WHERE id = %s",
            (request_id,),
        )

        if not rows:
            return {"success": False, "message": "요청을 찾을 수 없습니다.", "rows_affected": 0}

        request = rows[0]

        if request["status"] != "approved":
            return {
                "success": False,
                "message": f"실행할 수 없는 상태입니다: {request['status']}",
                "rows_affected": 0,
            }

        # SQL 실행
        sql = request["sql_text"]
        affected = execute_command(sql)

        result_summary = f"실행 완료: {affected}행 영향받음"

        # 상태 업데이트 → executed
        execute_command(
            """
            UPDATE approval_requests
            SET status = 'executed', result_summary = %s
            WHERE id = %s
            """,
            (result_summary, request_id),
        )

        _log_action(
            action_type="approval_executed",
            sql_generated=sql,
            result_summary=result_summary,
            status="success",
            user_id=request["requested_by"],
            metadata={"request_id": request_id, "rows_affected": affected},
        )

        logger.info(f"Approved SQL executed: id={request_id}, affected={affected}")

        return {
            "success": True,
            "message": result_summary,
            "rows_affected": affected,
        }

    except Exception as e:
        error_msg = str(e).strip()
        logger.error(f"Failed to execute approved request {request_id}: {error_msg}")

        # 실행 실패도 기록
        try:
            execute_command(
                """
                UPDATE approval_requests
                SET result_summary = %s
                WHERE id = %s
                """,
                (f"실행 실패: {error_msg}", request_id),
            )
        except Exception:
            pass

        return {
            "success": False,
            "message": f"SQL 실행 실패: {error_msg}",
            "rows_affected": 0,
        }
