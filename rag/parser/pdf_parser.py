"""
PDF 문서 텍스트 추출.
"""

import logging
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def parse_pdf(file_path: str) -> str:
    """
    PDF에서 텍스트를 추출.
    Returns: 전체 텍스트 문자열.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")

    try:
        reader = PdfReader(file_path)
        pages = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text and text.strip():
                pages.append(text.strip())

        full_text = "\n\n".join(pages)
        logger.info(f"PDF parsed: {path.name} ({len(reader.pages)} pages, {len(full_text)} chars)")
        return full_text

    except Exception:
        logger.exception(f"Failed to parse PDF: {file_path}")
        raise


def parse_pdf_by_pages(file_path: str) -> list[dict]:
    """
    페이지별로 텍스트를 추출.
    Returns: [{"page": 1, "text": "..."}, ...]
    """
    reader = PdfReader(file_path)
    result = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            result.append({"page": i + 1, "text": text.strip()})
    return result
