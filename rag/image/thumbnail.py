"""
이미지 썸네일 생성 모듈.

Pillow를 사용하여 이미지의 축소 썸네일을 생성합니다.
EXIF orientation을 자동 보정하며, UI에서 이미지 검색 결과 표시에 사용합니다.
"""

import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from config.settings import get_settings

logger = logging.getLogger(__name__)


def generate_thumbnail(
    source_path: str,
    thumbnail_dir: Optional[str] = None,
    max_size: Optional[int] = None,
) -> Optional[str]:
    """
    이미지에서 썸네일을 생성하여 저장.

    Args:
        source_path: 원본 이미지 파일 경로.
        thumbnail_dir: 썸네일 저장 디렉토리. None이면 Settings 사용.
        max_size: 썸네일 최대 크기(px). None이면 Settings 사용.

    Returns:
        생성된 썸네일 파일 경로. 실패 시 None.
    """
    settings = get_settings()
    thumb_dir = Path(thumbnail_dir or settings.image.thumbnail_dir)
    size = max_size or settings.image.thumbnail_size

    try:
        thumb_dir.mkdir(parents=True, exist_ok=True)

        source = Path(source_path)
        thumb_name = f"{source.stem}_thumb.jpg"
        thumb_path = thumb_dir / thumb_name

        with Image.open(source_path) as img:
            # EXIF orientation 자동 보정
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            img.save(str(thumb_path), "JPEG", quality=85)

        logger.debug(f"Thumbnail created: {thumb_path}")
        return str(thumb_path)

    except Exception as e:
        logger.warning(f"Thumbnail generation failed for {source_path}: {e}")
        return None
