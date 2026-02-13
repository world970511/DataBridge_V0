"""
DataBridge 인증/인가 패키지.

사용자 계정 관리(user_manager)와 Streamlit 세션 기반 인증(session)을 제공합니다.
앱 기동 시 ensure_admin_exists()로 관리자 계정을 자동 생성하며,
Streamlit 페이지에서 require_login()으로 로그인을 강제할 수 있습니다.
"""

from auth.user_manager import (
    create_user,
    authenticate,
    list_users,
    delete_user,
    ensure_admin_exists,
    hash_password,
)
from auth.session import (
    require_login,
    get_current_user,
    is_admin,
    logout,
)

__all__ = [
    "create_user",
    "authenticate",
    "list_users",
    "delete_user",
    "ensure_admin_exists",
    "hash_password",
    "require_login",
    "get_current_user",
    "is_admin",
    "logout",
]
