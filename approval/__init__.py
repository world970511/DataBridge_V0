"""
DataBridge SQL 승인 워크플로우 패키지.

SQL 문의 위험도를 4단계로 분류하고, 위험한 SQL(DROP/DELETE/TRUNCATE/ALTER)에 대해
관리자 승인을 요구하는 Human-in-the-Loop 워크플로우를 제공합니다.

4단계 SQL 분류:
    - SAFE: SELECT → 즉시 실행
    - AUTO_ALLOWED: CREATE TABLE, INSERT → 승인 없이 자동 실행
    - NEEDS_APPROVAL: DROP, DELETE, TRUNCATE, ALTER → 관리자 승인 후 실행
    - FORBIDDEN: GRANT, REVOKE, EXECUTE, COPY → 항상 차단
"""

from approval.sql_classifier import classify_sql, SqlCategory
from approval.approval_manager import (
    create_request,
    list_pending,
    list_user_requests,
    approve_request,
    deny_request,
    execute_approved,
)

__all__ = [
    "classify_sql",
    "SqlCategory",
    "create_request",
    "list_pending",
    "list_user_requests",
    "approve_request",
    "deny_request",
    "execute_approved",
]
