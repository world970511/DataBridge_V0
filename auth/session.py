"""
Streamlit 세션 기반 인증 관리 모듈.

st.session_state를 사용하여 로그인 상태를 유지하고, 각 페이지에서
require_login()으로 인증을 강제합니다. 로그인 폼 UI도 이 모듈에서 제공합니다.

Streamlit의 session_state는 브라우저 탭 단위로 유지되며,
페이지 새로고침 시에도 세션이 유지됩니다.

세션에 저장되는 키:
    - "authenticated": bool — 로그인 여부
    - "user": dict — 로그인한 사용자 정보 (id, username, role, display_name, is_active)

의존 모듈:
    - auth.user_manager: authenticate() — DB 기반 비밀번호 검증
    - streamlit: st.session_state, st.form, st.text_input 등

사용 예시:
    import streamlit as st
    from auth.session import require_login, get_current_user, is_admin

    # 페이지 최상단에서 호출 — 미로그인 시 로그인 폼 표시 후 실행 중단
    require_login()

    user = get_current_user()
    st.write(f"안녕하세요, {user['display_name']}님!")

    if is_admin():
        st.write("관리자 메뉴")
"""

import logging
from typing import Optional

import streamlit as st

from auth.user_manager import authenticate as db_authenticate

logger = logging.getLogger(__name__)


def _init_session_state():
    """
    Streamlit 세션 상태 초기화.

    'authenticated'와 'user' 키가 session_state에 없으면 기본값을 설정합니다.
    Streamlit 앱의 매 리렌더링마다 호출되어도 기존 값을 덮어쓰지 않습니다.
    """
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False
    if "user" not in st.session_state:
        st.session_state["user"] = None


def show_login_form():
    """
    Streamlit 로그인 폼을 화면에 표시.

    사용자명과 비밀번호를 입력받아 authenticate()로 검증합니다.
    인증 성공 시 session_state에 사용자 정보를 저장하고 페이지를 리렌더링합니다.
    인증 실패 시 에러 메시지를 표시합니다.
    """
    st.markdown("## 🔐 DataBridge 로그인")
    st.markdown("---")

    with st.form("login_form"):
        username = st.text_input("사용자명", placeholder="username")
        password = st.text_input("비밀번호", type="password", placeholder="password")
        submitted = st.form_submit_button("로그인", use_container_width=True)

    if submitted:
        if not username or not password:
            st.error("사용자명과 비밀번호를 모두 입력해 주세요.")
            return

        user = db_authenticate(username, password)
        if user:
            st.session_state["authenticated"] = True
            st.session_state["user"] = user
            logger.info(f"User logged in via UI: {username}")
            st.rerun()
        else:
            st.error("사용자명 또는 비밀번호가 올바르지 않습니다.")
            logger.warning(f"Login attempt failed via UI: {username}")


def require_login():
    """
    현재 사용자가 로그인했는지 확인하고, 미인증 시 로그인 폼을 표시 후 실행을 중단.

    각 Streamlit 페이지 최상단에서 호출합니다.
    로그인하지 않은 상태이면 show_login_form()을 표시하고 st.stop()으로
    나머지 페이지 렌더링을 중단합니다.
    """
    _init_session_state()

    if not st.session_state["authenticated"]:
        show_login_form()
        st.stop()


def get_current_user() -> Optional[dict]:
    """
    현재 로그인한 사용자 정보를 반환.

    Returns:
        로그인 상태이면 사용자 딕셔너리, 미인증이면 None.
        {"id": int, "username": str, "role": str, "display_name": str, "is_active": bool}
    """
    _init_session_state()
    return st.session_state.get("user")


def is_admin() -> bool:
    """
    현재 로그인한 사용자가 관리자(admin)인지 확인.

    Returns:
        admin이면 True, 일반 사용자 또는 미인증이면 False.
    """
    user = get_current_user()
    if user and user.get("role") == "admin":
        return True
    return False


def logout():
    """
    현재 사용자의 세션을 초기화하여 로그아웃 처리.

    session_state에서 인증 관련 키를 제거하고 채팅 이력도 함께 초기화합니다.
    호출 후 st.rerun()으로 페이지를 새로 렌더링해야 합니다.
    """
    user = get_current_user()
    if user:
        logger.info(f"User logged out: {user.get('username')}")

    st.session_state["authenticated"] = False
    st.session_state["user"] = None

    # 채팅 이력도 함께 초기화
    if "chat_history" in st.session_state:
        st.session_state["chat_history"] = []
