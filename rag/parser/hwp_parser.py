"""
HWP 문서 텍스트 추출 모듈.

한글 구버전(.hwp) 파일은 OLE2 Compound File 포맷입니다.
olefile로 스트림을 열고, BodyText/Section* 스트림을 zlib 해제 후
바이너리 레코드에서 텍스트(HWPTAG_PARA_TEXT)를 추출합니다.

참고: https://www.hancom.com/etc/hwpDownload.do (한글 문서 파일 구조)
"""

import logging
import struct
import zlib
from pathlib import Path

import olefile

from rag.parser import EncryptedFileError

logger = logging.getLogger(__name__)

# HWP 레코드 태그: HWPTAG_PARA_TEXT = 67 (0x0042 + 기본 태그 오프셋)
HWPTAG_BEGIN = 0x0010
HWPTAG_PARA_TEXT = HWPTAG_BEGIN + 51  # = 67


def _get_body_sections(ole: olefile.OleFileIO) -> list[str]:
    """BodyText 디렉토리 내 Section 스트림 이름 목록을 정렬하여 반환."""
    sections = []
    for entry in ole.listdir():
        if len(entry) >= 2 and entry[0] == "BodyText":
            sections.append("/".join(entry))
    sections.sort()
    return sections


def _read_header_props(ole: olefile.OleFileIO) -> int:
    """FileHeader에서 properties DWORD를 읽어 반환."""
    try:
        header = ole.openstream("FileHeader")
        data = header.read()
        if len(data) >= 40:
            return struct.unpack_from("<I", data, 36)[0]
    except Exception:
        pass
    return 0x01  # 기본: 압축됨


def _is_compressed(ole: olefile.OleFileIO) -> bool:
    """FileHeader에서 압축 여부 플래그를 확인 (bit 0)."""
    return bool(_read_header_props(ole) & 0x01)


def _is_encrypted(ole: olefile.OleFileIO) -> bool:
    """FileHeader에서 암호화 여부 플래그를 확인 (bit 1)."""
    return bool(_read_header_props(ole) & 0x02)


def _decompress_stream(raw: bytes, compressed: bool) -> bytes:
    """스트림 데이터를 zlib 해제 (비압축이면 그대로 반환)."""
    if not compressed:
        return raw
    try:
        return zlib.decompress(raw, -15)
    except zlib.error:
        # wbits 변경 재시도
        return zlib.decompress(raw)


def _extract_text_from_records(data: bytes) -> list[str]:
    """
    바이너리 레코드 스트림에서 HWPTAG_PARA_TEXT 레코드의 텍스트를 추출.

    HWP 레코드 헤더: 4바이트 (tag_id:10 | level:10 | size:12)
    size가 0xFFF이면 추가 4바이트에 실제 크기가 있음.
    텍스트는 UTF-16LE로 인코딩되어 있으며, 제어 문자를 필터링합니다.
    """
    texts = []
    offset = 0
    length = len(data)

    while offset < length - 4:
        # 레코드 헤더 파싱
        header = struct.unpack_from("<I", data, offset)[0]
        tag_id = header & 0x3FF
        # level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        offset += 4

        if size == 0xFFF:
            if offset + 4 > length:
                break
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4

        if offset + size > length:
            break

        if tag_id == HWPTAG_PARA_TEXT:
            record_data = data[offset: offset + size]
            text = _decode_para_text(record_data)
            if text.strip():
                texts.append(text.strip())

        offset += size

    return texts


def _decode_para_text(data: bytes) -> str:
    """
    HWPTAG_PARA_TEXT 레코드 데이터를 UTF-16LE 디코딩.

    HWP 특수 제어 문자(0x0000~0x001F 범위 중 일부)를 필터링합니다.
    제어 문자 중 일부는 인라인 확장 데이터(12바이트 또는 4바이트)를 포함합니다.
    """
    chars = []
    i = 0
    length = len(data)

    while i < length - 1:
        code = struct.unpack_from("<H", data, i)[0]
        i += 2

        if code == 0:
            # NULL 종료
            break
        elif code < 0x0020:
            # HWP 제어 문자 처리
            if code in (1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23):
                # 인라인 확장: 추가 12바이트 (6 chars) 건너뜀
                i += 12
            elif code == 10:
                # 줄바꿈
                chars.append("\n")
            elif code == 13:
                # 문단 끝
                chars.append("\n")
            elif code == 24:
                # 탭
                chars.append("\t")
            elif code == 4:
                # 필드 시작 (확장 바이트 건너뜀)
                i += 12
            # 그 외 제어 문자는 무시
        else:
            chars.append(chr(code))

    return "".join(chars)


def parse_hwp(file_path: str) -> str:
    """
    HWP 파일의 BodyText에서 텍스트를 추출하여 하나의 문자열로 결합.

    OLE2 Compound File을 열어 BodyText/Section* 스트림을 순회하며
    zlib 해제 후 바이너리 레코드에서 텍스트를 추출합니다.

    Args:
        file_path: HWP 파일 절대 경로.

    Returns:
        섹션별 텍스트를 빈 줄로 연결한 전체 텍스트 문자열.

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"HWP not found: {file_path}")

    try:
        ole = olefile.OleFileIO(file_path)

        # 암호화 감지
        if _is_encrypted(ole):
            ole.close()
            raise EncryptedFileError(file_path, "hwp")

        compressed = _is_compressed(ole)
        sections = _get_body_sections(ole)

        if not sections:
            logger.warning(f"No BodyText sections found: {path.name}")
            ole.close()
            return ""

        all_texts = []
        for section_name in sections:
            raw = ole.openstream(section_name).read()
            decompressed = _decompress_stream(raw, compressed)
            texts = _extract_text_from_records(decompressed)
            all_texts.extend(texts)

        ole.close()

        full_text = "\n\n".join(all_texts)
        logger.info(
            f"HWP parsed: {path.name} ({len(sections)} sections, {len(full_text)} chars)"
        )
        return full_text

    except Exception:
        logger.exception(f"Failed to parse HWP: {file_path}")
        raise
