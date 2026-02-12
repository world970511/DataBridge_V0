"""
Excel (.xlsx, .xls) 파일 → PostgreSQL 테이블 자동 적재.
시트가 여러 개면 시트별로 별도 테이블 생성.
"""

import logging
from pathlib import Path

import pandas as pd

from db.connection import get_connection
from catalog.catalog import register_table
from watcher.loader._utils import (
    sanitize_table_name,
    log_file_process,
    df_to_pg_types,
)

logger = logging.getLogger(__name__)


def load_excel(file_path: str) -> list[str]:
    """
    Excel 파일을 읽어 시트별로 PostgreSQL에 적재.
    Returns: 생성된 테이블 이름 리스트.
    """
    path = Path(file_path)
    base_name = sanitize_table_name(path.stem)
    created_tables = []

    try:
        xls = pd.ExcelFile(file_path)
        sheet_names = xls.sheet_names

        for sheet_name in sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name)

            if df.empty:
                logger.warning(f"Empty sheet: {sheet_name} in {file_path}")
                continue

            # 시트가 1개면 파일명, 여러 개면 파일명_시트명
            if len(sheet_names) == 1:
                table_name = base_name
            else:
                sheet_safe = sanitize_table_name(sheet_name)
                table_name = f"{base_name}_{sheet_safe}"

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
                file_type="excel",
                row_count=len(df),
                column_count=len(df.columns),
                columns_json=columns_info,
            )

            created_tables.append(table_name)
            logger.info(f"Excel sheet loaded: {sheet_name} → {table_name} ({len(df)} rows)")

        log_file_process(
            file_path, "excel", "load_to_db",
            ",".join(created_tables) if created_tables else None,
            "success" if created_tables else "failed",
            None if created_tables else "All sheets empty",
        )
        return created_tables

    except Exception as e:
        logger.exception(f"Failed to load Excel: {file_path}")
        log_file_process(file_path, "excel", "load_to_db", None, "failed", str(e))
        return []


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

            rows = [
                tuple(None if pd.isna(v) else v for v in row)
                for row in df.itertuples(index=False, name=None)
            ]

            cur.executemany(insert_sql, rows)

        cur.close()
