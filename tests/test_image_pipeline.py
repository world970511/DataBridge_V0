"""
이미지 파이프라인 통합 테스트.

classifier → image_loader → catalog/ChromaDB 전체 흐름을 테스트합니다.
외부 서비스(DB, ChromaDB, Ollama)는 mock으로 대체합니다.
"""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image


class TestClassifierImageRouting:
    """classifier의 이미지 파일 분류 및 라우팅 테스트."""

    def test_get_file_type_image(self):
        """이미지 확장자 → file_type 'image'."""
        from watcher.classifier import get_file_type

        assert get_file_type("photo.jpg") == "image"
        assert get_file_type("photo.jpeg") == "image"
        assert get_file_type("photo.png") == "image"
        assert get_file_type("photo.tiff") == "image"
        assert get_file_type("photo.webp") == "image"
        assert get_file_type("photo.bmp") == "image"

    def test_get_file_type_non_image(self):
        """비이미지 파일은 'image'가 아닌 다른 타입."""
        from watcher.classifier import get_file_type

        assert get_file_type("data.csv") != "image"
        assert get_file_type("doc.pdf") != "image"

    def test_get_file_action_image(self):
        """이미지 파일 → 'register_image' 액션."""
        from watcher.classifier import get_file_action

        assert get_file_action("photo.jpg") == "register_image"
        assert get_file_action("photo.png") == "register_image"
        assert get_file_action("photo.tiff") == "register_image"


class TestImageLoader:
    """image_loader 통합 테스트 (외부 의존성 mock)."""

    def _create_test_image(self, tmp_path: Path) -> str:
        """테스트용 이미지 생성."""
        img = Image.new("RGB", (200, 150), color="blue")
        path = tmp_path / "test_photo.jpg"
        img.save(str(path), "JPEG")
        return str(path)

    @patch("watcher.loader.image_loader.register_image")
    @patch("watcher.loader.image_loader.store_image_embedding")
    @patch("watcher.loader.image_loader.delete_image_embedding")
    @patch("watcher.loader.image_loader.compute_embedding")
    @patch("watcher.loader.image_loader.generate_thumbnail")
    @patch("watcher.loader.image_loader.log_file_process")
    def test_load_image_full_pipeline(
        self,
        mock_log,
        mock_thumb,
        mock_embed,
        mock_delete_embed,
        mock_store,
        mock_register,
        tmp_path,
    ):
        """전체 이미지 적재 파이프라인."""
        from watcher.loader.image_loader import load_image

        path = self._create_test_image(tmp_path)

        # mock 설정
        mock_embed.return_value = [0.1] * 384
        mock_thumb.return_value = str(tmp_path / "thumb.jpg")
        mock_delete_embed.return_value = 0

        load_image(path, "image")

        # 각 단계가 호출되었는지 확인
        mock_embed.assert_called_once()
        mock_thumb.assert_called_once()
        mock_store.assert_called_once()
        mock_register.assert_called_once()
        mock_log.assert_called()

    @patch("watcher.loader.image_loader.register_image")
    @patch("watcher.loader.image_loader.store_image_embedding")
    @patch("watcher.loader.image_loader.delete_image_embedding")
    @patch("watcher.loader.image_loader.compute_embedding")
    @patch("watcher.loader.image_loader.generate_thumbnail")
    @patch("watcher.loader.image_loader.log_file_process")
    def test_load_image_embedding_failure(
        self,
        mock_log,
        mock_thumb,
        mock_embed,
        mock_delete_embed,
        mock_store,
        mock_register,
        tmp_path,
    ):
        """임베딩 실패 시에도 카탈로그에는 등록 (graceful degradation)."""
        from watcher.loader.image_loader import load_image

        path = self._create_test_image(tmp_path)

        mock_embed.return_value = None  # 임베딩 실패
        mock_thumb.return_value = str(tmp_path / "thumb.jpg")

        load_image(path, "image")

        # 임베딩 실패해도 카탈로그 등록은 수행
        mock_register.assert_called_once()
        # ChromaDB 저장은 호출되지 않음
        mock_store.assert_not_called()


