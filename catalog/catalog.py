"""
데이터 카탈로그 관리 모듈 — 테이블과 문서의 메타데이터를 자동으로 관리.

적재된 테이블의 이름, 원본 파일, 컬럼 정보, 행 수 등을 catalog_tables 테이블에,
문서의 이름, 청크 수, 컬렉션명 등을 catalog_documents 테이블에 등록합니다.
AI 에이전트가 SQL을 생성할 때 이 카탈로그를 참조하여
어떤 테이블/문서가 존재하는지 파악합니다.
"""

import json
import logging
from typing import Optional

from db.connection import get_cursor, execute_query

logger = logging.getLogger(__name__)


# ============================================
# 테이블 카탈로그
# ============================================

def register_table(
    table_name: str,
    source_file: str,
    file_type: str,
    row_count: int,
    column_count: int,
    columns_json: list[dict],
    # Rich Catalog 신규 필드 (기본값 None으로 하위 호환성 유지)
    description: Optional[str] = None,
    data_category: Optional[str] = None,
    tags: Optional[list] = None,
    column_descriptions: Optional[dict] = None,
    sample_values: Optional[dict] = None,
    numeric_ratio: Optional[float] = None,
    avg_text_length: Optional[float] = None,
):
    """
    테이블 메타데이터를 catalog_tables에 UPSERT(INSERT 또는 UPDATE).

    동일한 table_name이 이미 존재하면 source_file, row_count 등을 갱신하고
    updated_at을 현재 시각으로 업데이트합니다. columns_json은 컬럼명과 dtype을
    담은 딕셔너리 리스트를 JSON 문자열로 변환하여 저장합니다.

    Rich Catalog 필드(description, tags 등)가 None이면 기존 값을 유지합니다
    (COALESCE 패턴). 이를 통해 기존 코드에서 새 필드 없이 호출해도 정상 동작합니다.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO catalog_tables
                (table_name, source_file, file_type, row_count, column_count,
                 columns_json, description, data_category, tags,
                 column_descriptions, sample_values, numeric_ratio, avg_text_length)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (table_name) DO UPDATE SET
                source_file = EXCLUDED.source_file,
                file_type = EXCLUDED.file_type,
                row_count = EXCLUDED.row_count,
                column_count = EXCLUDED.column_count,
                columns_json = EXCLUDED.columns_json,
                description = COALESCE(EXCLUDED.description, catalog_tables.description),
                data_category = COALESCE(EXCLUDED.data_category, catalog_tables.data_category),
                tags = COALESCE(EXCLUDED.tags, catalog_tables.tags),
                column_descriptions = COALESCE(EXCLUDED.column_descriptions, catalog_tables.column_descriptions),
                sample_values = COALESCE(EXCLUDED.sample_values, catalog_tables.sample_values),
                numeric_ratio = COALESCE(EXCLUDED.numeric_ratio, catalog_tables.numeric_ratio),
                avg_text_length = COALESCE(EXCLUDED.avg_text_length, catalog_tables.avg_text_length),
                updated_at = NOW()
            """,
            (
                table_name,
                source_file,
                file_type,
                row_count,
                column_count,
                json.dumps(columns_json, ensure_ascii=False),
                description,
                data_category,
                tags,  # psycopg2가 Python list → PostgreSQL TEXT[] 자동 변환
                json.dumps(column_descriptions, ensure_ascii=False) if column_descriptions else None,
                json.dumps(sample_values, ensure_ascii=False) if sample_values else None,
                numeric_ratio,
                avg_text_length,
            ),
        )
    logger.info(f"Table registered in catalog: {table_name} (category={data_category})")


def list_tables() -> list[dict]:
    """
    catalog_tables에 등록된 모든 테이블의 메타데이터를 updated_at 내림차순으로 조회.

    Rich Catalog 필드(description, data_category, tags, column_descriptions,
    sample_values, numeric_ratio, avg_text_length)도 함께 반환합니다.

    Returns: 테이블명, 원본 파일, 행/컬럼 수, Rich Catalog 필드 등을 담은 딕셔너리 리스트.
    """
    return execute_query(
        """
        SELECT table_name, source_file, file_type, row_count, column_count,
               columns_json, description, data_category, tags,
               column_descriptions, sample_values, numeric_ratio, avg_text_length,
               created_at, updated_at
        FROM catalog_tables
        ORDER BY updated_at DESC
        """
    )


def get_table_info(table_name: str) -> Optional[dict]:
    """
    특정 테이블명으로 catalog_tables에서 메타데이터를 조회.

    Returns: 해당 테이블의 모든 컬럼 정보를 담은 딕셔너리, 없으면 None.
    """
    rows = execute_query(
        "SELECT * FROM catalog_tables WHERE table_name = %s",
        (table_name,),
    )
    return rows[0] if rows else None


