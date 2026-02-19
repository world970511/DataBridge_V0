"""
CSV/TSV 파일을 pandas로 읽어 PostgreSQL 테이블로 자동 적재하는 모듈.

파일명에서 테이블명을 생성하고, 확장자에 따라 구분자(CSV→쉼표, TSV→탭)를
자동 감지합니다. 인코딩은 UTF-8 → CP949 → EUC-KR → Latin-1 순서로 시도하여
한글 파일도 처리 가능합니다. 기존 동명 테이블은 DROP 후 재생성됩니다.
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
    CSV/TSV 파일을 pandas DataFrame으로 읽어 PostgreSQL 테이블로 적재.

    처리 흐름: 구분자 감지 → 다중 인코딩 시도로 파일 읽기 → 컬럼명 SQL 안전 변환 →
    테이블 DROP/CREATE → 데이터 INSERT → 카탈로그 등록 → 처리 이력 기록.
    빈 파일이거나 예외 발생 시 실패 이력을 남기고 None을 반환합니다.
    Returns: 생성된 테이블 이름 문자열 또는 실패 시 None.
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
    """
    여러 인코딩(UTF-8, CP949, EUC-KR, Latin-1)을 순서대로 시도하여 CSV 파일을 읽음.

    한글이 포함된 파일의 경우 UTF-8 실패 시 CP949, EUC-KR 순으로 시도하며,
    모든 인코딩이 실패하면 ValueError를 발생시킵니다.
    Returns: 파일 내용을 담은 pandas DataFrame.
    """
    for encoding in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return pd.read_csv(file_path, sep=sep, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode CSV with any supported encoding: {file_path}")


def _create_and_load(table_name: str, df: pd.DataFrame):
    """
    기존 테이블을 DROP 후 DataFrame 스키마에 맞는 새 테이블을 CREATE하고 데이터를 INSERT.

    df_to_pg_types()로 pandas dtype을 PostgreSQL 타입으로 매핑하여 DDL을 생성하고,
    DataFrame의 각 행을 executemany()로 일괄 삽입합니다. NaN 값은 None으로 변환됩니다.
    """
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
