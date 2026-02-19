"""
CSV/Excel/Document 로더에서 공통으로 사용하는 유틸리티 모듈.

테이블명·컬럼명을 SQL 안전한 식별자로 변환하는 sanitize_table_name(),
pandas DataFrame의 dtype을 PostgreSQL 타입으로 매핑하는 df_to_pg_types(),
파일 처리 이력을 file_process_log 테이블에 기록하는 log_file_process()를 제공합니다.
"""

import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

from db.connection import get_cursor

logger = logging.getLogger(__name__)


def sanitize_table_name(name: str) -> str:
    """
    파일명이나 컬럼명을 PostgreSQL에서 안전하게 사용할 수 있는 SQL 식별자로 변환.

    변환 규칙: 공백·하이픈을 밑줄로 치환 → 한글·영문·숫자·밑줄 외 문자 제거 →
    연속 밑줄 정리 → 숫자로 시작하면 't_' 접두사 추가 → 소문자로 변환.
    빈 문자열이 되면 'unnamed'을 반환합니다.
    Returns: SQL 안전한 소문자 식별자 문자열.
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
    """
    pandas DataFrame의 각 컬럼 dtype을 PostgreSQL 타입 문자열로 매핑.

    매핑 규칙: 정수→BIGINT, 실수→DOUBLE PRECISION, 불리언→BOOLEAN,
    datetime→TIMESTAMPTZ, 그 외(문자열 등)→TEXT.
    Returns: [(컬럼명, PostgreSQL타입), ...] 형태의 튜플 리스트.
    """
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
    target_table: Optional[str] = None,
    status: str = "success",
    error_message: Optional[str] = None,
):
    """
    파일 처리 결과를 file_process_log 테이블에 INSERT하여 감사 이력을 남김.

    파일 경로, 이름, 유형, 크기, 수행 액션, 대상 테이블, 성공/실패 상태,
    에러 메시지를 기록합니다. 로그 기록 자체가 실패해도 예외를 전파하지 않고
    logger.exception()으로만 기록합니다.
    """
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
