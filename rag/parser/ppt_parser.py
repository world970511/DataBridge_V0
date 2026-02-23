"""
PowerPoint 97-2003 (.ppt) 문서 텍스트 추출 모듈.

.ppt 파일은 OLE2 Compound File(MS-CFB) 포맷입니다.
'PowerPoint Document' 스트림의 바이너리 레코드를 순회하며
TextCharsAtom(Unicode) 및 TextBytesAtom(Latin/CP1252) 레코드에서
텍스트를 추출합니다.

제한사항:
- 본문/제목 텍스트만 추출 (노트, 마스터 슬라이드 제외)
- 암호화된 .ppt 파일은 미지원
"""

import logging
import struct
from pathlib import Path

import olefile

from rag.parser import EncryptedFileError

logger = logging.getLogger(__name__)

# PowerPoint 레코드 타입
RT_TEXT_CHARS_ATOM = 4000   # Unicode (UTF-16LE) 텍스트
RT_TEXT_BYTES_ATOM = 4008   # Latin (CP1252/CP949) 텍스트


def _extract_records(data: bytes) -> list[str]:
    """
    PowerPoint Document 스트림에서 텍스트 레코드를 순회하며 텍스트 추출.

    PowerPoint 레코드 헤더: 8바이트
    - recVer(4bit) + recInstance(12bit) = 2bytes
    - recType = 2bytes
    - recLen = 4bytes

    TextCharsAtom(4000): UTF-16LE 인코딩 텍스트
    TextBytesAtom(4008): 1바이트 인코딩 텍스트 (CP1252 또는 시스템 코드 페이지)
    """
    texts = []
    offset = 0
    length = len(data)

    while offset + 8 <= length:
        # 레코드 헤더 파싱
        rec_ver_inst = struct.unpack_from("<H", data, offset)[0]
        rec_type = struct.unpack_from("<H", data, offset + 2)[0]
        rec_len = struct.unpack_from("<I", data, offset + 4)[0]
        offset += 8

        # 컨테이너 레코드(recVer == 0xF)는 자식 레코드를 포함하므로
        # 바디를 건너뛰지 않고 내부를 순회
        rec_ver = rec_ver_inst & 0x0F
        if rec_ver == 0x0F:
            # 컨테이너: 자식 레코드 순회를 위해 offset 그대로 유지
            continue

        if offset + rec_len > length:
            break

        if rec_type == RT_TEXT_CHARS_ATOM:
            # Unicode (UTF-16LE)
            raw = data[offset: offset + rec_len]
            text = raw.decode("utf-16-le", errors="replace").strip()
            if text:
                texts.append(text)

        elif rec_type == RT_TEXT_BYTES_ATOM:
            # 1바이트 인코딩 — CP949(한국어) 우선 시도, 실패 시 CP1252
            raw = data[offset: offset + rec_len]
            try:
                text = raw.decode("cp949", errors="strict").strip()
            except (UnicodeDecodeError, ValueError):
                text = raw.decode("cp1252", errors="replace").strip()
            if text:
                texts.append(text)

        offset += rec_len

    return texts


def _clean_text(raw_text: str) -> str:
    """추출된 텍스트에서 제어 문자를 정리."""
    result = []
    for ch in raw_text:
        code = ord(ch)
        if code == 13:
            result.append("\n")
        elif code == 11:
            # 줄 바꿈(vertical tab → soft return)
            result.append("\n")
        elif code < 32 and code not in (9, 10):
            continue
        else:
            result.append(ch)
    return "".join(result)


def parse_ppt(file_path: str) -> str:
    """
    PowerPoint 97-2003 (.ppt) 파일에서 텍스트를 추출하여 하나의 문자열로 반환.

    OLE2 Compound File을 열어 'PowerPoint Document' 스트림의 바이너리 레코드를
    순회하며 TextCharsAtom/TextBytesAtom에서 텍스트를 추출합니다.

    Args:
        file_path: .ppt 파일 절대 경로.

    Returns:
        추출된 전체 텍스트 문자열.

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때.
        ValueError: PowerPoint Document 스트림이 없을 때.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PPT not found: {file_path}")

    try:
        ole = olefile.OleFileIO(file_path)

        # 암호화 감지: EncryptedPackage가 있으면 MS-OFFCRYPTO 암호화
        if ole.exists("EncryptedPackage"):
            ole.close()
            raise EncryptedFileError(file_path, "ppt")

        if not ole.exists("PowerPoint Document"):
            ole.close()
            raise ValueError(f"PowerPoint Document stream not found: {file_path}")

        ppt_stream = ole.openstream("PowerPoint Document").read()
        ole.close()

        texts = _extract_records(ppt_stream)

        if not texts:
            logger.warning(f"No text extracted from PPT: {path.name}")
            return ""

        # 각 텍스트 블록을 정리 후 결합
        cleaned = []
        for t in texts:
            c = _clean_text(t)
            if c.strip():
                cleaned.append(c.strip())

        full_text = "\n\n".join(cleaned)
        logger.info(
            f"PPT parsed: {path.name} ({len(cleaned)} text blocks, {len(full_text)} chars)"
        )
        return full_text

    except (EncryptedFileError, ValueError, FileNotFoundError):
        raise
    except Exception:
        logger.exception(f"Failed to parse PPT: {file_path}")
        raise
