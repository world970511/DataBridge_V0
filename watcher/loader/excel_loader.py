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


def load_excel(file_path: str, data_category: str = "statistics") -> list[str]:
    """
    Excel 파일의 모든 시트를 순회하며 각 시트를 PostgreSQL 테이블로 적재.

    처리 흐름: pd.ExcelFile로 시트 목록 파악 → 시트별 DataFrame 읽기 →
    빈 시트 건너뛰기 → 테이블명 결정(단일 시트: 파일명, 다중 시트: 파일명_시트명) →
    컬럼명 정제 → DROP/CREATE/INSERT → Rich Catalog 메타데이터 생성 →
    카탈로그 등록 → 처리 이력 기록.

    Args:
        file_path: Excel 파일 경로.
        data_category: content_analyzer가 결정한 카테고리 (기본 "statistics").
                       스마트 분류기에서 전달됩니다.

    Returns: 성공적으로 생성된 테이블 이름들의 리스트 (실패 시 빈 리스트).
    """
    path = Path(file_path)
    base_name = sanitize_table_name(path.stem)
    created_tables = []

    try:
        # XML Spreadsheet 2003 감지 → 전용 파서로 처리
        fmt = _detect_excel_format(path)
        if fmt == "xml_spreadsheet":
            logger.info(f"Detected XML Spreadsheet 2003: {file_path}")
            sheets = _parse_xml_spreadsheet(file_path)
            if not sheets:
                logger.warning(f"No data in XML Spreadsheet: {file_path}")
                log_file_process(file_path, "excel", "load_to_db", None, "failed", "XML parse empty")
                return []
            return _load_sheets(sheets, base_name, file_path, data_category)

        # 일반 Excel: 엔진 자동 선택
        engine = "xlrd" if fmt == "xls_binary" else "openpyxl"
        xls = pd.ExcelFile(file_path, engine=engine)
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

            # Rich Catalog 메타데이터 생성
            try:
                from watcher.metadata_generator import generate_rich_metadata
                metadata = generate_rich_metadata(
                    df=df,
                    table_name=table_name,
                    source_file=file_path,
                    data_category=data_category,
                )
            except Exception as meta_err:
                logger.warning(f"Metadata generation failed for sheet {sheet_name}: {meta_err}")
                metadata = None

            # 카탈로그 등록 (확장된 메타데이터 포함)
            columns_info = [
                {"name": col, "dtype": str(df[col].dtype)}
                for col in df.columns
            ]
            register_kwargs = dict(
                table_name=table_name,
                source_file=file_path,
                file_type="excel",
                row_count=len(df),
                column_count=len(df.columns),
                columns_json=columns_info,
            )
            if metadata:
                register_kwargs.update(
                    description=metadata.description,
                    data_category=metadata.data_category,
                    tags=metadata.tags,
                    column_descriptions=metadata.column_descriptions,
                    sample_values=metadata.sample_values,
                    numeric_ratio=metadata.numeric_ratio,
                    avg_text_length=metadata.avg_text_length,
                )
            register_table(**register_kwargs)

            created_tables.append(table_name)
            logger.info(f"Excel sheet loaded: {sheet_name} → {table_name} ({len(df)} rows)")

        log_file_process(
            file_path, "excel", "load_to_db",
            ",".join(created_tables) if created_tables else None,
            "success" if created_tables else "failed",
            None if created_tables else "All sheets empty",
        )

        if created_tables:
            from notifications.dispatcher import emit_event
            emit_event("file.loaded", {"tables": created_tables, "count": len(created_tables), "file": path.name})

        return created_tables

    except Exception as e:
        logger.exception(f"Failed to load Excel: {file_path}")
        log_file_process(file_path, "excel", "load_to_db", None, "failed", str(e))

        from notifications.dispatcher import emit_event
        emit_event("file.failed", {"file": path.name, "error": str(e)[:300]})

        return []


