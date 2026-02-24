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
    elif action == "register_image":
        _cleanup_image(file_path)
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
            from notifications.dispatcher import emit_event
            emit_event("file.deleted", {"type": "table", "tables": cleaned_tables, "file": path.name})

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
        # (document_chunks도 ON DELETE CASCADE로 자동 삭제됨)
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
            from notifications.dispatcher import emit_event
            emit_event("file.deleted", {"type": "document", "doc": path.name, "chunks_removed": deleted_count})
        else:
            logger.info(f"No document data found for deleted file: {path.name}")

    except Exception as e:
        logger.exception(f"Failed to clean up document: {path.name}")
        log_file_process(file_path, file_type, "delete", None, "failed", str(e))


def _cleanup_image(file_path: str):
    """
    이미지 삭제 시 ChromaDB 임베딩, 카탈로그, 썸네일을 정리.

    watchdog에 의한 파일시스템 직접 삭제 시 호출됩니다 (승인 없이 즉시 실행).
    """
    from catalog.catalog import remove_image
    from rag.image.image_store import delete_image_embedding
    from watcher.classifier import get_file_type

    file_type = get_file_type(file_path)
    path = Path(file_path)

    try:
        # ChromaDB에서 임베딩 삭제
        deleted_count = delete_image_embedding(path.name)

        # 카탈로그에서 이미지 메타데이터 삭제 (썸네일 경로 포함)
        image_info = remove_image(source_file=str(file_path))

        # 썸네일 파일 삭제
        if image_info and image_info.get("thumbnail_path"):
            thumb_path = Path(image_info["thumbnail_path"])
            if thumb_path.exists():
                thumb_path.unlink()

        if image_info or deleted_count > 0:
            logger.info(
                f"Cleaned up image '{path.name}': "
                f"{deleted_count} embedding(s) removed from ChromaDB"
            )
            log_file_process(
                file_path, file_type, "delete", None, "success",
                f"Removed {deleted_count} embedding(s)",
            )
            from notifications.dispatcher import emit_event
            emit_event("file.deleted", {"type": "image", "image": path.name})
        else:
            logger.info(f"No image data found for deleted file: {path.name}")

    except Exception as e:
        logger.exception(f"Failed to clean up image: {path.name}")
        log_file_process(file_path, file_type, "delete", None, "failed", str(e))
