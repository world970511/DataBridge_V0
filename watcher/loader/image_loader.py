"""
이미지 처리 파이프라인 모듈.

이미지 파일 업로드 시:
1. EXIF 메타데이터 추출 (Pillow)
2. DINOv2 임베딩 생성 (torch)
3. 썸네일 생성 (Pillow)
4. ChromaDB 'images' 컬렉션에 임베딩 저장
5. PostgreSQL catalog_images에 메타데이터 등록

document_loader.py의 패턴을 따릅니다.
"""

import logging
import os
from pathlib import Path

from catalog.catalog import register_image
from config.settings import get_settings
from watcher.loader._utils import log_file_process

logger = logging.getLogger(__name__)


def load_image(file_path: str, file_type: str):
    """
    이미지 파일의 전체 처리 파이프라인.

    Args:
        file_path: 이미지 파일 절대 경로.
        file_type: 파일 유형 ('image').
    """
    path = Path(file_path)
    settings = get_settings()

    try:
        # 0. 파일 크기 확인
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        if file_size_mb > settings.image.max_image_size_mb:
            logger.info(f"Image too large, skipping: {path.name} ({file_size_mb:.1f}MB)")
            log_file_process(
                file_path, file_type, "register_image", None, "skipped",
                f"File too large ({file_size_mb:.1f}MB)",
            )
            return

        # 1. EXIF 메타데이터 추출
        from rag.image.exif_extractor import extract_exif
        exif_data = extract_exif(file_path)

        # 2. DINOv2 임베딩 생성
        from rag.image.dino_embedder import compute_embedding
        embedding = compute_embedding(file_path)
        if embedding is None:
            logger.warning(f"Failed to compute DINOv2 embedding: {path.name}")
            log_file_process(
                file_path, file_type, "register_image", None, "failed",
                "DINOv2 embedding failed",
            )
            return

        # 3. 썸네일 생성
        from rag.image.thumbnail import generate_thumbnail
        thumbnail_path = generate_thumbnail(file_path)

        # 4. ChromaDB에 임베딩 저장 (기존 임베딩 삭제 후 재저장)
        from rag.image.image_store import store_image_embedding, delete_image_embedding
        delete_image_embedding(path.name)

        metadata = {
            "source": path.name,
            "file_path": str(file_path),
            "width": exif_data.width,
            "height": exif_data.height,
            "camera_model": exif_data.camera_model or "",
        }

        description = _build_image_description(path.name, exif_data)
        store_image_embedding(
            image_name=path.name,
            embedding=embedding,
            metadata=metadata,
            description=description,
        )

        # 5. PostgreSQL catalog에 등록
        register_image(
            image_name=path.name,
            source_file=str(file_path),
            file_type=file_type,
            file_size_bytes=file_size,
            width=exif_data.width,
            height=exif_data.height,
            camera_make=exif_data.camera_make,
            camera_model=exif_data.camera_model,
            lens_info=exif_data.lens_info,
            focal_length=exif_data.focal_length,
            aperture=exif_data.aperture,
            shutter_speed=exif_data.shutter_speed,
            iso=exif_data.iso,
            date_taken=exif_data.date_taken,
            gps_latitude=exif_data.gps_latitude,
            gps_longitude=exif_data.gps_longitude,
            gps_altitude=exif_data.gps_altitude,
            orientation=exif_data.orientation,
            embedding_dim=len(embedding),
            collection_name=settings.image.collection_name,
            thumbnail_path=thumbnail_path,
            exif_json=exif_data.exif_raw,
        )

        log_file_process(file_path, file_type, "register_image", None, "success")
        logger.info(
            f"Image loaded: {path.name} ({exif_data.width}x{exif_data.height}, "
            f"embedding_dim={len(embedding)})"
        )

    except Exception as e:
        logger.exception(f"Failed to load image: {file_path}")
        log_file_process(file_path, file_type, "register_image", None, "failed", str(e))


def _build_image_description(image_name: str, exif_data) -> str:
    """EXIF 메타데이터 기반 이미지 설명 텍스트 생성 (ChromaDB document 필드용)."""
    parts = [f"Image: {image_name}"]
    if exif_data.camera_model:
        parts.append(f"Camera: {exif_data.camera_model}")
    if exif_data.date_taken:
        parts.append(f"Date: {exif_data.date_taken.strftime('%Y-%m-%d')}")
    if exif_data.width and exif_data.height:
        parts.append(f"Size: {exif_data.width}x{exif_data.height}")
    if exif_data.gps_latitude and exif_data.gps_longitude:
        parts.append(f"GPS: {exif_data.gps_latitude:.4f}, {exif_data.gps_longitude:.4f}")
    return " | ".join(parts)
