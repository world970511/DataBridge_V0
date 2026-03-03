"""
Streamlit 세션 기반 인증 관리 모듈.

st.session_state를 사용하여 로그인 상태를 유지하고, 각 페이지에서
require_login()으로 인증을 강제합니다. 로그인 폼 UI도 이 모듈에서 제공합니다.

세션 토큰 전략:
    - 로그인 성공 시 랜덤 토큰을 생성하여 서버 메모리 + URL query_params에 저장
    - 새로고침 시 session_state가 초기화되더라도 query_params의 토큰으로 세션 복원
    - 터널(cloudflared, ngrok) 환경에서 WebSocket 재연결 시에도 로그인 유지

세션에 저장되는 키:
    - "authenticated": bool — 로그인 여부
    - "user": dict — 로그인한 사용자 정보 (id, username, role, display_name, is_active)

의존 모듈:
    - auth.user_manager: authenticate() — DB 기반 비밀번호 검증
    - streamlit: st.session_state, st.query_params, st.form 등
"""

import logging
import secrets
import threading
import time
from typing import Optional

import streamlit as st

from auth.user_manager import authenticate as db_authenticate

logger = logging.getLogger(__name__)

# ── 서버 측 세션 토큰 저장소 ──
# token → {"user": dict, "created_at": float}
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()
_SESSION_TTL = 86400  # 24시간


def _generate_token() -> str:
    """URL-safe 랜덤 세션 토큰 생성."""
    return secrets.token_urlsafe(32)


def _store_session(token: str, user: dict):
    """서버 측 세션 저장소에 토큰-사용자 매핑 저장."""
    with _sessions_lock:
        # 오래된 세션 정리 (100개 초과 시)
        if len(_sessions) > 100:
            _cleanup_expired()
        _sessions[token] = {"user": user, "created_at": time.time()}


def _get_session(token: str) -> Optional[dict]:
    """토큰으로 사용자 정보 조회. 만료되었으면 None."""
    with _sessions_lock:
        session = _sessions.get(token)
        if not session:
            return None
        if time.time() - session["created_at"] > _SESSION_TTL:
            del _sessions[token]
            return None
        return session["user"]


def _remove_session(token: str):
    """토큰 삭제."""
    with _sessions_lock:
        _sessions.pop(token, None)


def _cleanup_expired():
    """만료된 세션 정리."""
    now = time.time()
    expired = [t for t, s in _sessions.items() if now - s["created_at"] > _SESSION_TTL]
    for t in expired:
        del _sessions[t]


# ── Streamlit 세션 관리 ──


def _init_session_state():
    """
    Streamlit 세션 상태 초기화 + 토큰 기반 세션 복원.

    session_state가 비어 있으면 query_params에서 토큰을 확인하고,
    유효한 토큰이 있으면 세션을 자동 복원합니다.
    """
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False
    if "user" not in st.session_state:
        st.session_state["user"] = None

    # 이미 인증된 상태면 복원 불필요
    if st.session_state["authenticated"]:
        return

    # query_params에서 토큰 확인 → 세션 복원
    token = st.query_params.get("token")
    if token:
        user = _get_session(token)
        if user:
            st.session_state["authenticated"] = True
            st.session_state["user"] = user
            logger.info(f"Session restored via token: {user.get('username')}")
        else:
            # 만료/무효 토큰 제거
            st.query_params.pop("token", None)


def show_login_form():
    """
    Streamlit 로그인 폼을 화면에 표시.

    인증 성공 시 세션 토큰을 생성하여 query_params에 저장하고,
    session_state에 사용자 정보를 저장합니다.
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
            # 세션 토큰 생성 + 저장
            token = _generate_token()
            _store_session(token, user)

            st.session_state["authenticated"] = True
            st.session_state["user"] = user
            st.query_params["token"] = token

            logger.info(f"User logged in via UI: {username}")
            st.rerun()
        else:
            st.error("사용자명 또는 비밀번호가 올바르지 않습니다.")
            logger.warning(f"Login attempt failed via UI: {username}")


def require_login():
    """
    현재 사용자가 로그인했는지 확인하고, 미인증 시 로그인 폼을 표시 후 실행을 중단.

    각 Streamlit 페이지 최상단에서 호출합니다.
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

    서버 측 토큰도 함께 삭제합니다.
    """
    user = get_current_user()
    if user:
        logger.info(f"User logged out: {user.get('username')}")

    # 서버 측 토큰 삭제
    token = st.query_params.get("token")
    if token:
        _remove_session(token)
        st.query_params.pop("token", None)

    st.session_state["authenticated"] = False
    st.session_state["user"] = None

    # 채팅 이력도 함께 초기화
    if "chat_history" in st.session_state:
        st.session_state["chat_history"] = []