def _detect_excel_format(path: Path) -> str:
    """
    파일 매직 바이트를 확인하여 Excel 포맷 유형을 반환.

    Returns:
        "xlsx": ZIP 기반 Office Open XML (.xlsx)
        "xls_binary": OLE2 기반 바이너리 (.xls)
        "xml_spreadsheet": XML Spreadsheet 2003 (확장자만 .xls인 XML 파일)
        "unknown": 판별 불가 시 확장자 기반
    """
    try:
        with open(path, "rb") as f:
            header = f.read(32)
    except OSError:
        return "xlsx"

    # ZIP (PK\x03\x04) → .xlsx
    if header[:4] == b"PK\x03\x04":
        return "xlsx"
    # OLE2 Compound Document (진짜 .xls 바이너리)
    if header[:4] == b"\xd0\xcf\x11\xe0":
        return "xls_binary"
    # XML Spreadsheet 2003 (탭이나 공백 뒤에 <?xml 이 오는 경우 포함)
    if b"<?xml" in header or b"<html" in header.lower():
        return "xml_spreadsheet"

    # 확장자 기반 폴백
    return "xls_binary" if path.suffix.lower() == ".xls" else "xlsx"


def _parse_xml_spreadsheet(file_path: str) -> dict[str, pd.DataFrame]:
    """
    XML Spreadsheet 2003 (MS Office SpreadsheetML) 포맷을 파싱.

    일부 시스템(ERP, 레거시 도구)에서 확장자는 .xls이지만 실제 내용은
    XML Spreadsheet 2003 형식으로 내보내는 경우가 있습니다.
    openpyxl, xlrd 모두 이 형식을 지원하지 않으므로 직접 XML 파싱합니다.

    Args:
        file_path: XML Spreadsheet 파일 경로.

    Returns:
        {시트명: DataFrame} 딕셔너리. 파싱 실패 시 빈 딕셔너리.
    """
    NS = "urn:schemas-microsoft-com:office:spreadsheet"
    ns = {"ss": NS}

    try:
        # lxml의 recover 모드로 비표준 XML도 최대한 파싱
        from lxml import etree

        # 바이트로 읽어서 인코딩 자동 감지 (XML 선언 기반)
        with open(file_path, "rb") as f:
            raw_bytes = f.read()

        # 선행 탭/공백 제거 (<?xml 앞에 비표준 문자 대응)
        raw_bytes = raw_bytes.lstrip(b"\t \n\r\xef\xbb\xbf")

        parser = etree.XMLParser(recover=True, encoding=None)
        root = etree.fromstring(raw_bytes, parser=parser)

    except Exception as e:
        logger.warning(f"XML Spreadsheet parse error: {file_path} ({e})")
        return {}

    sheets: dict[str, pd.DataFrame] = {}

    for worksheet in root.findall(".//ss:Worksheet", ns):
        sheet_name = worksheet.get(f"{{{NS}}}Name", "Sheet1")
        table = worksheet.find("ss:Table", ns)
        if table is None:
            continue

        rows_data = []
        for row in table.findall("ss:Row", ns):
            cells = []
            for cell in row.findall("ss:Cell", ns):
                # ss:Index 속성으로 빈 셀 건너뛰기 처리
                idx_attr = cell.get(f"{{{NS}}}Index")
                if idx_attr:
                    target_idx = int(idx_attr) - 1
                    while len(cells) < target_idx:
                        cells.append(None)

                data_elem = cell.find("ss:Data", ns)
                if data_elem is not None and data_elem.text:
                    data_type = data_elem.get(f"{{{NS}}}Type", "String")
                    val = data_elem.text
                    # 숫자 타입 변환
                    if data_type == "Number":
                        try:
                            val = float(val)
                            if val == int(val):
                                val = int(val)
                        except ValueError:
                            pass
                    cells.append(val)
                else:
                    cells.append(None)

            if cells:
                rows_data.append(cells)

        if not rows_data:
            continue

        # 헤더 행 자동 감지: 셀 수가 가장 많은 행을 헤더로 사용
        # (제목 행이 1~2셀만 가진 경우를 건너뛰기 위함)
        max_cell_count = max(len(r) for r in rows_data)
        header_idx = 0
        if max_cell_count >= 3:
            for i, r in enumerate(rows_data):
                if len(r) >= max_cell_count * 0.8:  # 최대 셀 수의 80% 이상이면 헤더
                    header_idx = i
                    break

        headers = [str(h) if h is not None else f"col_{i}" for i, h in enumerate(rows_data[header_idx])]
        data_rows = rows_data[header_idx + 1:]

        # 컬럼 수 맞추기 (데이터 행이 헤더보다 짧을 수 있음)
        max_cols = len(headers)
        normalized = []
        for row in data_rows:
            if len(row) < max_cols:
                row.extend([None] * (max_cols - len(row)))
            elif len(row) > max_cols:
                row = row[:max_cols]
            normalized.append(row)

        df = pd.DataFrame(normalized, columns=headers)
        sheets[sheet_name] = df

    return sheets


