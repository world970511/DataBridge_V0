"""
인증 모듈 테스트.

auth/user_manager.py의 비밀번호 해싱, 사용자 CRUD, 인증 기능을 테스트합니다.
DB 접근이 필요한 함수는 mock 기반으로 테스트합니다.

실행:
    pytest tests/test_auth.py -v -m unit
"""

from unittest.mock import patch, MagicMock

import pytest

from auth.user_manager import hash_password, create_user, authenticate, list_users, delete_user


# ============================================
# hash_password() 테스트
# ============================================

@pytest.mark.unit
class TestHashPassword:
    """hash_password() — SHA-256 + salt 비밀번호 해싱 테스트."""

    def test_generates_hash_and_salt(self):
        """해시와 salt를 생성하여 반환합니다."""
        pw_hash, salt = hash_password("mypassword")
        assert isinstance(pw_hash, str)
        assert isinstance(salt, str)
        assert len(pw_hash) == 64  # SHA-256 hex digest
        assert len(salt) > 0

    def test_same_password_different_salt(self):
        """같은 비밀번호라도 다른 salt를 생성합니다."""
        hash1, salt1 = hash_password("password123")
        hash2, salt2 = hash_password("password123")
        assert salt1 != salt2
        assert hash1 != hash2

    def test_same_password_same_salt_produces_same_hash(self):
        """같은 비밀번호 + 같은 salt이면 동일한 해시를 생성합니다."""
        _, salt = hash_password("password123")
        hash1, _ = hash_password("password123", salt=salt)
        hash2, _ = hash_password("password123", salt=salt)
        assert hash1 == hash2

    def test_different_password_same_salt(self):
        """다른 비밀번호는 같은 salt라도 다른 해시를 생성합니다."""
        _, salt = hash_password("password1")
        hash1, _ = hash_password("password1", salt=salt)
        hash2, _ = hash_password("password2", salt=salt)
        assert hash1 != hash2

    def test_empty_password(self):
        """빈 비밀번호도 해싱 가능합니다."""
        pw_hash, salt = hash_password("")
        assert len(pw_hash) == 64

    def test_unicode_password(self):
        """한국어 비밀번호도 정상 해싱됩니다."""
        pw_hash, salt = hash_password("한글비밀번호123!")
        assert len(pw_hash) == 64


# ============================================
# create_user() 테스트
# ============================================

