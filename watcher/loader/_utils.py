"""로더 공통 유틸리티."""

import logging
import re
from pathlib import Path

import pandas as pd
import numpy as np

from db.connection import get_cursor

logger = logging.getLogger(__name__)


def sanitize_table_name(name: str) -> str:
    """
    파일명/컬럼명을 안전한 SQL 식별자로 변환.
    - 한글, 영문, 숫자, 밑줄만 허용
    - 숫자로 시작하면 앞에 _ 추가
    """
    # 공백 → 밑줄
    name = name.strip().replace(" ", "_").replace("-", "_")
    # 안전한 문자만 남김 (한글 포함)
    name = re.sub(r"[^\w가-힣]", "_", name)
    # 연속 밑줄 정리
    name = re.sub(r"_+", "_", name).strip("_")
    # 숫자로 시작하면 접두사 추가
    if name and name[0].isdigit():
        name = f"t_{name}"
    # 빈 문자열 방지
    if not name:
        name = "unnamed"
    return name.lower()


def df_to_pg_types(df: pd.DataFrame) -> list[tuple[str, str]]:
    """DataFrame 컬럼 → PostgreSQL 타입 매핑."""
    result = []
    for col in df.columns:
        dtype = df[col].dtype
        if pd.api.types.is_integer_dtype(dtype):
            pg_type = "BIGINT"
        elif pd.api.types.is_float_dtype(dtype):
            pg_type = "DOUBLE PRECISION"
        elif pd.api.types.is_bool_dtype(dtype):
            pg_type = "BOOLEAN"
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            pg_type = "TIMESTAMPTZ"
        else:
            # 문자열의 최대 길이를 확인하여 TEXT vs VARCHAR 결정
            max_len = df[col].astype(str).str.len().max()
            if max_len and max_len > 500:
                pg_type = "TEXT"
            else:
                pg_type = "TEXT"
        result.append((col, pg_type))
    return result


def log_file_process(
    file_path: str,
    file_type: str,
    action: str,
    target_table: str | None = None,
    status: str = "success",
    error_message: str | None = None,
):
    """파일 처리 이력을 DB에 기록."""
    try:
        path = Path(file_path)
        file_size = path.stat().st_size if path.exists() else 0

        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO file_process_log
                    (file_path, file_name, file_type, file_size, action, target_table, status, error_message)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(file_path),
                    path.name,
                    file_type,
                    file_size,
                    action,
                    target_table,
                    status,
                    error_message,
                ),
            )
    except Exception:
        logger.exception("Failed to log file process")
