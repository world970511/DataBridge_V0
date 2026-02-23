"""
삭제 승인 시스템 테스트.

웹 UI를 통한 삭제 요청 생성, 승인, 실행 흐름을 테스트합니다.
테이블/문서/이미지 각 유형에 대해 삭제 승인 워크플로우를 검증합니다.
"""

from unittest.mock import patch, MagicMock, call

import pytest


class TestCreateDeleteRequest:
    """삭제 승인 요청 생성 테스트."""

    @patch("approval.approval_manager.create_request")
    def test_create_delete_request_table(self, mock_create):
        """테이블 삭제 요청 생성."""
        from approval.approval_manager import create_delete_request

        mock_create.return_value = 1

        req_id = create_delete_request(
            resource_type="table",
            resource_name="t_sales_2024",
            source_file="/data/sales.csv",
            requested_by="user1",
            details={"row_count": 1000, "column_count": 5},
        )

        assert req_id == 1
        mock_create.assert_called_once()

        # create_request 호출 인자 검증
        call_kwargs = mock_create.call_args
        assert call_kwargs[1]["request_type"] == "delete_resource"
        assert call_kwargs[1]["sql_category"] == "NEEDS_APPROVAL"
        assert "삭제" in call_kwargs[1]["title"]

    @patch("approval.approval_manager.create_request")
    def test_create_delete_request_document(self, mock_create):
        """문서 삭제 요청 생성."""
        from approval.approval_manager import create_delete_request

        mock_create.return_value = 2

        req_id = create_delete_request(
            resource_type="document",
            resource_name="report.pdf",
            source_file="/data/report.pdf",
            requested_by="user1",
        )

        assert req_id == 2

    @patch("approval.approval_manager.create_request")
    def test_create_delete_request_image(self, mock_create):
        """이미지 삭제 요청 생성."""
        from approval.approval_manager import create_delete_request

        mock_create.return_value = 3

        req_id = create_delete_request(
            resource_type="image",
            resource_name="sunset.jpg",
            source_file="/data/sunset.jpg",
            requested_by="user1",
            details={"thumbnail_path": "/thumbnails/sunset_thumb.jpg"},
        )

        assert req_id == 3


class TestExecuteDeleteApproved:
    """승인된 삭제 요청 실행 테스트."""

    @patch("approval.approval_manager.execute_query")
    @patch("approval.approval_manager.get_cursor")
    def test_execute_delete_not_approved(self, mock_cursor, mock_query):
        """미승인 상태에서 실행 시도 → 실패."""
        from approval.approval_manager import execute_delete_approved

        # status가 'pending'인 요청
        mock_query.return_value = [{
            "id": 1,
            "status": "pending",
            "request_type": "delete_resource",
            "metadata": '{"resource_type": "table", "resource_name": "t_test", "source_file": "/data/test.csv"}',
        }]

        result = execute_delete_approved(1)
        assert result["success"] is False

    @patch("approval.approval_manager._execute_delete_table")
    @patch("approval.approval_manager.log_file_process")
    @patch("approval.approval_manager.execute_query")
    @patch("approval.approval_manager.get_cursor")
    def test_execute_delete_table(self, mock_cursor, mock_query, mock_log, mock_del_table):
        """테이블 삭제 실행."""
        from approval.approval_manager import execute_delete_approved

        mock_query.return_value = [{
            "id": 1,
            "status": "approved",
            "request_type": "delete_resource",
            "metadata": '{"resource_type": "table", "resource_name": "t_test", "source_file": "/data/test.csv", "details": {}}',
        }]

        mock_del_table.return_value = None
        mock_ctx = MagicMock()
        mock_cursor.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = execute_delete_approved(1)
        assert result["success"] is True
        mock_del_table.assert_called_once_with("t_test")

    @patch("approval.approval_manager._execute_delete_document")
    @patch("approval.approval_manager.log_file_process")
    @patch("approval.approval_manager.execute_query")
    @patch("approval.approval_manager.get_cursor")
    def test_execute_delete_document(self, mock_cursor, mock_query, mock_log, mock_del_doc):
        """문서 삭제 실행."""
        from approval.approval_manager import execute_delete_approved

        mock_query.return_value = [{
            "id": 2,
            "status": "approved",
            "request_type": "delete_resource",
            "metadata": '{"resource_type": "document", "resource_name": "report.pdf", "source_file": "/data/report.pdf", "details": {}}',
        }]

        mock_del_doc.return_value = None
        mock_ctx = MagicMock()
        mock_cursor.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = execute_delete_approved(2)
        assert result["success"] is True
        mock_del_doc.assert_called_once_with("/data/report.pdf")

    @patch("approval.approval_manager._execute_delete_image")
    @patch("approval.approval_manager.log_file_process")
    @patch("approval.approval_manager.execute_query")
    @patch("approval.approval_manager.get_cursor")
    def test_execute_delete_image(self, mock_cursor, mock_query, mock_log, mock_del_img):
        """이미지 삭제 실행."""
        from approval.approval_manager import execute_delete_approved

        mock_query.return_value = [{
            "id": 3,
            "status": "approved",
            "request_type": "delete_resource",
            "metadata": '{"resource_type": "image", "resource_name": "sunset.jpg", "source_file": "/data/sunset.jpg", "details": {}}',
        }]

        mock_del_img.return_value = None
        mock_ctx = MagicMock()
        mock_cursor.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = execute_delete_approved(3)
        assert result["success"] is True
        mock_del_img.assert_called_once_with("/data/sunset.jpg")


class TestDeleteHelpers:
    """삭제 실행 헬퍼 함수 테스트."""

    @patch("approval.approval_manager.remove_table")
    @patch("approval.approval_manager.get_cursor")
    def test_execute_delete_table_helper(self, mock_cursor, mock_remove):
        """_execute_delete_table: DROP TABLE + 카탈로그 삭제."""
        from approval.approval_manager import _execute_delete_table

        mock_ctx = MagicMock()
        mock_cursor.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_cursor.return_value.__exit__ = MagicMock(return_value=False)

        _execute_delete_table("t_test")

        # DROP TABLE 실행 확인
        mock_ctx.execute.assert_called_once()
        assert "DROP TABLE" in mock_ctx.execute.call_args[0][0]
        # 카탈로그 삭제 확인
        mock_remove.assert_called_once_with("t_test")

    @patch("approval.approval_manager.remove_document")
    @patch("approval.approval_manager.delete_chunks")
    def test_execute_delete_document_helper(self, mock_del_chunks, mock_remove):
        """_execute_delete_document: ChromaDB 청크 삭제 + 카탈로그 삭제."""
        from approval.approval_manager import _execute_delete_document

        mock_del_chunks.return_value = 5
        mock_remove.return_value = {"doc_name": "report.pdf"}

        _execute_delete_document("/data/report.pdf")

        mock_del_chunks.assert_called_once()
        mock_remove.assert_called_once_with(source_file="/data/report.pdf")

    @patch("approval.approval_manager.remove_image")
    @patch("approval.approval_manager.delete_image_embedding")
    def test_execute_delete_image_helper(self, mock_del_embed, mock_remove):
        """_execute_delete_image: ChromaDB 임베딩 삭제 + 카탈로그 삭제 + 썸네일 삭제."""
        from approval.approval_manager import _execute_delete_image

        mock_del_embed.return_value = 1
        mock_remove.return_value = {
            "image_name": "sunset.jpg",
            "thumbnail_path": None,
        }

        _execute_delete_image("/data/sunset.jpg")

        mock_del_embed.assert_called_once()
        mock_remove.assert_called_once_with(source_file="/data/sunset.jpg")