class TestCleanupImage:
    """이미지 삭제 정리 테스트."""

    @patch("watcher.cleanup.remove_image")
    @patch("watcher.cleanup.delete_image_embedding")
    @patch("watcher.cleanup.get_file_type")
    @patch("watcher.cleanup.log_file_process")
    def test_cleanup_image(self, mock_log, mock_type, mock_del_embed, mock_remove):
        """이미지 삭제 시 ChromaDB + 카탈로그 + 썸네일 정리."""
        from watcher.cleanup import cleanup_file

        mock_type.return_value = "image"
        mock_del_embed.return_value = 1
        mock_remove.return_value = {
            "image_name": "test.jpg",
            "thumbnail_path": None,
        }

        cleanup_file("/data/test.jpg", "register_image")

        mock_del_embed.assert_called_once()
        mock_remove.assert_called_once()


class TestOrchestratorImageRouting:
    """오케스트레이터의 이미지 의도 분류 테스트."""

    @patch("agent.orchestrator.get_image_names")
    @patch("agent.orchestrator.get_document_names")
    @patch("agent.orchestrator.get_table_names")
    @patch("agent.orchestrator.get_table_tags")
    def test_classify_image_keywords(
        self, mock_tags, mock_tables, mock_docs, mock_images
    ):
        """이미지 키워드 → 'image' 의도."""
        from agent.orchestrator import classify_intent

        mock_tables.return_value = []
        mock_docs.return_value = []
        mock_images.return_value = []
        mock_tags.return_value = {}

        assert classify_intent("비슷한 이미지 찾아줘") == "image"
        assert classify_intent("중복 사진 확인해줘") == "image"
        assert classify_intent("이미지 클러스터링 해줘") == "image"

    @patch("agent.orchestrator.get_image_names")
    @patch("agent.orchestrator.get_document_names")
    @patch("agent.orchestrator.get_table_names")
    @patch("agent.orchestrator.get_table_tags")
    def test_classify_image_name_match(
        self, mock_tags, mock_tables, mock_docs, mock_images
    ):
        """이미지 파일명 매칭 → 'image' 의도."""
        from agent.orchestrator import classify_intent

        mock_tables.return_value = []
        mock_docs.return_value = []
        mock_images.return_value = ["sunset.jpg", "beach.png"]
        mock_tags.return_value = {}

        assert classify_intent("sunset.jpg와 비슷한 것 찾아줘") == "image"

    def test_classify_image_sub_intent(self):
        """이미지 세부 의도 분류."""
        from agent.orchestrator import _classify_image_sub_intent

        assert _classify_image_sub_intent("중복 이미지 확인") == "duplicates"
        assert _classify_image_sub_intent("이미지 그룹으로 묶어줘") == "group"
        assert _classify_image_sub_intent("이미지 목록 보여줘") == "list"
        assert _classify_image_sub_intent("sunset.jpg 정보 알려줘") == "info"
        assert _classify_image_sub_intent("비슷한 이미지 찾아줘") == "search"

    @patch("agent.orchestrator.get_image_names")
    @patch("agent.orchestrator.get_document_names")
    @patch("agent.orchestrator.get_table_names")
    @patch("agent.orchestrator.get_table_tags")
    def test_classify_list_image(
        self, mock_tags, mock_tables, mock_docs, mock_images
    ):
        """이미지 목록 요청 → 'list_image' 의도."""
        from agent.orchestrator import classify_intent

        mock_tables.return_value = []
        mock_docs.return_value = []
        mock_images.return_value = []
        mock_tags.return_value = {}

        assert classify_intent("이미지 목록 보여줘") == "list_image"
        assert classify_intent("사진 리스트") == "list_image"
