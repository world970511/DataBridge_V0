"""
텍스트 분할(Chunking) 모듈.

긴 문서 텍스트를 벡터 검색에 적합한 크기(기본 500자)의 청크로 분할합니다.
분할 전략은 문단 경계 → 문장 경계 → 문자 단위 순서로 우선 적용하며,
인접 청크 간 50자의 오버랩을 두어 문맥 연속성을 유지합니다.
각 청크는 고유 ID, 텍스트, 출처·인덱스 메타데이터를 담은 Chunk 데이터클래스로 반환됩니다.
"""

import logging
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 50


@dataclass
class Chunk:
    """
    텍스트 청크를 표현하는 데이터클래스.

    id: '{출처}_{인덱스}_{uuid8자}' 형태의 고유 식별자.
    text: 청크의 실제 텍스트 내용.
    metadata: 출처(source)와 청크 순서(chunk_index)를 담는 딕셔너리.
    """
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
    텍스트를 chunk_size(기본 500자) 이하의 청크들로 분할.

    분할 전략: 줄바꿈 기준으로 문단을 분리한 뒤 chunk_size 이내에서 최대한 문단을 모읍니다.
    문단이 chunk_size를 초과하면 문장 경계, 그래도 안 되면 문자 단위로 강제 분할합니다.
    인접 청크 간 chunk_overlap(기본 50자)만큼 텍스트가 겹쳐 문맥 연속성을 유지합니다.
    Returns: Chunk 데이터클래스 인스턴스들의 리스트 (빈 텍스트 입력 시 빈 리스트).
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
    """
    텍스트, 출처, 인덱스로 Chunk 객체를 생성.

    ID는 '{source}_{index}_{uuid4 앞 8자}' 형태로 생성되며,
    metadata에 source와 chunk_index를 포함합니다.
    Returns: 생성된 Chunk 인스턴스.
    """
    return Chunk(
        id=f"{source}_{index}_{uuid.uuid4().hex[:8]}",
        text=text.strip(),
        metadata={"source": source, "chunk_index": index},
    )


def _split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    chunk_size를 초과하는 긴 텍스트를 더 작은 조각으로 분할.

    먼저 문장 경계(.!?。 뒤 공백)에서 분할을 시도하고,
    문장이 1개뿐이면 overlap을 적용하여 문자 단위로 강제 분할합니다.
    Returns: 분할된 텍스트 조각들의 리스트.
    """
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
    """
    정규식을 사용하여 텍스트를 문장 단위로 분리.

    마침표(.), 느낌표(!), 물음표(?), 한국어/일본어 마침표(。) 뒤의
    공백을 기준으로 분할하며, 빈 문장은 제거합니다.
    Returns: 문장 문자열들의 리스트.
    """
    import re
    sentences = re.split(r'(?<=[.!?。])\s+', text)
    return [s.strip() for s in sentences if s.strip()]
