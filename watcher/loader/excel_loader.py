"""
Excel (.xlsx, .xls) 파일을 pandas로 읽어 PostgreSQL 테이블로 자동 적재하는 모듈.

시트가 1개이면 파일명을 테이블명으로 사용하고, 시트가 여러 개이면
'파일명_시트명' 형태로 시트별 별도 테이블을 생성합니다.
각 시트마다 컬럼명을 SQL 안전 형태로 변환하고, 카탈로그에 메타데이터를 등록합니다.
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
    Excel 파일의 모든 시트를 순회하며 각 시트를 PostgreSQL 테이블로 적재.

    처리 흐름: pd.ExcelFile로 시트 목록 파악 → 시트별 DataFrame 읽기 →
    빈 시트 건너뛰기 → 테이블명 결정(단일 시트: 파일명, 다중 시트: 파일명_시트명) →
    컬럼명 정제 → DROP/CREATE/INSERT → 카탈로그 등록 → 처리 이력 기록.
    Returns: 성공적으로 생성된 테이블 이름들의 리스트 (실패 시 빈 리스트).
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

            rows = [
                tuple(None if pd.isna(v) else v for v in row)
                for row in df.itertuples(index=False, name=None)
            ]

            cur.executemany(insert_sql, rows)

        cur.close()
