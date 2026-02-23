"""
이미지 처리 유닛 테스트.

EXIF 추출, DINOv2 임베딩, 썸네일 생성, ChromaDB 저장/검색,
클러스터링, 중복 탐지 기능을 테스트합니다.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image


# ==============================================================
# EXIF 추출 테스트
# ==============================================================

class TestExifExtractor:
    """EXIF 추출기 유닛 테스트."""

    def _create_test_image(self, tmp_path: Path, fmt: str = "JPEG") -> str:
        """테스트용 이미지 파일 생성."""
        img = Image.new("RGB", (100, 100), color="red")
        ext = ".jpg" if fmt == "JPEG" else ".png"
        path = tmp_path / f"test{ext}"
        img.save(str(path), fmt)
        return str(path)

    def test_extract_exif_basic(self, tmp_path):
        """EXIF 없는 이미지에서도 width/height는 추출되어야 함."""
        from rag.image.exif_extractor import extract_exif

        path = self._create_test_image(tmp_path)
        result = extract_exif(path)

        assert result.width == 100
        assert result.height == 100
        # EXIF가 없으므로 카메라 정보는 None
        assert result.camera_make is None
        assert result.camera_model is None

    def test_extract_exif_png(self, tmp_path):
        """PNG 이미지에서도 width/height 추출."""
        from rag.image.exif_extractor import extract_exif

        path = self._create_test_image(tmp_path, fmt="PNG")
        result = extract_exif(path)

        assert result.width == 100
        assert result.height == 100

    def test_extract_exif_nonexistent_file(self):
        """존재하지 않는 파일은 기본값 ImageExifData 반환."""
        from rag.image.exif_extractor import extract_exif

        result = extract_exif("/nonexistent/image.jpg")
        assert result.width == 0
        assert result.height == 0

    def test_gps_dms_to_decimal(self):
        """GPS DMS → 십진도 변환 검증."""
        from rag.image.exif_extractor import _dms_to_decimal

        # 37° 30' 0" N → 37.5
        result = _dms_to_decimal((37, 30, 0), "N")
        assert abs(result - 37.5) < 0.001

        # 127° 0' 0" E → 127.0
        result = _dms_to_decimal((127, 0, 0), "E")
        assert abs(result - 127.0) < 0.001

        # 남위/서경은 음수
        result = _dms_to_decimal((33, 30, 0), "S")
        assert result < 0

    def test_parse_exif_datetime(self):
        """EXIF datetime 파싱 검증."""
        from rag.image.exif_extractor import _parse_exif_datetime

        dt = _parse_exif_datetime("2024:06:15 14:30:00")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 15

        # 잘못된 형식은 None
        assert _parse_exif_datetime("invalid") is None
        assert _parse_exif_datetime(None) is None


# ==============================================================
# 썸네일 생성 테스트
# ==============================================================

class TestThumbnail:
    """썸네일 생성기 유닛 테스트."""

    def test_generate_thumbnail(self, tmp_path):
        """정상적인 썸네일 생성."""
        from rag.image.thumbnail import generate_thumbnail

        # 원본 이미지 생성
        img = Image.new("RGB", (800, 600), color="blue")
        source = tmp_path / "source.jpg"
        img.save(str(source), "JPEG")

        thumb_dir = tmp_path / "thumbs"
        thumb_dir.mkdir()

        result = generate_thumbnail(str(source), str(thumb_dir), max_size=128)
        assert result is not None
        assert Path(result).exists()

        # 썸네일 크기 확인
        thumb = Image.open(result)
        assert max(thumb.size) <= 128

    def test_generate_thumbnail_small_image(self, tmp_path):
        """원본보다 큰 썸네일 사이즈 → 원본 크기 유지."""
        from rag.image.thumbnail import generate_thumbnail

        img = Image.new("RGB", (64, 64), color="green")
        source = tmp_path / "small.jpg"
        img.save(str(source), "JPEG")

        thumb_dir = tmp_path / "thumbs"
        thumb_dir.mkdir()

        result = generate_thumbnail(str(source), str(thumb_dir), max_size=256)
        assert result is not None


# ==============================================================
# 클러스터링 테스트
# ==============================================================

class TestClustering:
    """이미지 클러스터링 유닛 테스트."""

    def test_cluster_images_minimum(self):
        """이미지 1개 이하 → 빈 결과."""
        from rag.image.clustering import cluster_images

        assert cluster_images({}) == {}
        assert cluster_images({"a": [1.0, 0.0]}) == {}

    @patch("rag.image.clustering.get_settings")
    def test_cluster_images_similar(self, mock_settings):
        """유사한 임베딩 → 같은 그룹."""
        from rag.image.clustering import cluster_images

        mock_cfg = MagicMock()
        mock_cfg.image.near_duplicate_threshold = 0.90
        mock_settings.return_value = mock_cfg

        embeddings = {
            "a.jpg": [1.0, 0.0, 0.0],
            "b.jpg": [0.99, 0.01, 0.0],
            "c.jpg": [0.0, 0.0, 1.0],  # 다른 그룹
        }

        groups = cluster_images(embeddings, distance_threshold=0.05)
        # a와 b는 같은 그룹, c는 단독 → 단독은 제외되므로 1그룹
        assert len(groups) >= 1
        found = False
        for members in groups.values():
            if "a.jpg" in members and "b.jpg" in members:
                found = True
        assert found

    @patch("rag.image.clustering.get_settings")
    def test_find_duplicates(self, mock_settings):
        """코사인 유사도 기반 중복 탐지."""
        from rag.image.clustering import find_duplicates

        mock_cfg = MagicMock()
        mock_cfg.image.similarity_threshold = 0.95
        mock_settings.return_value = mock_cfg

        embeddings = {
            "a.jpg": [1.0, 0.0],
            "b.jpg": [0.999, 0.001],  # 거의 동일
            "c.jpg": [0.0, 1.0],       # 완전히 다름
        }

        dups = find_duplicates(embeddings, threshold=0.99)
        # a와 b의 코사인 유사도가 ~0.9999이므로 중복
        assert len(dups) >= 1

    def test_find_duplicates_no_data(self):
        """데이터 부족 시 빈 결과."""
        from rag.image.clustering import find_duplicates

        assert find_duplicates({}) == []
        assert find_duplicates({"a": [1.0]}) == []


# ==============================================================
# DINOv2 임베딩 테스트 (모델 로드는 mock)
# ==============================================================

class TestDinoEmbedder:
    """DINOv2 임베딩 유닛 테스트 (torch 의존성은 mock)."""

    @patch("rag.image.dino_embedder.get_dino_model")
    @patch("rag.image.dino_embedder.get_transform")
    def test_compute_embedding(self, mock_transform, mock_model, tmp_path):
        """단일 이미지 임베딩 계산."""
        import torch

        # mock 모델: 384차원 벡터 반환
        mock_net = MagicMock()
        mock_net.return_value = torch.randn(1, 384)
        mock_model.return_value = (mock_net, "cpu")
        mock_transform.return_value = MagicMock(return_value=torch.randn(3, 224, 224))

        from rag.image.dino_embedder import compute_embedding

        img = Image.new("RGB", (100, 100), color="red")
        path = tmp_path / "test.jpg"
        img.save(str(path))

        result = compute_embedding(str(path))
        assert result is not None
        assert len(result) == 384


# ==============================================================
# ChromaDB 이미지 저장소 테스트
# ==============================================================

class TestImageStore:
    """ChromaDB 이미지 컬렉션 유닛 테스트."""

    @patch("rag.image.image_store.get_image_collection")
    def test_store_and_search(self, mock_collection):
        """저장 후 검색 — mock 기반."""
        from rag.image.image_store import store_image_embedding, search_similar_images

        mock_col = MagicMock()
        mock_collection.return_value = mock_col

        # 저장 테스트
        store_image_embedding(
            image_name="test.jpg",
            embedding=[0.1] * 384,
            metadata={"source_file": "/data/test.jpg"},
            description="test image",
        )
        mock_col.upsert.assert_called_once()

        # 검색 테스트
        mock_col.query.return_value = {
            "ids": [["img1", "img2"]],
            "distances": [[0.1, 0.3]],
            "metadatas": [[{"key": "val"}, {"key": "val2"}]],
        }

        results = search_similar_images([0.1] * 384, n_results=2)
        assert len(results) == 2
        assert results[0]["image_name"] == "img1"
        assert "similarity" in results[0]

    @patch("rag.image.image_store.get_image_collection")
    def test_delete_embedding(self, mock_collection):
        """임베딩 삭제."""
        from rag.image.image_store import delete_image_embedding

        mock_col = MagicMock()
        mock_col.get.return_value = {"ids": ["test.jpg"]}
        mock_collection.return_value = mock_col

        count = delete_image_embedding("test.jpg")
        assert count == 1
        mock_col.delete.assert_called_once()
