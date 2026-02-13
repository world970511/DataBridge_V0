"""
pypdf 라이브러리를 사용한 PDF 문서 텍스트 추출 모듈.

PDF 파일의 각 페이지에서 텍스트를 추출하여 전체 문자열로 결합하거나,
페이지별 텍스트와 페이지 번호를 딕셔너리 리스트로 반환하는 두 가지 방식을 제공합니다.
빈 페이지는 자동으로 건너뜁니다.
"""

import logging
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)


def parse_pdf(file_path: str) -> str:
    """
    PDF의 모든 페이지에서 텍스트를 추출하여 하나의 문자열로 결합.

    pypdf의 PdfReader로 각 페이지의 extract_text()를 호출하고,
    빈 페이지는 건너뛴 뒤 페이지 간 빈 줄(\\n\\n)로 연결합니다.
    파일이 존재하지 않으면 FileNotFoundError를 발생시킵니다.
    Returns: 페이지별 텍스트를 빈 줄로 연결한 전체 텍스트 문자열.
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
    PDF의 각 페이지 텍스트를 페이지 번호와 함께 개별적으로 추출.

    빈 페이지는 결과에서 제외되며, 페이지 번호는 1부터 시작합니다.
    Returns: [{"page": 페이지번호(int), "text": 해당페이지텍스트(str)}, ...] 리스트.
    """
    reader = PdfReader(file_path)
    result = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            result.append({"page": i + 1, "text": text.strip()})
    return result
