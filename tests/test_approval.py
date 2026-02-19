"""
승인 모듈 테스트.

approval/sql_classifier.py의 4단계 SQL 분류와
approval/approval_manager.py의 승인 요청 CRUD를 테스트합니다.

실행:
    pytest tests/test_approval.py -v -m unit
"""

from unittest.mock import patch, MagicMock

import pytest

from approval.sql_classifier import classify_sql, SqlCategory
from approval.approval_manager import create_request, approve_request, deny_request, execute_approved


# ============================================
# classify_sql() 테스트
# ============================================

@pytest.mark.unit
class TestClassifySql:
    """classify_sql() — 4단계 SQL 위험도 분류 테스트."""

    # --- SAFE (SELECT) ---

    def test_safe_select(self):
        """SELECT 쿼리는 SAFE로 분류됩니다."""
        category, reason = classify_sql("SELECT * FROM sales")
        assert category == SqlCategory.SAFE

    def test_safe_select_with_join(self):
        """JOIN이 포함된 SELECT도 SAFE입니다."""
        sql = "SELECT a.id, b.name FROM orders a JOIN products b ON a.product_id = b.id"
        category, reason = classify_sql(sql)
        assert category == SqlCategory.SAFE

    def test_safe_select_with_aggregation(self):
        """집계 함수가 포함된 SELECT도 SAFE입니다."""
        sql = "SELECT category, SUM(amount) FROM sales GROUP BY category"
        category, reason = classify_sql(sql)
        assert category == SqlCategory.SAFE

    # --- AUTO_ALLOWED (CREATE, INSERT, UPDATE) ---

    def test_auto_create_table(self):
        """CREATE TABLE은 AUTO_ALLOWED로 분류됩니다."""
        category, reason = classify_sql("CREATE TABLE test_table (id SERIAL PRIMARY KEY)")
        assert category == SqlCategory.AUTO_ALLOWED

    def test_auto_insert(self):
        """INSERT는 AUTO_ALLOWED로 분류됩니다."""
        category, reason = classify_sql("INSERT INTO sales (name, amount) VALUES ('test', 100)")
        assert category == SqlCategory.AUTO_ALLOWED

    def test_auto_update(self):
        """UPDATE는 AUTO_ALLOWED로 분류됩니다."""
        category, reason = classify_sql("UPDATE sales SET amount = 200 WHERE id = 1")
        assert category == SqlCategory.AUTO_ALLOWED

    # --- NEEDS_APPROVAL (DROP, DELETE, TRUNCATE, ALTER) ---

    def test_needs_approval_drop(self):
        """DROP TABLE은 NEEDS_APPROVAL로 분류됩니다."""
        category, reason = classify_sql("DROP TABLE sales")
        assert category == SqlCategory.NEEDS_APPROVAL

    def test_needs_approval_delete(self):
        """DELETE는 NEEDS_APPROVAL로 분류됩니다."""
        category, reason = classify_sql("DELETE FROM sales WHERE year < 2020")
        assert category == SqlCategory.NEEDS_APPROVAL

    def test_needs_approval_truncate(self):
        """TRUNCATE는 NEEDS_APPROVAL로 분류됩니다."""
        category, reason = classify_sql("TRUNCATE TABLE sales")
        assert category == SqlCategory.NEEDS_APPROVAL

    def test_needs_approval_alter(self):
        """ALTER TABLE은 NEEDS_APPROVAL로 분류됩니다."""
        category, reason = classify_sql("ALTER TABLE sales ADD COLUMN region VARCHAR(50)")
        assert category == SqlCategory.NEEDS_APPROVAL

    # --- FORBIDDEN ---

    def test_forbidden_grant(self):
        """GRANT는 FORBIDDEN으로 분류됩니다."""
        category, reason = classify_sql("GRANT ALL ON sales TO public")
        assert category == SqlCategory.FORBIDDEN

    def test_forbidden_revoke(self):
        """REVOKE는 FORBIDDEN으로 분류됩니다."""
        category, reason = classify_sql("REVOKE SELECT ON sales FROM public")
        assert category == SqlCategory.FORBIDDEN

    def test_forbidden_copy(self):
        """COPY는 FORBIDDEN으로 분류됩니다."""
        category, reason = classify_sql("COPY sales TO '/tmp/export.csv'")
        assert category == SqlCategory.FORBIDDEN

    def test_forbidden_empty_sql(self):
        """빈 SQL은 FORBIDDEN으로 분류됩니다."""
        category, reason = classify_sql("")
        assert category == SqlCategory.FORBIDDEN

    def test_forbidden_sql_with_comment(self):
        """SQL 주석이 포함되면 FORBIDDEN입니다."""
        category, reason = classify_sql("SELECT * FROM sales -- drop table")
        assert category == SqlCategory.FORBIDDEN

    def test_forbidden_multi_statement(self):
        """다중 문장은 FORBIDDEN입니다."""
        category, reason = classify_sql("SELECT 1; DROP TABLE sales")
        assert category == SqlCategory.FORBIDDEN

    def test_forbidden_block_comment(self):
        """블록 주석이 포함되면 FORBIDDEN입니다."""
        category, reason = classify_sql("SELECT /* test */ * FROM sales")
        assert category == SqlCategory.FORBIDDEN

    def test_forbidden_unclassifiable(self):
        """분류할 수 없는 SQL은 FORBIDDEN입니다."""
        category, reason = classify_sql("EXPLAIN ANALYZE SELECT 1")
        assert category == SqlCategory.FORBIDDEN

    # --- 엣지 케이스 ---

    def test_trailing_semicolon_allowed(self):
        """트레일링 세미콜론은 허용됩니다."""
        category, reason = classify_sql("SELECT * FROM sales;")
        assert category == SqlCategory.SAFE

    def test_case_insensitive(self):
        """대소문자를 구분하지 않습니다."""
        category, reason = classify_sql("select * from sales")
        assert category == SqlCategory.SAFE

    def test_whitespace_handling(self):
        """앞뒤 공백을 제거한 후 분류합니다."""
        category, reason = classify_sql("   SELECT * FROM sales   ")
        assert category == SqlCategory.SAFE

    def test_category_is_string_enum(self):
        """SqlCategory는 str을 상속하여 문자열 비교가 가능합니다."""
        assert SqlCategory.SAFE == "SAFE"
        assert SqlCategory.NEEDS_APPROVAL == "NEEDS_APPROVAL"


