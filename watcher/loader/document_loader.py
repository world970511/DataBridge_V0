"""
비정형 문서(PDF, DOCX, TXT 등) 처리 파이프라인 모듈.

Tier 1 (업로드 시):
    문서에서 텍스트를 추출한 뒤 TF-IDF 추출적 요약을 생성하여
    ChromaDB의 'documents' 컬렉션에 임베딩합니다 (LLM 호출 없음, 수 밀리초).
    동시에 원문 텍스트를 청크로 분할하여 PostgreSQL document_chunks 테이블에 캐시합니다.

Tier 2 (질의 시):
    doc_agent가 PostgreSQL에서 캐시된 청크를 로드하고 TF-IDF로 질의 관련
    청크를 선별하여 LLM에 전달합니다 (파일 재파싱 불필요).

Lazy Loading: 파일 크기가 MAX_EMBED_SIZE_MB를 초과하면 임베딩을 건너뛰고
카탈로그에만 등록합니다 (chunk_count=0). 질의 시 온디맨드로 파싱+캐싱합니다.

처리 결과는 카탈로그와 처리 이력 로그에 기록됩니다.
"""

import logging
import os
from pathlib import Path

from catalog.catalog import register_document
from config.settings import get_settings
from rag.parser import EncryptedFileError
from watcher.loader._utils import log_file_process

# 원문 청크 캐시용 청크 설정 (요약 청크보다 크게 설정하여 문맥 보존)
_CACHE_CHUNK_SIZE = 1000
_CACHE_CHUNK_OVERLAP = 100

logger = logging.getLogger(__name__)

COLLECTION_NAME = "documents"


def _get_file_size_mb(file_path: str) -> float:
    """
    파일 크기를 MB 단위로 반환.

    Args:
        file_path: 파일 절대 경로.

    Returns: 파일 크기 (MB). 파일이 없으면 0.0.
    """
    try:
        return os.path.getsize(file_path) / (1024 * 1024)
    except OSError:
        return 0.0


