"""
파일 삭제 시 관련 리소스(DB 테이블, ChromaDB 임베딩, 카탈로그)를 정리하는 모듈.

파일이 공유 폴더에서 삭제되면 해당 파일로 생성된 모든 리소스를 정리합니다:
- 정형 데이터(CSV/Excel): PostgreSQL 데이터 테이블 DROP + 카탈로그 삭제
- 비정형 문서(PDF/DOCX/TXT): ChromaDB 임베딩 삭제 + 카탈로그 삭제
- 파일 처리 이력에 삭제 기록

의존 모듈:
    - catalog.catalog: get_tables_by_source(), remove_table(), remove_document()
    - rag.embedder: delete_chunks()
    - db.connection: get_cursor()
    - watcher.classifier: get_file_type()
    - watcher.loader._utils: log_file_process()
"""

import logging
from pathlib import Path

from watcher.loader._utils import log_file_process

logger = logging.getLogger(__name__)


def cleanup_file(file_path: str, action: str):
    """
    삭제된 파일의 유형에 따라 적절한 정리 함수를 호출.

    Args:
        file_path: 삭제된 파일의 경로.
        action: classifier가 결정한 액션 ('load_to_db' 또는 'register_for_search').
    """
    if action == "load_to_db":
        _cleanup_structured_data(file_path)
    elif action == "register_for_search":
        _cleanup_document(file_path)
    else:
        logger.debug(f"No cleanup needed for action: {action}")


def _cleanup_structured_data(file_path: str):
    """
    정형 데이터(CSV/Excel) 파일 삭제 시 PostgreSQL 테이블과 카탈로그를 정리.

    1. catalog_tables에서 source_file로 관련 테이블 조회
    2. 각 테이블에 대해 DROP TABLE 실행
    3. catalog_tables에서 메타데이터 삭제
    4. file_process_log에 삭제 기록
    """
    from catalog.catalog import get_tables_by_source, remove_table
    from db.connection import get_cursor
    from watcher.classifier import get_file_type

    file_type = get_file_type(file_path)
    path = Path(file_path)

    try:
        tables = get_tables_by_source(str(file_path))
        if not tables:
            logger.info(f"No tables found for deleted file: {path.name}")
            return

        cleaned_tables = []
        for table_info in tables:
            table_name = table_info["table_name"]
            try:
                # 실제 PostgreSQL 테이블 DROP
                with get_cursor() as cur:
                    cur.execute(f'DROP TABLE IF EXISTS "{table_name}"')

                # 카탈로그에서 메타데이터 삭제
                remove_table(table_name)
                cleaned_tables.append(table_name)
                logger.info(f"Dropped table '{table_name}' for deleted file: {path.name}")

            except Exception as e:
                logger.exception(f"Failed to clean up table '{table_name}'")
                log_file_process(
                    file_path, file_type, "delete", table_name, "failed", str(e),
                )

        if cleaned_tables:
            log_file_process(
                file_path, file_type, "delete",
                ",".join(cleaned_tables), "success",
            )

    except Exception as e:
        logger.exception(f"Failed to clean up structured data for: {path.name}")
        log_file_process(file_path, file_type, "delete", None, "failed", str(e))


def _cleanup_document(file_path: str):
    """
    비정형 문서(PDF/DOCX/TXT) 삭제 시 ChromaDB 임베딩과 카탈로그를 정리.

    1. ChromaDB에서 해당 source의 모든 청크(요약 임베딩) 삭제
    2. catalog_documents에서 메타데이터 삭제
    3. file_process_log에 삭제 기록
    """
    from catalog.catalog import remove_document
    from rag.embedder import delete_chunks
    from watcher.classifier import get_file_type

    file_type = get_file_type(file_path)
    path = Path(file_path)

    try:
        # ChromaDB에서 해당 문서의 임베딩 삭제 (source metadata = 파일명)
        deleted_count = delete_chunks(source=path.name, collection_name="documents")

        # 카탈로그에서 문서 메타데이터 삭제
        doc_info = remove_document(source_file=str(file_path))

        if doc_info or deleted_count > 0:
            logger.info(
                f"Cleaned up document '{path.name}': "
                f"{deleted_count} chunks removed from ChromaDB"
            )
            log_file_process(
                file_path, file_type, "delete", None, "success",
                f"Removed {deleted_count} chunks",
            )
        else:
            logger.info(f"No document data found for deleted file: {path.name}")

    except Exception as e:
        logger.exception(f"Failed to clean up document: {path.name}")
        log_file_process(file_path, file_type, "delete", None, "failed", str(e))
