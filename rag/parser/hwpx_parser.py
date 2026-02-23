"""
HWPX 문서 텍스트 추출 모듈.

한글 신버전(.hwpx)은 OOXML 기반 ZIP 컨테이너입니다.
Contents/section*.xml 파일 내 <hp:t> 태그에서 텍스트를 추출합니다.

참고: https://www.hancom.com/etc/hwpDownload.do (HWPX 문서 파일 구조)
"""

import logging
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from rag.parser import EncryptedFileError

logger = logging.getLogger(__name__)


def _is_encrypted_ooxml(file_path: str) -> bool:
    """HWPX 파일이 암호화 상태인지 확인 (OLE2 + EncryptedPackage)."""
    try:
        import olefile
        if olefile.isOleFile(file_path):
            ole = olefile.OleFileIO(file_path)
            encrypted = ole.exists("EncryptedPackage")
            ole.close()
            return encrypted
    except Exception:
        pass
    return False

# HWPX XML 네임스페이스
HWPX_NAMESPACES = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
}


def _find_section_files(zf: zipfile.ZipFile) -> list[str]:
    """ZIP 내 Contents/section*.xml 파일 목록을 정렬하여 반환."""
    sections = []
    for name in zf.namelist():
        lower = name.lower()
        if lower.startswith("contents/section") and lower.endswith(".xml"):
            sections.append(name)
    sections.sort()
    return sections


def _extract_text_from_xml(xml_bytes: bytes) -> list[str]:
    """
    섹션 XML에서 텍스트를 추출.

    <hp:t> 태그의 텍스트를 수집합니다.
    네임스페이스 매칭 실패 시 로컬 이름 기반 폴백을 시도합니다.
    """
    texts = []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        logger.warning("Failed to parse HWPX section XML")
        return texts

    # 네임스페이스 기반 검색
    for t_elem in root.iter("{http://www.hancom.co.kr/hwpml/2011/paragraph}t"):
        if t_elem.text and t_elem.text.strip():
            texts.append(t_elem.text.strip())

    # 네임스페이스가 다른 경우 폴백: 로컬 이름으로 검색
    if not texts:
        for elem in root.iter():
            local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local_name == "t" and elem.text and elem.text.strip():
                texts.append(elem.text.strip())

    return texts


def parse_hwpx(file_path: str) -> str:
    """
    HWPX 파일의 모든 섹션에서 텍스트를 추출하여 하나의 문자열로 결합.

    ZIP을 열어 Contents/section*.xml을 순회하며
    각 섹션의 <hp:t> 태그 텍스트를 추출합니다.

    Args:
        file_path: HWPX 파일 절대 경로.

    Returns:
        섹션별 텍스트를 빈 줄로 연결한 전체 텍스트 문자열.

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"HWPX not found: {file_path}")

    # 암호화 감지
    if _is_encrypted_ooxml(file_path):
        raise EncryptedFileError(file_path, "hwpx")

    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            sections = _find_section_files(zf)

            if not sections:
                logger.warning(f"No section XML found in HWPX: {path.name}")
                return ""

            all_texts = []
            for section_name in sections:
                xml_bytes = zf.read(section_name)
                texts = _extract_text_from_xml(xml_bytes)
                all_texts.extend(texts)

        full_text = "\n\n".join(all_texts)
        logger.info(
            f"HWPX parsed: {path.name} ({len(sections)} sections, {len(full_text)} chars)"
        )
        return full_text

    except zipfile.BadZipFile:
        logger.error(f"Invalid HWPX (not a valid ZIP): {file_path}")
        raise
    except Exception:
        logger.exception(f"Failed to parse HWPX: {file_path}")
        raise