def load_document(file_path: str, file_type: str):
    """
    문서 파일의 Tier 1 처리 파이프라인 (요약 임베딩 + 원문 청크 캐시).

    처리 흐름:
    0) 파일 크기 확인 → MAX_EMBED_SIZE_MB 초과 시 Lazy Loading 모드
    1) 파일 유형에 맞는 파서로 텍스트 추출
    2) TF-IDF 추출적 요약 생성 (LLM 없음, 수 밀리초)
    3) 요약 청크를 ChromaDB에 저장 (파일 식별용)
    4) 원문 청크를 PostgreSQL document_chunks에 캐시 (질의 시 재파싱 불필요)
    5) 카탈로그에 문서 메타데이터 등록
    """
    path = Path(file_path)
    settings = get_settings()
    max_size_mb = settings.document.max_embed_size_mb

    try:
        # 0. 파일 크기 확인 → Lazy Loading 여부 결정
        file_size_mb = _get_file_size_mb(file_path)

        if file_size_mb > max_size_mb:
            # Lazy Loading: 최소 임베딩만 저장 (검색 가능하도록)
            # 원문 청크 캐시는 첫 질의 시 온디맨드로 생성됨
            logger.info(
                f"Lazy loading: {path.name} ({file_size_mb:.1f}MB > {max_size_mb}MB threshold)"
            )

            lazy_summary = (
                f"문서 파일: {path.name}\n"
                f"파일 유형: {file_type}\n"
                f"파일 크기: {file_size_mb:.1f}MB\n"
                f"상태: 대용량 파일 - 질의 시 전체 내용 파싱"
            )

            from rag.chunker import chunk_text
            from rag.embedder import delete_chunks, store_chunks

            delete_chunks(source=path.name, collection_name=COLLECTION_NAME)
            lazy_chunks = chunk_text(lazy_summary, source=path.name)
            store_chunks(lazy_chunks, collection_name=COLLECTION_NAME)

            register_document(
                doc_name=path.name,
                source_file=str(file_path),
                file_type=file_type,
                chunk_count=0,
                collection_name=COLLECTION_NAME,
                summary_text=lazy_summary,
            )
            log_file_process(
                file_path, file_type, "register_for_search", None, "success",
                f"Lazy loaded ({file_size_mb:.1f}MB)"
            )
            return

        # 1. 텍스트 추출
        text = _extract_text(file_path, file_type)
        if not text or not text.strip():
            logger.warning(f"No text extracted from: {file_path}")
            log_file_process(file_path, file_type, "register_for_search", None, "failed", "No text extracted")
            return

        # 2. TF-IDF 추출적 요약 생성 (LLM 호출 없음)
        from rag.summarizer import generate_summary
        summary = generate_summary(text, source=path.name)

        if not summary:
            logger.warning(f"No summary generated from: {file_path}")
            log_file_process(file_path, file_type, "register_for_search", None, "failed", "No summary")
            return

        # 3. 요약 텍스트 청킹 → ChromaDB (파일 식별용, 소량)
        from rag.chunker import chunk_text
        summary_chunks = chunk_text(summary, source=path.name)

        if not summary_chunks:
            logger.warning(f"No summary chunks from: {file_path}")
            log_file_process(file_path, file_type, "register_for_search", None, "failed", "No chunks")
            return

        # 4. 원문 텍스트 청킹 → PostgreSQL 캐시 (질의 시 재파싱 불필요)
        full_text_chunks = chunk_text(
            text, source=path.name,
            chunk_size=_CACHE_CHUNK_SIZE,
            chunk_overlap=_CACHE_CHUNK_OVERLAP,
        )

        # 5. ChromaDB에 요약 임베딩 저장 (재업로드 대응: 기존 삭제 후 삽입)
        from rag.embedder import delete_chunks, store_chunks
        delete_chunks(source=path.name, collection_name=COLLECTION_NAME)
        store_chunks(summary_chunks, collection_name=COLLECTION_NAME)

        # 6. 카탈로그 등록 (RETURNING id로 FK 연결)
        from catalog.catalog import register_document_returning_id, replace_document_chunks
        doc_id = register_document_returning_id(
            doc_name=path.name,
            source_file=str(file_path),
            file_type=file_type,
            chunk_count=len(full_text_chunks),
            collection_name=COLLECTION_NAME,
            summary_text=summary,
        )

        # 7. 원문 청크를 PostgreSQL에 캐시 (재업로드 대응: 기존 삭제 후 삽입)
        if doc_id and full_text_chunks:
            replace_document_chunks(doc_id, full_text_chunks)

        log_file_process(file_path, file_type, "register_for_search", None, "success")
        logger.info(
            f"Document loaded: {path.name} "
            f"({len(summary_chunks)} summary chunks, {len(full_text_chunks)} cached chunks)"
        )

    except EncryptedFileError:
        logger.warning(f"Encrypted file detected: {file_path}")
        register_document(
            doc_name=path.name,
            source_file=str(file_path),
            file_type=file_type,
            chunk_count=0,
            collection_name=COLLECTION_NAME,
            summary_text=f"암호화된 파일: {path.name} (비밀번호 필요)",
            status="encrypted",
        )
        log_file_process(
            file_path, file_type, "register_for_search", None, "failed",
            "Encrypted file - password required"
        )

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

    pdf → pdf_parser, doc → doc_parser (OLE2), docx → docx_parser,
    ppt → ppt_parser (OLE2), pptx → pptx_parser,
    hwp → hwp_parser/hwpx_parser, text → UTF-8로 직접 읽기 (단, CSV 구조 감지 시 CSV 로더로 라우팅).
    지원하지 않는 유형은 빈 문자열을 반환합니다.
    Returns: 추출된 텍스트 문자열 (실패 또는 미지원 유형은 빈 문자열).
             CSV 구조 감지 시 CSV 로더로 라우팅하고 빈 문자열을 반환.
    """
    if file_type == "pdf":
        from rag.parser.pdf_parser import parse_pdf
        return parse_pdf(file_path)
    elif file_type == "doc":
        from rag.parser.doc_parser import parse_doc
        return parse_doc(file_path)
    elif file_type == "docx":
        from rag.parser.docx_parser import parse_docx
        return parse_docx(file_path)
    elif file_type == "ppt":
        from rag.parser.ppt_parser import parse_ppt
        return parse_ppt(file_path)
    elif file_type == "pptx":
        from rag.parser.pptx_parser import parse_pptx
        return parse_pptx(file_path)
    elif file_type == "hwp":
        suffix = Path(file_path).suffix.lower()
        if suffix == ".hwpx":
            from rag.parser.hwpx_parser import parse_hwpx
            return parse_hwpx(file_path)
        else:
            from rag.parser.hwp_parser import parse_hwp
            return parse_hwp(file_path)
    elif file_type == "text":
        # CSV 구조 감지: 텍스트 파일이 실제로는 CSV 데이터일 수 있음
        if _looks_like_csv(file_path):
            logger.info(
                f"Text file detected as CSV structure, routing to CSV loader: {file_path}"
            )
            _redirect_to_csv_loader(file_path)
            return ""  # 문서 파이프라인 중단 (CSV 로더가 처리)
        return Path(file_path).read_text(encoding="utf-8", errors="replace")
    else:
        logger.warning(f"Unsupported document type for extraction: {file_type}")
        return ""


def _looks_like_csv(file_path: str, sample_lines: int = 10) -> bool:
    """
    텍스트 파일 앞부분을 읽어 CSV/TSV 구조인지 휴리스틱 판별.

    판별 기준:
    1) 비어 있지 않은 줄이 최소 2줄 이상 (헤더 + 데이터 1행)
    2) 일관된 구분자(쉼표 또는 탭)가 존재
    3) 헤더를 포함한 과반수 줄의 필드 개수가 동일 (최소 2컬럼)

    Args:
        file_path: 텍스트 파일 절대 경로.
        sample_lines: 판별에 사용할 최대 줄 수 (기본 10).

    Returns:
        True이면 CSV/TSV 구조로 간주.
    """
    import csv
    import io

    try:
        raw = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    lines = [ln for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False

    sample = lines[:sample_lines]

    # csv.Sniffer로 구분자 감지 시도
    try:
        dialect = csv.Sniffer().sniff("\n".join(sample), delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        # Sniffer 실패 시 쉼표/탭 빈도로 판단
        comma_count = sum(ln.count(",") for ln in sample)
        tab_count = sum(ln.count("\t") for ln in sample)
        if comma_count == 0 and tab_count == 0:
            return False
        delimiter = "\t" if tab_count > comma_count else ","

    # 각 줄의 필드 개수 계산
    field_counts = []
    for ln in sample:
        reader = csv.reader(io.StringIO(ln), delimiter=delimiter)
        fields = next(reader, [])
        field_counts.append(len(fields))

    if not field_counts:
        return False

    # 최소 2컬럼 이상이어야 CSV로 간주
    header_fields = field_counts[0]
    if header_fields < 2:
        return False

    # 과반수 줄이 헤더와 동일한 필드 수를 가져야 함
    matching = sum(1 for c in field_counts if c == header_fields)
    return matching / len(field_counts) >= 0.5


def _redirect_to_csv_loader(file_path: str):
    """
    CSV 구조로 감지된 텍스트 파일을 스마트 분류 파이프라인으로 라우팅.

    classifier._route_to_db_loader()를 호출하여 내용 분석 기반의
    스마트 라우팅(DB or ChromaDB)을 수행합니다.

    Args:
        file_path: 원본 텍스트 파일 경로.
    """
    from watcher.classifier import _route_to_db_loader

    logger.info(f"Redirecting text file to CSV pipeline: {file_path}")
    _route_to_db_loader(file_path, file_type="csv")


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


# ============================================
# 스마트 분류기: DataFrame → 문서 변환
# ============================================

def load_document_from_dataframe(
    file_path: str,
    file_type: str,
    df,  # pd.DataFrame — 지연 임포트 대신 타입 생략
    data_category: str = "document",
):
    """
    문서형으로 판별된 CSV/Excel DataFrame을 ChromaDB에 저장.

    스마트 분류기(classifier.py)가 내용 분석 후 문서형으로 판별한
    CSV/Excel 파일을 기존 문서 파이프라인(요약→청킹→임베딩→카탈로그)으로 처리합니다.
    DataFrame의 각 행을 "컬럼명: 값" 형식의 텍스트 문단으로 변환합니다.

    Args:
        file_path: 원본 파일 경로 (카탈로그 등록용).
        file_type: 파일 유형 ('csv' 또는 'excel').
        df: 변환 대상 DataFrame.
        data_category: 데이터 카테고리 ('document' 또는 'reference').
    """
    import pandas as pd

    path = Path(file_path)

    try:
        # DataFrame → 텍스트 변환
        text = _dataframe_to_text(df, path.name)

        if not text or not text.strip():
            logger.warning(f"No text converted from DataFrame: {file_path}")
            log_file_process(
                file_path, file_type, "register_for_search", None, "failed",
                "Empty text from DataFrame"
            )
            return

        # TF-IDF 추출적 요약 생성 (LLM 호출 없음)
        from rag.summarizer import generate_summary
        summary = generate_summary(text, source=path.name)

        if not summary:
            summary = text[:1500]

        # 요약 청킹 → ChromaDB
        from rag.chunker import chunk_text
        from rag.embedder import delete_chunks, store_chunks

        summary_chunks = chunk_text(summary, source=path.name)

        delete_chunks(source=path.name, collection_name=COLLECTION_NAME)
        if summary_chunks:
            store_chunks(summary_chunks, collection_name=COLLECTION_NAME)

        # 원문 청킹 → PostgreSQL 캐시
        full_text_chunks = chunk_text(
            text, source=path.name,
            chunk_size=_CACHE_CHUNK_SIZE,
            chunk_overlap=_CACHE_CHUNK_OVERLAP,
        )

        # 카탈로그 등록 (RETURNING id)
        from catalog.catalog import register_document_returning_id, replace_document_chunks
        doc_id = register_document_returning_id(
            doc_name=path.name,
            source_file=str(file_path),
            file_type=file_type,
            chunk_count=len(full_text_chunks),
            collection_name=COLLECTION_NAME,
            summary_text=summary,
        )

        # 원문 청크 캐시 저장
        if doc_id and full_text_chunks:
            replace_document_chunks(doc_id, full_text_chunks)

        log_file_process(
            file_path, file_type, "register_for_search", None, "success",
            f"Smart classified as {data_category}"
        )
        logger.info(
            f"Spreadsheet loaded as document: {path.name} "
            f"({len(summary_chunks)} summary, {len(full_text_chunks)} cached chunks, "
            f"category={data_category})"
        )

    except Exception as e:
        logger.exception(f"Failed to load spreadsheet as document: {file_path}")
        log_file_process(
            file_path, file_type, "register_for_search", None, "failed", str(e)
        )


def _dataframe_to_text(df, source_name: str) -> str:
    """
    DataFrame의 각 행을 "컬럼명: 값" 형식의 텍스트 문단으로 변환.

    문서형 스프레드시트(테스트 케이스, 체크리스트, 회의록 등)를
    시맨틱 검색에 적합한 자연어 텍스트로 변환합니다.

    Args:
        df: 변환 대상 DataFrame.
        source_name: 문서 출처 파일명.

    Returns:
        변환된 전체 텍스트 문자열.

    변환 형식 예시::

        문서 출처: 테스트케이스_v3.xlsx

        [항목 1]
        테스트명: 로그인 테스트
        설명: 사용자가 올바른 비밀번호로 로그인할 수 있는지 확인
        예상결과: 로그인 성공

        [항목 2]
        ...
    """
    import pandas as pd

    lines = [f"문서 출처: {source_name}\n"]

    for idx, row in df.iterrows():
        # 행 번호는 1부터 시작 (사용자 친화적)
        display_idx = idx + 1 if isinstance(idx, int) else idx
        lines.append(f"[항목 {display_idx}]")
        for col in df.columns:
            val = row[col]
            if pd.notna(val):
                val_str = str(val)
                # 너무 긴 값은 잘라냄 (임베딩 효율)
                if len(val_str) > 500:
                    val_str = val_str[:497] + "..."
                lines.append(f"{col}: {val_str}")
        lines.append("")  # 빈 줄로 항목 구분

    return "\n".join(lines)