@pytest.mark.unit
class TestCreateUser:
    """create_user() — DB mock 기반 사용자 생성 테스트."""

    @patch("auth.user_manager.get_cursor")
    def test_create_user_success(self, mock_get_cursor):
        """사용자 생성 성공 시 사용자 정보를 반환합니다."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "id": 1,
            "username": "testuser",
            "role": "user",
            "display_name": "테스트 유저",
            "is_active": True,
        }
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = create_user("testuser", "password123", display_name="테스트 유저")

        assert result is not None
        assert result["username"] == "testuser"
        assert result["role"] == "user"
        mock_cursor.execute.assert_called_once()

    @patch("auth.user_manager.get_cursor")
    def test_create_user_duplicate(self, mock_get_cursor):
        """중복 username이면 None을 반환합니다."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("duplicate key value violates unique constraint")
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = create_user("existing_user", "password123")
        assert result is None

    @patch("auth.user_manager.get_cursor")
    def test_create_admin_user(self, mock_get_cursor):
        """admin 역할로 사용자를 생성합니다."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "id": 1,
            "username": "admin2",
            "role": "admin",
            "display_name": "관리자2",
            "is_active": True,
        }
        mock_get_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_get_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = create_user("admin2", "pw", role="admin", display_name="관리자2")
        assert result is not None
        assert result["role"] == "admin"


# ============================================
# authenticate() 테스트
# ============================================

@pytest.mark.unit
class TestAuthenticate:
    """authenticate() — DB mock 기반 인증 테스트."""

    @patch("auth.user_manager.execute_query")
    def test_authenticate_success(self, mock_query):
        """올바른 username/password로 인증 성공."""
        pw_hash, salt = hash_password("correct_password")
        mock_query.return_value = [{
            "id": 1,
            "username": "testuser",
            "password_hash": pw_hash,
            "salt": salt,
            "role": "user",
            "display_name": "테스트",
            "is_active": True,
        }]

        result = authenticate("testuser", "correct_password")
        assert result is not None
        assert result["username"] == "testuser"

    @patch("auth.user_manager.execute_query")
    def test_authenticate_wrong_password(self, mock_query):
        """잘못된 비밀번호로 인증 실패."""
        pw_hash, salt = hash_password("correct_password")
        mock_query.return_value = [{
            "id": 1,
            "username": "testuser",
            "password_hash": pw_hash,
            "salt": salt,
            "role": "user",
            "display_name": "테스트",
            "is_active": True,
        }]

        result = authenticate("testuser", "wrong_password")
        assert result is None

    @patch("auth.user_manager.execute_query")
    def test_authenticate_user_not_found(self, mock_query):
        """존재하지 않는 사용자로 인증 실패."""
        mock_query.return_value = []

        result = authenticate("nonexistent", "password")
        assert result is None

    @patch("auth.user_manager.execute_query")
    def test_authenticate_inactive_user(self, mock_query):
        """비활성 계정은 인증 실패."""
        pw_hash, salt = hash_password("password")
        mock_query.return_value = [{
            "id": 1,
            "username": "inactive",
            "password_hash": pw_hash,
            "salt": salt,
            "role": "user",
            "display_name": "비활성",
            "is_active": False,
        }]

        result = authenticate("inactive", "password")
        assert result is None

    @patch("auth.user_manager.execute_query")
    def test_authenticate_db_error(self, mock_query):
        """DB 오류 시 None 반환 (예외 전파 안 함)."""
        mock_query.side_effect = Exception("DB connection failed")

        result = authenticate("testuser", "password")
        assert result is None


# ============================================
# list_users() 테스트
# ============================================

@pytest.mark.unit
class TestListUsers:
    """list_users() — DB mock 기반 사용자 목록 조회 테스트."""

    @patch("auth.user_manager.execute_query")
    def test_list_active_users(self, mock_query):
        """활성 사용자만 조회합니다."""
        mock_query.return_value = [
            {"id": 1, "username": "admin", "role": "admin", "display_name": "관리자", "is_active": True, "created_at": "2024-01-01"},
            {"id": 2, "username": "user1", "role": "user", "display_name": "유저1", "is_active": True, "created_at": "2024-01-02"},
        ]

        result = list_users(include_inactive=False)
        assert len(result) == 2
        assert all(u["is_active"] for u in result)

    @patch("auth.user_manager.execute_query")
    def test_list_empty(self, mock_query):
        """사용자가 없으면 빈 리스트를 반환합니다."""
        mock_query.return_value = []
        result = list_users()
        assert result == []


# ============================================
# delete_user() 테스트
# ============================================

@pytest.mark.unit
class TestDeleteUser:
    """delete_user() — DB mock 기반 사용자 삭제 테스트."""

    @patch("auth.user_manager.get_cursor")
    @patch("auth.user_manager.execute_query")
    def test_deactivate_user(self, mock_query, mock_cursor_ctx):
        """일반 사용자 soft delete (비활성화)."""
        mock_query.return_value = [{"role": "user"}]
        mock_cursor = MagicMock()
        mock_cursor_ctx.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor_ctx.return_value.__exit__ = MagicMock(return_value=False)

        result = delete_user("user1")
        assert result is True

    @patch("auth.user_manager.execute_query")
    def test_delete_last_admin_blocked(self, mock_query):
        """마지막 admin은 삭제할 수 없습니다."""
        # 첫 번째 호출: 사용자 역할 조회 → admin
        # 두 번째 호출: admin 수 카운트 → 1
        mock_query.side_effect = [
            [{"role": "admin"}],
            [{"cnt": 1}],
        ]

        result = delete_user("admin")
        assert result is False

    @patch("auth.user_manager.execute_query")
    def test_delete_nonexistent_user(self, mock_query):
        """존재하지 않는 사용자 삭제 시 False."""
        mock_query.return_value = []

        result = delete_user("ghost")
        assert result is False
