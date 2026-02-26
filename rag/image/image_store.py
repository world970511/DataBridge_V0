"""
이미지 임베딩을 ChromaDB에 저장하고 시각적 유사도 검색을 수행하는 모듈.

기존 rag/embedder.py의 텍스트 기반 저장/검색과 달리,
이 모듈은 DINOv2가 사전 생성한 임베딩 벡터를 직접 ChromaDB에 전달합니다.
(ChromaDB의 자동 텍스트 임베딩 기능은 사용하지 않음)

ChromaDB 'images' 컬렉션:
    - embedding: DINOv2 벡터 (사전 계산, 384차원 ViT-S/14)
    - metadata: {source, file_path, width, height, camera_model, ...}
    - document: 이미지 설명 텍스트 (EXIF 기반, 검색 보조용)
"""

import logging
from typing import Optional

import chromadb.utils.embedding_functions as ef

from rag.embedder import get_chroma_client
from config.settings import get_settings

logger = logging.getLogger(__name__)

_collection = None


class _NoOpEmbeddingFunction(ef.EmbeddingFunction):
    """ChromaDB 자동 임베딩을 비활성화하기 위한 더미 함수."""

    def __call__(self, input):
        return [[0.0]] * len(input)


def get_image_collection():
    """
    ChromaDB 'images' 컬렉션을 가져오거나 생성.

    DINOv2 임베딩을 직접 전달하므로 자동 임베딩은 사용하지 않습니다.
    """
    global _collection

    if _collection is not None:
        return _collection

    settings = get_settings()
    client = get_chroma_client()

    _collection = client.get_or_create_collection(
        name=settings.image.collection_name,
        metadata={"hnsw:space": "cosine"},
        embedding_function=_NoOpEmbeddingFunction(),
    )

    logger.info(f"ChromaDB image collection ready: {settings.image.collection_name}")
    return _collection


def store_image_embedding(
    image_name: str,
    embedding: list[float],
    metadata: dict,
    description: str = "",
) -> None:
    """
    단일 이미지의 DINOv2 임베딩을 ChromaDB에 저장.

    Args:
        image_name: 이미지 파일명 (고유 ID로 사용).
        embedding: DINOv2 임베딩 벡터.
        metadata: EXIF 및 파일 메타데이터 딕셔너리.
        description: 이미지 설명 텍스트.
    """
    collection = get_image_collection()

    # ChromaDB metadata는 str/int/float/bool만 허용
    clean_meta = {
        k: v for k, v in metadata.items()
        if isinstance(v, (str, int, float, bool)) and v is not None
    }

    collection.upsert(
        ids=[image_name],
        embeddings=[embedding],
        metadatas=[clean_meta],
        documents=[description or f"Image: {image_name}"],
    )

    logger.debug(f"Stored embedding for: {image_name} (dim={len(embedding)})")


def search_similar_images(
    query_embedding: list[float],
    n_results: int = 10,
    where_filter: Optional[dict] = None,
) -> list[dict]:
    """
    DINOv2 임베딩으로 시각적으로 유사한 이미지를 검색.

    Args:
        query_embedding: 쿼리 이미지의 DINOv2 임베딩.
        n_results: 반환할 최대 결과 수.
        where_filter: ChromaDB where 필터.

    Returns:
        [{"image_name": str, "metadata": dict, "distance": float, "similarity": float}, ...]
    """
    collection = get_image_collection()

    kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": n_results,
    }
    if where_filter:
        kwargs["where"] = where_filter

    results = collection.query(**kwargs)

    items = []
    if results and results["ids"] and results["ids"][0]:
        for i, image_name in enumerate(results["ids"][0]):
            distance = results["distances"][0][i] if results.get("distances") else 0.0
            items.append({
                "image_name": image_name,
                "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                "distance": distance,
                "similarity": 1.0 - distance,  # cosine distance → similarity
            })

    return items


def search_similar_by_image_name(
    image_name: str,
    n_results: int = 10,
) -> list[dict]:
    """
    이미 저장된 이미지의 이름으로 유사 이미지를 검색.

    해당 이미지의 임베딩을 ChromaDB에서 조회한 뒤 검색합니다.
    """
    collection = get_image_collection()

    try:
        existing = collection.get(ids=[image_name], include=["embeddings"])
        embeddings = existing["embeddings"]
        if embeddings is None or len(embeddings) == 0 or len(embeddings[0]) == 0:
            logger.warning(f"No embedding found for: {image_name}")
            return []

        embedding = embeddings[0]
        # n_results+1 → 자기 자신 제외
        results = search_similar_images(embedding, n_results=n_results + 1)
        return [r for r in results if r["image_name"] != image_name][:n_results]

    except Exception as e:
        logger.error(f"Similar search failed for {image_name}: {e}")
        return []


def delete_image_embedding(image_name: str) -> int:
    """이미지의 ChromaDB 임베딩을 삭제. 삭제된 수를 반환."""
    collection = get_image_collection()

    try:
        existing = collection.get(ids=[image_name])
        if existing["ids"]:
            collection.delete(ids=[image_name])
            logger.debug(f"Deleted embedding: {image_name}")
            return 1
        return 0
    except Exception as e:
        logger.error(f"Failed to delete embedding {image_name}: {e}")
        return 0


def get_all_embeddings() -> dict[str, list[float]]:
    """
    ChromaDB 'images' 컬렉션의 모든 임베딩을 조회.

    클러스터링(image grouping)에 사용됩니다.

    Returns:
        {image_name: embedding_vector, ...} 딕셔너리.
    """
    collection = get_image_collection()

    try:
        all_data = collection.get(include=["embeddings"])
        result = {}
        if all_data["ids"] and all_data["embeddings"]:
            for name, emb in zip(all_data["ids"], all_data["embeddings"]):
                result[name] = emb
        return result
    except Exception as e:
        logger.error(f"Failed to get all embeddings: {e}")
        return {}
