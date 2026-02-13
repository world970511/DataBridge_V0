"""
텍스트 청크를 벡터 임베딩하여 ChromaDB에 저장하고 유사도 검색을 수행하는 모듈.

ChromaDB HTTP 클라이언트를 싱글톤으로 관리하며,
ChromaDB 내장 임베딩 함수(Sentence Transformers all-MiniLM-L6-v2)를 사용합니다.
코사인 유사도 기반의 HNSW 인덱스로 시맨틱 검색을 지원합니다.
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
    """
    ChromaDB HTTP 클라이언트 싱글톤 인스턴스를 반환.

    최초 호출 시 Settings의 chroma.host, chroma.port를 사용하여 연결하고,
    이후 호출에서는 동일 인스턴스를 재사용합니다.
    Returns: chromadb.HttpClient 인스턴스.
    """
    global _client
    if _client is None:
        settings = get_settings()
        _client = chromadb.HttpClient(
            host=settings.chroma.host,
            port=settings.chroma.port,
        )
        logger.info(f"ChromaDB client connected: {settings.chroma.url}")
    return _client


def store_chunks(
    chunks: list[Chunk],
    collection_name: str = "documents",
    batch_size: int = 50,
):
    """
    Chunk 리스트의 텍스트를 ChromaDB 컬렉션에 벡터 임베딩과 함께 저장.

    컬렉션이 없으면 코사인 유사도 기반 HNSW 인덱스로 자동 생성합니다.
    각 청크의 id, text(documents), metadata를 전달하면
    ChromaDB 내장 Sentence Transformers(all-MiniLM-L6-v2)가 자동으로 임베딩을 생성합니다.
    대량 청크는 batch_size 단위로 분할하여 ChromaDB 메모리 부담을 줄입니다.
    빈 청크 리스트가 전달되면 아무 작업도 하지 않습니다.
    """
    if not chunks:
        return

    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # 배치 단위로 분할하여 임베딩 — 메모리 부족(OOM) 방지
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        ids = [c.id for c in batch]
        documents = [c.text for c in batch]
        metadatas = [c.metadata for c in batch]

        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        logger.debug(f"Stored batch {i // batch_size + 1}: {len(batch)} chunks")

    logger.info(f"Stored {len(chunks)} chunks in collection '{collection_name}'")


def search_documents(
    query: str,
    collection_name: str = "documents",
    n_results: int = 5,
) -> list[dict]:
    """
    쿼리 텍스트와 의미적으로 유사한 문서 청크를 ChromaDB에서 검색.

    쿼리를 임베딩하여 코사인 유사도 기반으로 가장 가까운 n_results개의 청크를 반환합니다.
    컬렉션이 존재하지 않으면 빈 리스트를 반환합니다.
    Returns: [{"text": 청크텍스트, "metadata": 메타데이터, "distance": 코사인거리}, ...] 리스트.
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


def delete_chunks(source: str, collection_name: str = "documents") -> int:
    """
    지정된 source(파일명)에 해당하는 모든 청크를 ChromaDB 컬렉션에서 삭제.

    메타데이터의 source 필드가 일치하는 모든 임베딩을 제거합니다.
    문서 재업로드 시 기존 요약 교체, 파일 삭제 시 고아 임베딩 정리에 사용됩니다.

    Args:
        source: 삭제할 문서의 source 메타데이터 값 (파일명).
        collection_name: 대상 ChromaDB 컬렉션명. 기본값 "documents".

    Returns:
        삭제된 청크 수. 컬렉션이 없거나 해당 source가 없으면 0.
    """
    client = get_chroma_client()

    try:
        collection = client.get_collection(name=collection_name)
    except Exception:
        logger.warning(f"Collection '{collection_name}' not found for deletion")
        return 0

    existing = collection.get(where={"source": source})
    count = len(existing["ids"]) if existing and existing["ids"] else 0

    if count > 0:
        collection.delete(where={"source": source})
        logger.info(f"Deleted {count} chunks for source '{source}' from '{collection_name}'")

    return count


def check_chroma_connection() -> bool:
    """
    ChromaDB 서버에 heartbeat 요청을 보내 연결 상태를 확인.

    앱 기동 시 ChromaDB가 정상적으로 응답하는지 검증하는 용도로 사용됩니다.
    Returns: 연결 성공 시 True, 실패 시 False.
    """
    try:
        client = get_chroma_client()
        client.heartbeat()
        return True
    except Exception as e:
        logger.error(f"ChromaDB connection failed: {e}")
        return False
