"""
DataBridge 승인 관리 페이지.

관리자(admin):
    - 대기 중(pending) 승인 요청 목록을 조회
    - 각 요청에 대해 [승인] / [승인 및 실행] / [거부] 버튼으로 처리

일반 사용자:
    - 본인이 요청한 승인 요청의 상태를 조회

의존 모듈:
    - approval.approval_manager: list_pending(), list_user_requests(),
      approve_request(), deny_request(), execute_approved()
    - auth.session: get_current_user(), is_admin()
"""

import logging

import streamlit as st

from approval.approval_manager import (
    list_pending,
    list_user_requests,
    approve_request,
    deny_request,
    execute_approved,
)
from auth.session import get_current_user, is_admin

logger = logging.getLogger(__name__)


def render_approval_page():
    """
    승인 관리 페이지를 렌더링.

    admin이면 pending 목록 + 승인/거부/실행 버튼을 표시합니다.
    일반 유저이면 본인 요청 상태만 조회합니다.
    """
    user = get_current_user()
    if not user:
        st.error("로그인이 필요합니다.")
        st.stop()

    if is_admin():
        _render_admin_approval_view()
    else:
        _render_user_request_view(user["username"])


def _render_admin_approval_view():
    """관리자용 승인 관리 뷰 — pending 요청 목록 + 승인/거부/실행."""
    st.title("✅ 승인 관리")
    st.caption("위험 SQL 요청을 검토하고 승인/거부합니다.")

    # 새로고침 버튼
    if st.button("🔄 새로고침"):
        st.rerun()

    pending = list_pending()

    if not pending:
        st.info("📭 대기 중인 승인 요청이 없습니다.")
        return

    st.markdown(f"### 대기 중인 요청 ({len(pending)}건)")

    for req in pending:
        with st.container(border=True):
            # 요청 헤더
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.markdown(f"**#{req['id']}** — {req.get('title', '제목 없음')}")
            with col2:
                st.caption(f"요청자: {req.get('requested_by', 'unknown')}")
            with col3:
                st.caption(f"분류: {req.get('sql_category', 'N/A')}")

            # SQL 표시
            st.code(req.get("sql_text", ""), language="sql")

            # 생성 시각
            created_at = req.get("created_at")
            if created_at:
                st.caption(f"요청 시각: {created_at}")

            # 액션 버튼
            btn_col1, btn_col2, btn_col3 = st.columns(3)

            with btn_col1:
                if st.button(
                    "✅ 승인 및 실행",
                    key=f"approve_exec_{req['id']}",
                    use_container_width=True,
                ):
                    _approve_and_execute(req["id"])

            with btn_col2:
                if st.button(
                    "👍 승인만",
                    key=f"approve_{req['id']}",
                    use_container_width=True,
                ):
                    user = get_current_user()
                    if approve_request(req["id"], reviewed_by=user["username"]):
                        st.success(f"요청 #{req['id']}이 승인되었습니다.")
                        st.rerun()
                    else:
                        st.error("승인 처리에 실패했습니다.")

            with btn_col3:
                if st.button(
                    "❌ 거부",
                    key=f"deny_{req['id']}",
                    type="secondary",
                    use_container_width=True,
                ):
                    user = get_current_user()
                    if deny_request(req["id"], reviewed_by=user["username"]):
                        st.warning(f"요청 #{req['id']}이 거부되었습니다.")
                        st.rerun()
                    else:
                        st.error("거부 처리에 실패했습니다.")


def _approve_and_execute(request_id: int):
    """
    요청을 승인한 후 즉시 SQL을 실행.

    승인 → 실행을 한 번에 처리하여 관리자의 클릭 수를 줄입니다.
    """
    user = get_current_user()
    username = user["username"] if user else "admin"

    if approve_request(request_id, reviewed_by=username):
        result = execute_approved(request_id)
        if result["success"]:
            st.success(f"✅ 요청 #{request_id} 실행 완료: {result['message']}")
        else:
            st.error(f"실행 실패: {result['message']}")
        st.rerun()
    else:
        st.error("승인 처리에 실패했습니다.")


def _render_user_request_view(username: str):
    """일반 유저용 — 본인 요청 상태 조회."""
    st.title("📋 내 요청 현황")
    st.caption("내가 요청한 SQL 승인 현황을 확인합니다.")

    requests = list_user_requests(username)

    if not requests:
        st.info("📭 요청 이력이 없습니다.")
        return

    # 상태별 아이콘
    _STATUS_ICONS = {
        "pending": "⏳",
        "approved": "✅",
        "denied": "❌",
        "executed": "🚀",
    }

    for req in requests:
        status = req.get("status", "unknown")
        icon = _STATUS_ICONS.get(status, "❓")

        with st.container(border=True):
            col1, col2 = st.columns([4, 1])
            with col1:
                st.markdown(f"**#{req['id']}** — {req.get('title', '제목 없음')}")
            with col2:
                st.markdown(f"{icon} **{status.upper()}**")

            with st.expander("SQL 보기"):
                st.code(req.get("sql_text", ""), language="sql")

            # 결과 요약 (있는 경우)
            result_summary = req.get("result_summary")
            if result_summary:
                st.caption(f"결과: {result_summary}")

            # 검토 정보
            if req.get("reviewed_by"):
                st.caption(
                    f"검토자: {req['reviewed_by']} | "
                    f"검토 시각: {req.get('reviewed_at', 'N/A')}"
                )