def _load_sheets(
    sheets: dict[str, pd.DataFrame],
    base_name: str,
    file_path: str,
    data_category: str,
) -> list[str]:
    """
    파싱된 시트 딕셔너리를 DB에 적재하고 카탈로그에 등록하는 공통 로직.

    load_excel()의 시트 루프와 동일한 처리를 수행합니다.
    XML Spreadsheet 파서와 일반 Excel 파서가 모두 이 함수를 공유할 수 있습니다.

    Args:
        sheets: {시트명: DataFrame} 딕셔너리.
        base_name: sanitized된 기본 테이블명.
        file_path: 원본 파일 경로.
        data_category: 데이터 카테고리.

    Returns: 성공적으로 생성된 테이블 이름 리스트.
    """
    created_tables = []

    for sheet_name, df in sheets.items():
        if df.empty:
            logger.warning(f"Empty sheet: {sheet_name} in {file_path}")
            continue

        # 테이블명 결정
        if len(sheets) == 1:
            table_name = base_name
        else:
            sheet_safe = sanitize_table_name(sheet_name)
            table_name = f"{base_name}_{sheet_safe}"

        # 컬럼명 정리
        df.columns = [sanitize_table_name(str(c)) for c in df.columns]

        # DB 적재
        _create_and_load(table_name, df)

        # Rich Catalog 메타데이터 생성
        try:
            from watcher.metadata_generator import generate_rich_metadata
            metadata = generate_rich_metadata(
                df=df,
                table_name=table_name,
                source_file=file_path,
                data_category=data_category,
            )
        except Exception as meta_err:
            logger.warning(f"Metadata generation failed for sheet {sheet_name}: {meta_err}")
            metadata = None

        # 카탈로그 등록
        columns_info = [
            {"name": col, "dtype": str(df[col].dtype)}
            for col in df.columns
        ]
        register_kwargs = dict(
            table_name=table_name,
            source_file=file_path,
            file_type="excel",
            row_count=len(df),
            column_count=len(df.columns),
            columns_json=columns_info,
        )
        if metadata:
            register_kwargs.update(
                description=metadata.description,
                data_category=metadata.data_category,
                tags=metadata.tags,
                column_descriptions=metadata.column_descriptions,
                sample_values=metadata.sample_values,
                numeric_ratio=metadata.numeric_ratio,
                avg_text_length=metadata.avg_text_length,
            )
        register_table(**register_kwargs)

        created_tables.append(table_name)
        logger.info(f"Excel sheet loaded: {sheet_name} → {table_name} ({len(df)} rows)")

    log_file_process(
        file_path, "excel", "load_to_db",
        ",".join(created_tables) if created_tables else None,
        "success" if created_tables else "failed",
        None if created_tables else "All sheets empty",
    )
    return created_tables


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