def remove_table(table_name: str):
    """
    catalog_tables에서 지정된 테이블명의 메타데이터 레코드를 삭제.

    실제 PostgreSQL 테이블은 삭제하지 않으며, 카탈로그 레코드만 제거합니다.
    """
    with get_cursor() as cur:
        cur.execute("DELETE FROM catalog_tables WHERE table_name = %s", (table_name,))
    logger.info(f"Table removed from catalog: {table_name}")


# ============================================
# 문서 카탈로그
# ============================================

def register_document(
    doc_name: str,
    source_file: str,
    file_type: str,
    chunk_count: int,
    collection_name: str,
    summary_text: Optional[str] = None,
):
    """
    문서 메타데이터를 catalog_documents에 UPSERT(INSERT 또는 UPDATE).

    동일한 source_file이 이미 존재하면 메타데이터를 갱신하고
    updated_at을 현재 시각으로 업데이트합니다. 재업로드 시 요약 정보도 갱신됩니다.
    """
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO catalog_documents
                (doc_name, source_file, file_type, chunk_count, collection_name, summary_text)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_file) DO UPDATE SET
                doc_name = EXCLUDED.doc_name,
                file_type = EXCLUDED.file_type,
                chunk_count = EXCLUDED.chunk_count,
                collection_name = EXCLUDED.collection_name,
                summary_text = EXCLUDED.summary_text,
                updated_at = NOW()
            """,
            (doc_name, source_file, file_type, chunk_count, collection_name, summary_text),
        )
    logger.info(f"Document registered in catalog: {doc_name}")


def list_documents() -> list[dict]:
    """
    catalog_documents에 등록된 모든 문서의 메타데이터를 created_at 내림차순으로 조회.

    Returns: 문서명, 원본 파일, 청크 수, 컬렉션명 등을 담은 딕셔너리 리스트.
    """
    return execute_query(
        """
        SELECT doc_name, source_file, file_type, chunk_count,
               collection_name, created_at
        FROM catalog_documents
        ORDER BY created_at DESC
        """
    )


def get_document_by_name(doc_name: str) -> Optional[dict]:
    """
    doc_name(파일명)으로 catalog_documents에서 문서 메타데이터를 조회.

    doc_agent의 Tier 2 온디맨드 파싱에서 source_file 경로를 얻기 위해 사용합니다.

    Returns: 문서 메타데이터 딕셔너리 (source_file 포함), 없으면 None.
    """
    rows = execute_query(
        "SELECT * FROM catalog_documents WHERE doc_name = %s LIMIT 1",
        (doc_name,),
    )
    return rows[0] if rows else None


def remove_document(source_file: str) -> Optional[dict]:
    """
    catalog_documents에서 source_file로 문서를 조회한 뒤 삭제.

    삭제 전에 문서 정보를 조회하여 반환합니다 (로깅, ChromaDB 정리 등에 활용).

    Returns: 삭제된 문서의 메타데이터 딕셔너리, 없으면 None.
    """
    rows = execute_query(
        "SELECT * FROM catalog_documents WHERE source_file = %s",
        (source_file,),
    )
    doc_info = rows[0] if rows else None

    if doc_info:
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM catalog_documents WHERE source_file = %s",
                (source_file,),
            )
        logger.info(f"Document removed from catalog: {doc_info.get('doc_name', source_file)}")

    return doc_info


def get_tables_by_source(source_file: str) -> list[dict]:
    """
    source_file 경로로 catalog_tables에서 관련 테이블들을 조회.

    하나의 Excel 파일이 여러 시트를 별도 테이블로 생성할 수 있으므로 리스트로 반환합니다.

    Returns: 관련 테이블 메타데이터 딕셔너리 리스트.
    """
    return execute_query(
        "SELECT * FROM catalog_tables WHERE source_file = %s",
        (source_file,),
    )


def get_catalog_summary() -> dict:
    """
    카탈로그에 등록된 테이블 수와 문서 수를 집계하여 요약 정보를 반환.

    Returns: {"total_tables": 테이블수(int), "total_documents": 문서수(int)} 딕셔너리.
    """
    tables = execute_query("SELECT COUNT(*) as cnt FROM catalog_tables")
    docs = execute_query("SELECT COUNT(*) as cnt FROM catalog_documents")
    return {
        "total_tables": tables[0]["cnt"] if tables else 0,
        "total_documents": docs[0]["cnt"] if docs else 0,
    }


def get_table_tags() -> dict:
    """
    모든 테이블의 태그 매핑을 반환.

    오케스트레이터의 의도 분류 시 사용자 질의에 포함된 태그를 매칭하여
    데이터 조회 의도를 강화하는 데 사용됩니다.

    Returns: {"table_name": ["tag1", "tag2", ...], ...} 형태의 딕셔너리.
             태그가 없는 테이블은 제외됩니다.
    """
    rows = execute_query(
        "SELECT table_name, tags FROM catalog_tables WHERE tags IS NOT NULL"
    )
    return {r["table_name"]: r["tags"] or [] for r in rows}
