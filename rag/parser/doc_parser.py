"""
Word 97-2003 (.doc) 문서 텍스트 추출 모듈.

.doc 파일은 OLE2 Compound File(MS-CFB) 포맷입니다.
WordDocument 스트림의 FIB(File Information Block)에서 텍스트 범위를 읽고,
clx(piece table)를 파싱하여 본문 텍스트를 추출합니다.

제한사항:
- 본문 텍스트만 추출 (머리글/바닥글/각주/텍스트박스 제외)
- 암호화된 .doc 파일은 미지원
"""

import logging
import struct
from pathlib import Path

import olefile

from rag.parser import EncryptedFileError

logger = logging.getLogger(__name__)


def _read_fib(word_stream: bytes) -> dict:
    """
    FIB(File Information Block)에서 텍스트 추출에 필요한 정보를 읽음.

    Returns:
        dict with keys: encrypted, table_name, ccpText, fcClx, lcbClx
    """
    # FIB base (offset 0): wIdent(2) + nFib(2) + ... + flags(2) at offset 10
    if len(word_stream) < 100:
        raise ValueError("WordDocument stream too short for FIB")

    # offset 10: bit flags — bit 8 = fEncrypted
    flags = struct.unpack_from("<H", word_stream, 10)[0]
    encrypted = bool(flags & 0x0100)

    # offset 10: bit 9 = fWhichTblStm (0 = "0Table", 1 = "1Table")
    use_1table = bool(flags & 0x0200)
    table_name = "1Table" if use_1table else "0Table"

    # FIB RgLw (starts at offset 32 for Word 97+)
    # offset 76 (= 32 + 11*4): ccpText — character count of main text
    ccpText = struct.unpack_from("<I", word_stream, 76)[0]

    # FIB RgFcLcb — offset varies by nFib version
    # For Word 97 (nFib=193): fcClx at offset 418, lcbClx at offset 422
    # For Word 2000+ (nFib=217+): same offsets
    fcClx = struct.unpack_from("<I", word_stream, 418)[0]
    lcbClx = struct.unpack_from("<I", word_stream, 422)[0]

    return {
        "encrypted": encrypted,
        "table_name": table_name,
        "ccpText": ccpText,
        "fcClx": fcClx,
        "lcbClx": lcbClx,
    }


def _parse_piece_table(clx_data: bytes) -> list[dict]:
    """
    CLX(Complex) 데이터에서 Piece Table을 파싱하여 텍스트 조각 정보를 반환.

    CLX는 여러 Prc(formatting) 블록과 마지막 Pcdt(piece table) 블록으로 구성.
    Pcdt 시작: 0x02 바이트, 이후 4바이트 크기, 그 뒤 CP 배열 + PCD 배열.

    Returns:
        list of {"cp_start": int, "cp_end": int, "fc": int, "is_unicode": bool}
    """
    offset = 0
    length = len(clx_data)

    # Prc 블록 건너뛰기 (type == 0x01)
    while offset < length:
        clxt = clx_data[offset]
        if clxt == 0x02:
            # Pcdt 찾음
            break
        elif clxt == 0x01:
            # Prc: skip
            offset += 1
            if offset + 2 > length:
                break
            cb = struct.unpack_from("<H", clx_data, offset)[0]
            offset += 2 + cb
        else:
            break

    if offset >= length or clx_data[offset] != 0x02:
        raise ValueError("Pcdt not found in CLX data")

    offset += 1  # skip 0x02
    pcdt_size = struct.unpack_from("<I", clx_data, offset)[0]
    offset += 4

    pcdt_data = clx_data[offset: offset + pcdt_size]

    # Piece Table: (n+1) CPs (4 bytes each) + n PCDs (8 bytes each)
    # pcdt_size = (n+1)*4 + n*8 = 4 + n*12
    # n = (pcdt_size - 4) / 12
    n = (pcdt_size - 4) // 12
    if n <= 0:
        return []

    # CP 배열 읽기
    cps = []
    for i in range(n + 1):
        cp = struct.unpack_from("<I", pcdt_data, i * 4)[0]
        cps.append(cp)

    # PCD 배열 읽기 (CP 배열 뒤)
    pcd_offset = (n + 1) * 4
    pieces = []
    for i in range(n):
        # PCD: 2 bytes (padding) + 4 bytes (fc) + 2 bytes (prm)
        base = pcd_offset + i * 8
        fc_raw = struct.unpack_from("<I", pcdt_data, base + 2)[0]

        # fc의 bit 30이 1이면 ANSI(cp1252), 0이면 Unicode
        is_unicode = not bool(fc_raw & 0x40000000)
        # 실제 파일 오프셋: bit 30 클리어
        fc = fc_raw & ~0x40000000
        if not is_unicode:
            fc = fc >> 1  # ANSI의 경우 바이트 오프셋 보정

        pieces.append({
            "cp_start": cps[i],
            "cp_end": cps[i + 1],
            "fc": fc,
            "is_unicode": is_unicode,
        })

    return pieces


