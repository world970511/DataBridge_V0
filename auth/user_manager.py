"""
사용자 계정 관리 모듈.

PostgreSQL users 테이블을 기반으로 사용자 CRUD와 비밀번호 인증을 처리합니다.
비밀번호는 hashlib.sha256 + per-user 랜덤 salt로 해싱하여 저장하며,
외부 의존성 없이 표준 라이브러리만으로 동작합니다.

users 테이블 스키마:
    id              SERIAL PRIMARY KEY
    username        VARCHAR(100) UNIQUE NOT NULL
    password_hash   VARCHAR(256) NOT NULL
    salt            VARCHAR(64) NOT NULL
    role            VARCHAR(20) NOT NULL DEFAULT 'user'  -- 'admin' 또는 'user'
    display_name    VARCHAR(200)
    is_active       BOOLEAN DEFAULT TRUE
    created_at      TIMESTAMPTZ
    updated_at      TIMESTAMPTZ

의존 모듈:
    - db.connection: get_cursor(), execute_query() — PostgreSQL 접근
    - config.settings: get_settings() → AuthConfig(admin_password)

사용 예시:
    from auth.user_manager import create_user, authenticate, ensure_admin_exists

    # 앱 기동 시 admin 계정 보장
    ensure_admin_exists()

    # 사용자 생성
    user = create_user("kim", "password123", role="user", display_name="김철수")

    # 인증
    user = authenticate("kim", "password123")
    if user:
        print(f"로그인 성공: {user['display_name']}")
"""

import hashlib
import logging
import os
from typing import Optional

from db.connection import get_cursor, execute_query
from config.settings import get_settings

logger = logging.getLogger(__name__)


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """
    비밀번호를 SHA-256 + 랜덤 salt로 해싱.

    salt가 주어지지 않으면 os.urandom(32)으로 새로 생성합니다.
    이미 저장된 salt로 검증할 때는 해당 salt를 전달합니다.

    Args:
        password: 평문 비밀번호.
        salt: 16진수 문자열 salt. None이면 새로 생성합니다.

    Returns:
        (password_hash, salt) 튜플.
        password_hash: SHA-256 해시의 16진수 문자열.
        salt: 사용된 salt의 16진수 문자열.
    """
    if salt is None:
        salt = os.urandom(32).hex()

    salted = f"{salt}{password}".encode("utf-8")
    password_hash = hashlib.sha256(salted).hexdigest()

    return password_hash, salt


def create_user(
    username: str,
    password: str,
    role: str = "user",
    display_name: Optional[str] = None,
) -> Optional[dict]:
    """
    새 사용자를 생성하여 users 테이블에 INSERT.

    username이 이미 존재하면 None을 반환합니다.
    비밀번호는 hash_password()로 해싱하여 저장합니다.

    Args:
        username: 로그인 ID (unique).
        password: 평문 비밀번호.
        role: 'admin' 또는 'user'. 기본값 'user'.
        display_name: 표시 이름. None이면 username을 사용합니다.

    Returns:
        생성된 사용자 정보 딕셔너리. username 중복 시 None.
        {
            "id": int,
            "username": str,
            "role": str,
            "display_name": str,
            "is_active": bool,
        }
    """
    if not display_name:
        display_name = username

    password_hash, salt = hash_password(password)

    try:
        with get_cursor(dict_cursor=True) as cur:
            cur.execute(
                """
                INSERT INTO users (username, password_hash, salt, role, display_name)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, username, role, display_name, is_active
                """,
                (username, password_hash, salt, role, display_name),
            )
            user = cur.fetchone()
            logger.info(f"User created: username={username}, role={role}")
            return dict(user) if user else None

    except Exception as e:
        error_msg = str(e).lower()
        if "unique" in error_msg or "duplicate" in error_msg:
            logger.warning(f"Username already exists: {username}")
            return None
        logger.error(f"Failed to create user: {e}")
        raise


