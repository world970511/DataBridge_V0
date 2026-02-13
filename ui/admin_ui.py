"""
DataBridge 사용자 관리 페이지 (admin 전용).

관리자가 사용자를 추가·비활성화할 수 있는 관리 페이지입니다.
일반 사용자는 이 페이지에 접근할 수 없습니다.

기능:
    - 등록된 사용자 목록 조회 (활성/비활성 포함)
    - 새 사용자 추가 (username, password, role, display_name)
    - 사용자 비활성화 (soft delete — is_active=FALSE)

의존 모듈:
    - auth.user_manager: create_user(), list_users(), delete_user()
    - auth.session: is_admin()
"""

import logging

import streamlit as st

from auth.user_manager import create_user, list_users, delete_user
from auth.session import is_admin

logger = logging.getLogger(__name__)


def render_admin_page():
    """
    사용자 관리 페이지를 렌더링.

    admin 권한이 없으면 접근 차단 메시지를 표시합니다.
    admin이면 사용자 목록 + 사용자 추가 폼을 표시합니다.
    """
    if not is_admin():
        st.error("⛔ 관리자만 접근할 수 있습니다.")
        st.stop()

    st.title("👥 사용자 관리")
    st.caption("사용자를 추가하거나 비활성화합니다.")

    # 두 컬럼 레이아웃: 좌=사용자 목록, 우=추가 폼
    col_list, col_add = st.columns([2, 1])

    with col_list:
        _render_user_list()

    with col_add:
        _render_add_user_form()


def _render_user_list():
    """등록된 사용자 목록을 테이블로 표시하고 비활성화 버튼을 제공."""
    st.markdown("### 사용자 목록")

    # 비활성 계정도 표시 옵션
    show_inactive = st.checkbox("비활성 계정도 표시", value=False)

    users = list_users(include_inactive=show_inactive)

    if not users:
        st.info("등록된 사용자가 없습니다.")
        return

    for user in users:
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])

            with col1:
                status_icon = "🟢" if user.get("is_active") else "🔴"
                st.markdown(
                    f"{status_icon} **{user['username']}** "
                    f"({user.get('display_name', '')})"
                )

            with col2:
                role = user.get("role", "user")
                role_badge = "👑 admin" if role == "admin" else "👤 user"
                st.caption(role_badge)

            with col3:
                created = user.get("created_at")
                if created:
                    st.caption(str(created)[:10])

            with col4:
                # admin 계정은 비활성화 버튼 비표시
                if user.get("role") != "admin" and user.get("is_active"):
                    if st.button(
                        "비활성화",
                        key=f"deactivate_{user['id']}",
                        type="secondary",
                    ):
                        if delete_user(user["username"]):
                            st.success(f"{user['username']} 비활성화됨")
                            st.rerun()
                        else:
                            st.error("비활성화 실패")


def _render_add_user_form():
    """새 사용자 추가 폼."""
    st.markdown("### 사용자 추가")

    with st.form("add_user_form"):
        username = st.text_input("사용자명 (로그인 ID)", placeholder="예: kim")
        display_name = st.text_input("표시 이름", placeholder="예: 김철수")
        password = st.text_input("비밀번호", type="password")
        password_confirm = st.text_input("비밀번호 확인", type="password")
        role = st.selectbox("역할", ["user", "admin"])

        submitted = st.form_submit_button("➕ 사용자 추가", use_container_width=True)

    if submitted:
        # 입력 검증
        if not username or not password:
            st.error("사용자명과 비밀번호는 필수입니다.")
            return

        if len(username) < 2:
            st.error("사용자명은 2자 이상이어야 합니다.")
            return

        if len(password) < 4:
            st.error("비밀번호는 4자 이상이어야 합니다.")
            return

        if password != password_confirm:
            st.error("비밀번호가 일치하지 않습니다.")
            return

        # 사용자 생성
        result = create_user(
            username=username,
            password=password,
            role=role,
            display_name=display_name or username,
        )

        if result:
            st.success(f"✅ 사용자 '{username}' 생성 완료!")
            st.rerun()
        else:
            st.error(f"사용자 생성 실패 — 이미 존재하는 사용자명일 수 있습니다.")