def _extract_text_from_pieces(
    word_stream: bytes, pieces: list[dict], ccp_text: int
) -> str:
    """
    Piece Table 정보를 사용하여 WordDocument 스트림에서 텍스트를 추출.

    Args:
        word_stream: WordDocument 스트림 전체 바이트.
        pieces: _parse_piece_table() 결과.
        ccp_text: 본문 텍스트의 문자 수.
    """
    text_parts = []
    chars_read = 0

    for piece in pieces:
        if chars_read >= ccp_text:
            break

        cp_start = piece["cp_start"]
        cp_end = piece["cp_end"]
        fc = piece["fc"]
        is_unicode = piece["is_unicode"]

        # 본문 텍스트 범위만 추출
        effective_start = max(cp_start, 0)
        effective_end = min(cp_end, ccp_text)
        if effective_start >= effective_end:
            continue

        char_count = effective_end - effective_start
        # piece 내 오프셋 보정
        offset_in_piece = effective_start - cp_start

        if is_unicode:
            byte_offset = fc + offset_in_piece * 2
            byte_length = char_count * 2
            raw = word_stream[byte_offset: byte_offset + byte_length]
            text = raw.decode("utf-16-le", errors="replace")
        else:
            byte_offset = fc + offset_in_piece
            byte_length = char_count
            raw = word_stream[byte_offset: byte_offset + byte_length]
            text = raw.decode("cp1252", errors="replace")

        text_parts.append(text)
        chars_read += char_count

    return "".join(text_parts)


def _clean_text(raw_text: str) -> str:
    """추출된 텍스트에서 Word 특수 문자를 정리."""
    result = []
    for ch in raw_text:
        code = ord(ch)
        if code == 13:
            result.append("\n")
        elif code == 7:
            # 셀/행 끝 마커 → 탭으로 변환
            result.append("\t")
        elif code == 12:
            # 페이지 브레이크
            result.append("\n")
        elif code == 11:
            # 줄 바꿈(soft return)
            result.append("\n")
        elif code < 32 and code not in (9, 10):
            # 기타 제어 문자 제거 (탭, 줄바꿈 제외)
            continue
        else:
            result.append(ch)

    return "".join(result)


def parse_doc(file_path: str) -> str:
    """
    Word 97-2003 (.doc) 파일에서 본문 텍스트를 추출하여 하나의 문자열로 반환.

    OLE2 Compound File을 열어 WordDocument 스트림의 FIB에서 Piece Table 위치를
    확인하고, Table 스트림의 CLX 데이터를 파싱하여 텍스트를 추출합니다.

    Args:
        file_path: .doc 파일 절대 경로.

    Returns:
        추출된 전체 텍스트 문자열.

    Raises:
        FileNotFoundError: 파일이 존재하지 않을 때.
        ValueError: 암호화된 파일이거나 구조 파싱 실패 시.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"DOC not found: {file_path}")

    try:
        ole = olefile.OleFileIO(file_path)

        # WordDocument 스트림 읽기
        if not ole.exists("WordDocument"):
            ole.close()
            raise ValueError(f"WordDocument stream not found: {file_path}")

        word_stream = ole.openstream("WordDocument").read()
        fib = _read_fib(word_stream)

        if fib["encrypted"]:
            ole.close()
            raise EncryptedFileError(file_path, "doc")

        if fib["ccpText"] == 0:
            ole.close()
            logger.warning(f"Empty document: {path.name}")
            return ""

        # Table 스트림에서 CLX(Piece Table) 읽기
        table_name = fib["table_name"]
        if not ole.exists(table_name):
            ole.close()
            raise ValueError(f"{table_name} stream not found: {file_path}")

        table_stream = ole.openstream(table_name).read()
        fc_clx = fib["fcClx"]
        lcb_clx = fib["lcbClx"]

        if fc_clx + lcb_clx > len(table_stream):
            ole.close()
            raise ValueError(f"CLX data out of bounds: {file_path}")

        clx_data = table_stream[fc_clx: fc_clx + lcb_clx]
        pieces = _parse_piece_table(clx_data)

        if not pieces:
            ole.close()
            logger.warning(f"No piece table entries found: {path.name}")
            return ""

        # 텍스트 추출
        raw_text = _extract_text_from_pieces(word_stream, pieces, fib["ccpText"])
        ole.close()

        full_text = _clean_text(raw_text)
        # 연속 빈 줄 정리
        lines = [line for line in full_text.splitlines() if line.strip()]
        full_text = "\n".join(lines)

        logger.info(
            f"DOC parsed: {path.name} ({fib['ccpText']} chars raw, {len(full_text)} chars clean)"
        )
        return full_text

    except (EncryptedFileError, ValueError, FileNotFoundError):
        raise
    except Exception:
        logger.exception(f"Failed to parse DOC: {file_path}")
        raise
