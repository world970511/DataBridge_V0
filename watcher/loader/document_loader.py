"""
비정형 문서(PDF, DOCX, TXT 등) 처리 파이프라인 모듈.

Tier 1 (업로드 시): 문서에서 텍스트를 추출한 뒤 경량 요약만 생성하여
ChromaDB의 'documents' 컬렉션에 임베딩합니다. 전체 텍스트는 임베딩하지 않습니다.

Tier 2 (질의 시): doc_agent가 extract_text()를 호출하여 원본 파일을 온디맨드로
파싱하고, 전체 텍스트를 LLM에 직접 전달합니다.

처리 결과는 카탈로그와 처리 이력 로그에 기록됩니다.
"""

import logging
from pathlib import Path

from catalog.catalog import register_document
from watcher.loader._utils import log_file_process

logger = logging.getLogger(__name__)

COLLECTION_NAME = "documents"


def load_document(file_path: str, file_type: str):
    """
    문서 파일의 Tier 1 처리 파이프라인 (요약만 임베딩).

    처리 흐름: 1) 파일 유형에 맞는 파서로 텍스트 추출 →
    2) generate_summary()로 경량 요약 생성 →
    3) chunk_text()로 요약 텍스트를 청크로 분할 →
    4) 기존 임베딩 삭제 후 요약 청크만 ChromaDB에 저장 →
    5) 카탈로그에 문서 메타데이터(요약 포함) 등록.
    텍스트 추출 실패나 빈 청크 등 각 단계에서 실패 시 이력을 남기고 종료합니다.
    """
    path = Path(file_path)

    try:
        # 1. 텍스트 추출
        text = _extract_text(file_path, file_type)
        if not text or not text.strip():
            logger.warning(f"No text extracted from: {file_path}")
            log_file_process(file_path, file_type, "register_for_search", None, "failed", "No text extracted")
            return

        # 2. 요약 생성
        from rag.summarizer import generate_summary
        summary = generate_summary(text, source=path.name)

        if not summary:
            logger.warning(f"No summary generated from: {file_path}")
            log_file_process(file_path, file_type, "register_for_search", None, "failed", "No summary")
            return

        # 3. 요약 텍스트 청킹
        from rag.chunker import chunk_text
        chunks = chunk_text(summary, source=path.name)

        if not chunks:
            logger.warning(f"No chunks generated from: {file_path}")
            log_file_process(file_path, file_type, "register_for_search", None, "failed", "No chunks")
            return

        # 4. 기존 임베딩 삭제 후 새 요약 청크 저장 (재업로드 대응)
        from rag.embedder import delete_chunks, store_chunks
        delete_chunks(source=path.name, collection_name=COLLECTION_NAME)
        store_chunks(chunks, collection_name=COLLECTION_NAME)

        # 5. 카탈로그 등록 (요약 텍스트 포함)
        register_document(
            doc_name=path.name,
            source_file=str(file_path),
            file_type=file_type,
            chunk_count=len(chunks),
            collection_name=COLLECTION_NAME,
            summary_text=summary,
        )

        log_file_process(file_path, file_type, "register_for_search", None, "success")
        logger.info(f"Document loaded (summary): {path.name} ({len(chunks)} summary chunks)")

    except Exception as e:
        logger.exception(f"Failed to load document: {file_path}")
        log_file_process(file_path, file_type, "register_for_search", None, "failed", str(e))


def extract_text(file_path: str, file_type: str) -> str:
    """
    파일에서 텍스트를 추출하는 공개 인터페이스 (Tier 2 온디맨드 파싱용).

    doc_agent가 질의 시 원본 파일의 전체 텍스트를 추출할 때 호출합니다.
    내부적으로 _extract_text()를 호출합니다.

    Args:
        file_path: 문서 파일 절대 경로.
        file_type: 파일 유형 ('pdf', 'docx', 'text').

    Returns: 추출된 텍스트 문자열. 실패 시 빈 문자열.
    """
    return _extract_text(file_path, file_type)


def _extract_text(file_path: str, file_type: str) -> str:
    """
    파일 유형에 따라 적절한 파서를 선택하여 텍스트를 추출.

    pdf → pdf_parser.parse_pdf(), docx → python-docx 기반 추출,
    text → UTF-8로 직접 읽기. 지원하지 않는 유형은 빈 문자열을 반환합니다.
    Returns: 추출된 텍스트 문자열 (실패 또는 미지원 유형은 빈 문자열).
    """
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
    """
    python-docx를 사용하여 DOCX 파일에서 문단별 텍스트를 추출.

    빈 문단은 건너뛰고, 각 문단을 줄바꿈으로 연결하여 반환합니다.
    python-docx가 설치되지 않은 경우 빈 문자열을 반환합니다.
    Returns: DOCX 내의 전체 텍스트 문자열.
    """
    try:
        from docx import Document
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except ImportError:
        logger.error("python-docx not installed. Cannot parse DOCX files.")
        return ""
