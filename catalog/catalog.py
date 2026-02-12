"""
데이터 카탈로그 — 테이블/문서 메타데이터 자동 관리.
에이전트가 테이블 목록을 알아야 SQL을 생성할 수 있음.
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
):
    """테이블 메타데이터를 카탈로그에 등록 (UPSERT)."""
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO catalog_tables
                (table_name, source_file, file_type, row_count, column_count, columns_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (table_name) DO UPDATE SET
                source_file = EXCLUDED.source_file,
                file_type = EXCLUDED.file_type,
                row_count = EXCLUDED.row_count,
                column_count = EXCLUDED.column_count,
                columns_json = EXCLUDED.columns_json,
                updated_at = NOW()
            """,
            (
                table_name,
                source_file,
                file_type,
                row_count,
                column_count,
                json.dumps(columns_json, ensure_ascii=False),
            ),
        )
    logger.info(f"Table registered in catalog: {table_name}")


def list_tables() -> list[dict]:
    """등록된 모든 테이블 메타데이터 반환."""
    return execute_query(
        """
        SELECT table_name, source_file, file_type, row_count, column_count,
               columns_json, created_at, updated_at
        FROM catalog_tables
        ORDER BY updated_at DESC
        """
    )


def get_table_info(table_name: str) -> Optional[dict]:
    """특정 테이블의 메타데이터 반환."""
    rows = execute_query(
        "SELECT * FROM catalog_tables WHERE table_name = %s",
        (table_name,),
    )
    return rows[0] if rows else None


def remove_table(table_name: str):
    """카탈로그에서 테이블 메타데이터 삭제."""
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
):
    """문서 메타데이터를 카탈로그에 등록."""
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO catalog_documents
                (doc_name, source_file, file_type, chunk_count, collection_name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (doc_name, source_file, file_type, chunk_count, collection_name),
        )
    logger.info(f"Document registered in catalog: {doc_name}")


def list_documents() -> list[dict]:
    """등록된 모든 문서 메타데이터 반환."""
    return execute_query(
        """
        SELECT doc_name, source_file, file_type, chunk_count,
               collection_name, created_at
        FROM catalog_documents
        ORDER BY created_at DESC
        """
    )


def get_catalog_summary() -> dict:
    """카탈로그 요약 정보."""
    tables = execute_query("SELECT COUNT(*) as cnt FROM catalog_tables")
    docs = execute_query("SELECT COUNT(*) as cnt FROM catalog_documents")
    return {
        "total_tables": tables[0]["cnt"] if tables else 0,
        "total_documents": docs[0]["cnt"] if docs else 0,
    }
