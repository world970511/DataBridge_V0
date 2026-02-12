"""
텍스트 → 벡터 임베딩 → ChromaDB 저장.
Ollama 임베딩 모델 또는 ChromaDB 기본 임베딩 사용.
"""

import logging
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

from config.settings import get_settings
from rag.chunker import Chunk

logger = logging.getLogger(__name__)

_client: Optional[chromadb.HttpClient] = None


def get_chroma_client() -> chromadb.HttpClient:
    """ChromaDB HTTP 클라이언트 싱글톤."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = chromadb.HttpClient(
            host=settings.chroma.host,
            port=settings.chroma.port,
        )
        logger.info(f"ChromaDB client connected: {settings.chroma.url}")
    return _client


def store_chunks(chunks: list[Chunk], collection_name: str = "documents"):
    """
    청크 리스트를 ChromaDB에 저장.
    ChromaDB 기본 임베딩 함수 사용 (all-MiniLM-L6-v2).
    """
    if not chunks:
        return

    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    ids = [c.id for c in chunks]
    documents = [c.text for c in chunks]
    metadatas = [c.metadata for c in chunks]

    # ChromaDB는 기본 임베딩 함수를 내장 (Sentence Transformers)
    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )

    logger.info(f"Stored {len(chunks)} chunks in collection '{collection_name}'")


def search_documents(
    query: str,
    collection_name: str = "documents",
    n_results: int = 5,
) -> list[dict]:
    """
    ChromaDB에서 유사 문서 검색.
    Returns: [{"text": ..., "metadata": ..., "distance": ...}, ...]
    """
    client = get_chroma_client()

    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        logger.warning(f"Collection '{collection_name}' not found")
        return []

    results = collection.query(
        query_texts=[query],
        n_results=n_results,
    )

    output = []
    if results and results["documents"]:
        for i, doc in enumerate(results["documents"][0]):
            output.append({
                "text": doc,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else None,
            })

    return output


def check_chroma_connection() -> bool:
    """ChromaDB 연결 상태 확인."""
    try:
        client = get_chroma_client()
        client.heartbeat()
        return True
    except Exception as e:
        logger.error(f"ChromaDB connection failed: {e}")
        return False