def authenticate(username: str, password: str) -> Optional[dict]:
    """
    사용자 인증 — username과 password가 일치하면 사용자 정보를 반환.

    users 테이블에서 username으로 조회하여 salt를 가져온 뒤,
    동일한 salt로 입력된 password를 해싱하여 저장된 hash와 비교합니다.
    비활성(is_active=False) 계정은 인증에 실패합니다.

    Args:
        username: 로그인 ID.
        password: 평문 비밀번호.

    Returns:
        인증 성공 시 사용자 정보 딕셔너리, 실패 시 None.
        {
            "id": int,
            "username": str,
            "role": str,
            "display_name": str,
            "is_active": bool,
        }
    """
    try:
        rows = execute_query(
            "SELECT id, username, password_hash, salt, role, display_name, is_active "
            "FROM users WHERE username = %s",
            (username,),
        )

        if not rows:
            logger.warning(f"Authentication failed: user not found — {username}")
            return None

        user = rows[0]

        # 비활성 계정 차단
        if not user["is_active"]:
            logger.warning(f"Authentication failed: account disabled — {username}")
            return None

        # 비밀번호 검증
        computed_hash, _ = hash_password(password, salt=user["salt"])
        if computed_hash != user["password_hash"]:
            logger.warning(f"Authentication failed: wrong password — {username}")
            return None

        logger.info(f"Authentication successful: {username}")
        return {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "display_name": user["display_name"],
            "is_active": user["is_active"],
        }

    except Exception as e:
        logger.error(f"Authentication error: {e}")
        return None


def list_users(include_inactive: bool = False) -> list[dict]:
    """
    등록된 사용자 목록을 조회.

    Args:
        include_inactive: True이면 비활성 계정도 포함. 기본값 False.

    Returns:
        사용자 정보 딕셔너리 리스트.
        [{"id": int, "username": str, "role": str, "display_name": str, "is_active": bool, "created_at": datetime}, ...]
    """
    if include_inactive:
        sql = "SELECT id, username, role, display_name, is_active, created_at FROM users ORDER BY id"
    else:
        sql = "SELECT id, username, role, display_name, is_active, created_at FROM users WHERE is_active = TRUE ORDER BY id"

    try:
        rows = execute_query(sql)
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to list users: {e}")
        return []


def delete_user(username: str, hard_delete: bool = False) -> bool:
    """
    사용자를 비활성화(soft delete) 또는 완전 삭제(hard delete).

    admin 계정은 삭제할 수 없습니다. 최소 1명의 admin이 유지되어야 합니다.

    Args:
        username: 삭제할 사용자의 username.
        hard_delete: True이면 DB에서 완전 삭제, False이면 is_active=FALSE로 변경.

    Returns:
        성공 시 True, 실패 시 False.
    """
    try:
        # admin 보호 — 마지막 admin은 삭제 불가
        rows = execute_query(
            "SELECT role FROM users WHERE username = %s", (username,)
        )
        if not rows:
            logger.warning(f"User not found: {username}")
            return False

        if rows[0]["role"] == "admin":
            admin_count = execute_query(
                "SELECT COUNT(*) as cnt FROM users WHERE role = 'admin' AND is_active = TRUE"
            )
            if admin_count and admin_count[0]["cnt"] <= 1:
                logger.warning(f"Cannot delete last admin: {username}")
                return False

        with get_cursor() as cur:
            if hard_delete:
                cur.execute("DELETE FROM users WHERE username = %s", (username,))
            else:
                cur.execute(
                    "UPDATE users SET is_active = FALSE, updated_at = NOW() WHERE username = %s",
                    (username,),
                )

        logger.info(
            f"User {'deleted' if hard_delete else 'deactivated'}: {username}"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to delete user: {e}")
        return False


def ensure_admin_exists():
    """
    관리자(admin) 계정이 존재하는지 확인하고, 없으면 자동 생성.

    Settings.auth.admin_password를 사용하여 'admin' 계정을 생성합니다.
    이미 존재하면 아무 동작도 하지 않습니다.
    앱 기동 시(startup_checks 이후) 호출됩니다.
    """
    try:
        rows = execute_query(
            "SELECT id FROM users WHERE username = 'admin' AND is_active = TRUE"
        )
        if rows:
            logger.debug("Admin user already exists")
            return

        settings = get_settings()
        result = create_user(
            username="admin",
            password=settings.auth.admin_password,
            role="admin",
            display_name="관리자",
        )

        if result:
            logger.info("Admin user created with default password")
        else:
            logger.warning("Admin user creation skipped (may already exist)")

    except Exception as e:
        logger.error(f"Failed to ensure admin exists: {e}")
