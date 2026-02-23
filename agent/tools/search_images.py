"""
ChromaDB 이미지 검색 도구.

rag.image.image_store 모듈을 래핑하여 AI 에이전트가 사용하기 적합한
인터페이스로 시각적 이미지 검색을 제공합니다.
"""

import logging

from rag.image.image_store import search_similar_by_image_name, get_all_embeddings
from catalog.catalog import list_images, get_image_by_name

logger = logging.getLogger(__name__)


def search_by_name(image_name: str, n_results: int = 10) -> list[dict]:
    """
    이미지명으로 시각적으로 유사한 이미지를 검색.

    Args:
        image_name: 쿼리 이미지의 파일명.
        n_results: 최대 결과 수.

    Returns:
        [{"image_name": str, "similarity": float, "metadata": dict}, ...]
    """
    try:
        results = search_similar_by_image_name(image_name, n_results=n_results)

        # catalog에서 썸네일 경로 보강
        for r in results:
            catalog_info = get_image_by_name(r["image_name"])
            if catalog_info:
                r["thumbnail_path"] = catalog_info.get("thumbnail_path", "")
                r["source_file"] = catalog_info.get("source_file", "")

        return results

    except Exception as e:
        logger.error(f"Image search failed for {image_name}: {e}")
        return []


def search_all_images() -> list[dict]:
    """카탈로그에 등록된 모든 이미지 메타데이터를 반환."""
    try:
        return list_images()
    except Exception as e:
        logger.error(f"Failed to list images: {e}")
        return []


def get_image_names() -> list[str]:
    """카탈로그에 등록된 이미지명 리스트. 의도 분류 시 사용."""
    try:
        images = list_images()
        return [i["image_name"] for i in images if "image_name" in i]
    except Exception as e:
        logger.error(f"Failed to get image names: {e}")
        return []


def format_image_results(results: list[dict]) -> str:
    """검색 결과를 사용자에게 표시할 텍스트로 포맷팅."""
    if not results:
        return "유사한 이미지를 찾지 못했습니다."

    lines = [f"유사 이미지 {len(results)}건을 찾았습니다:\n"]
    for i, r in enumerate(results, 1):
        sim = r.get("similarity", 0)
        name = r.get("image_name", "unknown")
        lines.append(f"{i}. **{name}** (유사도: {sim:.2%})")

    return "\n".join(lines)