# ============================================
# create_request() 테스트
# ============================================

@pytest.mark.unit
class TestCreateRequest:
    """create_request() — 승인 요청 생성 테스트."""

    @patch("approval.approval_manager._log_action")
    @patch("approval.approval_manager.get_cursor")
    def test_create_request_success(self, mock_cursor_ctx, mock_log):
        """승인 요청 생성 성공 시 request_id를 반환합니다."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 42}
        mock_cursor_ctx.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor_ctx.return_value.__exit__ = MagicMock(return_value=False)

        req_id = create_request(
            sql="DELETE FROM sales WHERE year < 2020",
            title="2020년 이전 삭제",
            requested_by="kim",
        )
        assert req_id == 42

    @patch("approval.approval_manager._log_action")
    @patch("approval.approval_manager.get_cursor")
    def test_create_request_failure(self, mock_cursor_ctx, mock_log):
        """DB 오류 시 None을 반환합니다."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_cursor_ctx.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor_ctx.return_value.__exit__ = MagicMock(return_value=False)

        req_id = create_request(sql="DROP TABLE test", title="테스트")
        assert req_id is None


# ============================================
# approve_request() / deny_request() 테스트
# ============================================

@pytest.mark.unit
class TestApproveAndDeny:
    """approve_request(), deny_request() 테스트."""

    @patch("approval.approval_manager._log_action")
    @patch("approval.approval_manager.execute_command")
    def test_approve_success(self, mock_cmd, mock_log):
        """승인 성공 시 True를 반환합니다."""
        mock_cmd.return_value = 1  # 1행 업데이트

        result = approve_request(42, reviewed_by="admin")
        assert result is True

    @patch("approval.approval_manager._log_action")
    @patch("approval.approval_manager.execute_command")
    def test_approve_not_pending(self, mock_cmd, mock_log):
        """pending이 아닌 요청은 승인 실패."""
        mock_cmd.return_value = 0  # 0행 업데이트

        result = approve_request(42, reviewed_by="admin")
        assert result is False

    @patch("approval.approval_manager._log_action")
    @patch("approval.approval_manager.execute_command")
    def test_deny_success(self, mock_cmd, mock_log):
        """거부 성공 시 True를 반환합니다."""
        mock_cmd.return_value = 1

        result = deny_request(42, reviewed_by="admin", reason="위험한 작업")
        assert result is True

    @patch("approval.approval_manager._log_action")
    @patch("approval.approval_manager.execute_command")
    def test_deny_not_pending(self, mock_cmd, mock_log):
        """pending이 아닌 요청은 거부 실패."""
        mock_cmd.return_value = 0

        result = deny_request(42)
        assert result is False


# ============================================
# execute_approved() 테스트
# ============================================

@pytest.mark.unit
class TestExecuteApproved:
    """execute_approved() — 승인된 SQL 실행 테스트."""

    @patch("approval.approval_manager._log_action")
    @patch("approval.approval_manager.execute_command")
    @patch("approval.approval_manager.execute_query")
    def test_execute_approved_success(self, mock_query, mock_cmd, mock_log):
        """승인된 SQL 실행 성공."""
        mock_query.return_value = [{
            "id": 42,
            "sql_text": "DELETE FROM old_data WHERE year < 2020",
            "status": "approved",
            "requested_by": "kim",
        }]
        mock_cmd.return_value = 100  # 100행 삭제

        result = execute_approved(42)
        assert result["success"] is True
        assert result["rows_affected"] == 100

    @patch("approval.approval_manager.execute_query")
    def test_execute_not_approved(self, mock_query):
        """approved가 아닌 요청은 실행 거부."""
        mock_query.return_value = [{
            "id": 42,
            "sql_text": "DELETE FROM old_data",
            "status": "pending",
            "requested_by": "kim",
        }]

        result = execute_approved(42)
        assert result["success"] is False
        assert "상태" in result["message"]

    @patch("approval.approval_manager.execute_query")
    def test_execute_request_not_found(self, mock_query):
        """요청이 없으면 실행 실패."""
        mock_query.return_value = []

        result = execute_approved(999)
        assert result["success"] is False
        assert "찾을 수 없" in result["message"]
