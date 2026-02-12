"""
CSV/TSV 파일 → PostgreSQL 테이블 자동 적재.
"""

import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from db.connection import get_connection
from catalog.catalog import register_table
from watcher.loader._utils import (
    sanitize_table_name,
    log_file_process,
    df_to_pg_types,
)

logger = logging.getLogger(__name__)


def load_csv(file_path: str) -> Optional[str]:
    """
    CSV/TSV 파일을 읽어 PostgreSQL에 테이블로 적재.
    Returns: 생성된 테이블 이름 또는 None.
    """
    path = Path(file_path)
    table_name = sanitize_table_name(path.stem)

    try:
        # 구분자 자동 감지
        sep = "\t" if path.suffix.lower() in (".tsv",) else ","

        # 인코딩 시도: utf-8 → cp949 (한글 파일)
        df = _read_csv(file_path, sep)

        if df.empty:
            logger.warning(f"Empty CSV: {file_path}")
            log_file_process(file_path, "csv", "load_to_db", table_name, "failed", "Empty file")
            return None

        # 컬럼명 정리
        df.columns = [sanitize_table_name(str(c)) for c in df.columns]

        # DB에 적재
        _create_and_load(table_name, df)

        # 카탈로그 등록
        columns_info = [
            {"name": col, "dtype": str(df[col].dtype)}
            for col in df.columns
        ]
        register_table(
            table_name=table_name,
            source_file=file_path,
            file_type="csv",
            row_count=len(df),
            column_count=len(df.columns),
            columns_json=columns_info,
        )

        log_file_process(file_path, "csv", "load_to_db", table_name, "success")
        logger.info(f"CSV loaded: {file_path} → {table_name} ({len(df)} rows)")
        return table_name

    except Exception as e:
        logger.exception(f"Failed to load CSV: {file_path}")
        log_file_process(file_path, "csv", "load_to_db", table_name, "failed", str(e))
        return None


def _read_csv(file_path: str, sep: str) -> pd.DataFrame:
    """UTF-8 → CP949 순서로 인코딩 시도."""
    for encoding in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return pd.read_csv(file_path, sep=sep, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode CSV with any supported encoding: {file_path}")


def _create_and_load(table_name: str, df: pd.DataFrame):
    """테이블 생성(DROP IF EXISTS) 후 데이터 INSERT."""
    col_defs = df_to_pg_types(df)

    create_sql = f"""
        DROP TABLE IF EXISTS "{table_name}";
        CREATE TABLE "{table_name}" (
            {', '.join(f'"{c}" {t}' for c, t in col_defs)}
        );
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(create_sql)

        if not df.empty:
            cols = ', '.join(f'"{c}"' for c, _ in col_defs)
            placeholders = ', '.join(['%s'] * len(col_defs))
            insert_sql = f'INSERT INTO "{table_name}" ({cols}) VALUES ({placeholders})'

            # NaN → None 변환
            rows = [
                tuple(None if pd.isna(v) else v for v in row)
                for row in df.itertuples(index=False, name=None)
            ]

            cur.executemany(insert_sql, rows)

        cur.close()
