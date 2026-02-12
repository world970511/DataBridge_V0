"""
비정형 문서 (PDF, DOCX 등) → 텍스트 추출 → 청킹 → ChromaDB 저장.
"""

import logging
from pathlib import Path

from catalog.catalog import register_document
from watcher.loader._utils import log_file_process

logger = logging.getLogger(__name__)

COLLECTION_NAME = "documents"


def load_document(file_path: str, file_type: str):
    """
    문서 파일을 파싱 → 청킹 → 임베딩 → ChromaDB에 저장.
    """
    path = Path(file_path)

    try:
        # 1. 텍스트 추출
        text = _extract_text(file_path, file_type)
        if not text or not text.strip():
            logger.warning(f"No text extracted from: {file_path}")
            log_file_process(file_path, file_type, "register_for_search", None, "failed", "No text extracted")
            return

        # 2. 청킹
        from rag.chunker import chunk_text
        chunks = chunk_text(text, source=path.name)

        if not chunks:
            logger.warning(f"No chunks generated from: {file_path}")
            log_file_process(file_path, file_type, "register_for_search", None, "failed", "No chunks")
            return

        # 3. ChromaDB에 저장
        from rag.embedder import store_chunks
        store_chunks(chunks, collection_name=COLLECTION_NAME)

        # 4. 카탈로그 등록
        register_document(
            doc_name=path.name,
            source_file=str(file_path),
            file_type=file_type,
            chunk_count=len(chunks),
            collection_name=COLLECTION_NAME,
        )

        log_file_process(file_path, file_type, "register_for_search", None, "success")
        logger.info(f"Document loaded: {path.name} ({len(chunks)} chunks)")

    except Exception as e:
        logger.exception(f"Failed to load document: {file_path}")
        log_file_process(file_path, file_type, "register_for_search", None, "failed", str(e))


def _extract_text(file_path: str, file_type: str) -> str:
    """파일 유형에 따라 텍스트 추출."""
    if file_type == "pdf":
        from rag.parser.pdf_parser import parse_pdf
        return parse_pdf(file_path)
    elif file_type == "docx":
        return _extract_docx(file_path)
    elif file_type == "text":
        return Path(file_path).read_text(encoding="utf-8", errors="replace")
    else:
        logger.warning(f"Unsupported document type for extraction: {file_type}")
        return ""


def _extract_docx(file_path: str) -> str:
    """DOCX 파일 텍스트 추출 (간이 구현)."""
    try:
        from docx import Document
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        logger.error("python-docx not installed. Cannot parse DOCX files.")
        return ""
