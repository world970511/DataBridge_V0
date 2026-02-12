"""
텍스트 분할 (Chunking).
긴 문서를 검색에 적합한 크기로 나눔.
"""

import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict


def chunk_text(
    text: str,
    source: str = "",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[Chunk]:
    """
    텍스트를 고정 크기 청크로 분할.
    - 문단 경계 우선, 불가능하면 문장 경계, 최후에 문자 단위.
    """
    if not text or not text.strip():
        return []

    # 문단 단위로 먼저 분리
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    chunks: list[Chunk] = []
    current_text = ""
    chunk_index = 0

    for para in paragraphs:
        # 현재 청크에 문단 추가 가능한지 확인
        if len(current_text) + len(para) + 1 <= chunk_size:
            current_text = f"{current_text}\n{para}" if current_text else para
        else:
            # 현재 청크 저장
            if current_text:
                chunks.append(_make_chunk(current_text, source, chunk_index))
                chunk_index += 1

            # 문단이 chunk_size보다 길면 강제 분할
            if len(para) > chunk_size:
                sub_chunks = _split_long_text(para, chunk_size, chunk_overlap)
                for sc in sub_chunks:
                    chunks.append(_make_chunk(sc, source, chunk_index))
                    chunk_index += 1
                current_text = ""
            else:
                # 오버랩: 이전 청크 마지막 부분을 가져옴
                if current_text and chunk_overlap > 0:
                    overlap = current_text[-chunk_overlap:]
                    current_text = f"{overlap}\n{para}"
                else:
                    current_text = para

    # 마지막 청크
    if current_text.strip():
        chunks.append(_make_chunk(current_text, source, chunk_index))

    logger.info(f"Chunked '{source}': {len(chunks)} chunks")
    return chunks


def _make_chunk(text: str, source: str, index: int) -> Chunk:
    return Chunk(
        id=f"{source}_{index}_{uuid.uuid4().hex[:8]}",
        text=text.strip(),
        metadata={"source": source, "chunk_index": index},
    )


def _split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """긴 텍스트를 문장 경계에서 분할 시도, 실패 시 문자 단위."""
    # 문장 경계 분할 시도
    sentences = _split_sentences(text)
    if len(sentences) > 1:
        result = []
        current = ""
        for sent in sentences:
            if len(current) + len(sent) + 1 <= chunk_size:
                current = f"{current} {sent}" if current else sent
            else:
                if current:
                    result.append(current)
                current = sent
        if current:
            result.append(current)
        return result

    # 문자 단위 분할
    result = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        result.append(text[start:end])
        start = end - overlap if overlap > 0 else end
    return result


def _split_sentences(text: str) -> list[str]:
    """간단한 문장 분리."""
    import re
    sentences = re.split(r'(?<=[.!?。])\s+', text)
    return [s.strip() for s in sentences if s.strip()]
