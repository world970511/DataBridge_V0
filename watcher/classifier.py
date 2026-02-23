"""
파일 유형 자동 분류 및 적절한 로더로 라우팅하는 모듈.

config/classification.yaml에 정의된 규칙(패턴 → 액션 매핑)을 기반으로
파일 확장자를 분석하여 세 가지 액션 중 하나를 결정합니다:
- load_to_db: CSV/Excel → PostgreSQL 적재 (csv_loader, excel_loader)
- register_for_search: PDF/DOCX/TXT → 텍스트 추출 후 ChromaDB 저장 (document_loader)
- ignore: 임시 파일, 시스템 파일 등 무시

스마트 분류기 (Smart Classifier):
CSV/Excel 파일의 경우 확장자 기반 분류 후 **내용 분석**을 추가로 수행합니다.
content_analyzer 모듈이 DataFrame의 숫자 비율, 텍스트 길이 등을 분석하여
통계형 데이터는 DB로, 문서형 데이터(테스트 케이스, 체크리스트 등)는 ChromaDB로
자동 전환합니다. YAML 파일이 없으면 DEFAULT_RULES를 폴백으로 사용합니다.
"""

import fnmatch
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from config.settings import get_settings

logger = logging.getLogger(__name__)

# 기본 분류 규칙 (yaml 로드 실패 시 폴백)
# 무시 규칙이 가장 먼저: ~$temp.xlsx 같은 임시 파일이 *.xlsx보다 먼저 매칭되도록
DEFAULT_RULES = [
    {"patterns": ["~$*", "*.tmp", "Thumbs.db", ".DS_Store", "*.swp", "*.lock"], "action": "ignore"},
    {"patterns": ["*.xlsx", "*.xls", "*.csv", "*.tsv"], "action": "load_to_db"},
    {"patterns": ["*.pdf", "*.hwp", "*.hwpx", "*.doc", "*.docx", "*.ppt", "*.pptx", "*.txt"], "action": "register_for_search"},
    {"patterns": ["*.jpg", "*.jpeg", "*.png", "*.tiff", "*.tif", "*.bmp", "*.webp", "*.heic"], "action": "register_image"},
]


def _load_rules() -> list[dict]:
    """
    config/classification.yaml 파일에서 파일 분류 규칙을 읽어옴.

    YAML 파일이 존재하면 'rules' 키의 값을 반환하고,
    파일을 찾을 수 없으면 DEFAULT_RULES(Excel/CSV→DB, PDF/DOCX→검색, 임시파일→무시)를 사용합니다.
    Returns: [{"patterns": [...], "action": "..."}, ...] 형태의 규칙 리스트.
    """
    yaml_path = Path(__file__).parent.parent / "config" / "classification.yaml"
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("rules", DEFAULT_RULES)
    except FileNotFoundError:
        logger.warning("classification.yaml not found, using defaults")
        return DEFAULT_RULES


def get_file_action(file_path: str) -> str:
    """
    파일명을 분류 규칙의 패턴과 fnmatch로 비교하여 수행할 액션을 결정.

    규칙에 매칭되는 패턴이 없으면 기본값 'ignore'를 반환합니다.
    Returns: 'load_to_db', 'register_for_search', 또는 'ignore' 문자열.
    """
    name = Path(file_path).name.lower()
    rules = _load_rules()

    for rule in rules:
        for pattern in rule["patterns"]:
            if fnmatch.fnmatch(name, pattern.lower()):
                return rule["action"]

    logger.info(f"No matching rule for: {name}, defaulting to ignore")
    return "ignore"


def get_file_type(file_path: str) -> str:
    """
    파일 확장자를 기반으로 내부 파일 유형 문자열을 반환.

    지원되는 매핑: .csv/.tsv→'csv', .xlsx/.xls→'excel', .pdf→'pdf',
    .hwp/.hwpx→'hwp', .doc→'doc', .docx→'docx', .ppt→'ppt', .pptx→'pptx',
    .txt→'text', .json→'json'.
    매핑에 없는 확장자는 'unknown'을 반환합니다.
    Returns: 파일 유형을 나타내는 문자열 (예: 'csv', 'excel', 'pdf').
    """
    suffix = Path(file_path).suffix.lower()
    type_map = {
        ".csv": "csv",
        ".tsv": "csv",
        ".xlsx": "excel",
        ".xls": "excel",
        ".pdf": "pdf",
        ".hwp": "hwp",
        ".hwpx": "hwp",
        ".doc": "doc",
        ".docx": "docx",
        ".ppt": "ppt",
        ".pptx": "pptx",
        ".txt": "text",
        ".json": "json",
        ".jpg": "image",
        ".jpeg": "image",
        ".png": "image",
        ".tiff": "image",
        ".tif": "image",
        ".bmp": "image",
        ".webp": "image",
        ".heic": "image",
    }
    return type_map.get(suffix, "unknown")


def classify_file(file_path: str):
    """
    파일의 액션과 유형을 판별한 뒤 적절한 로더 함수를 호출.

    get_file_action()으로 액션을, get_file_type()으로 유형을 결정하고,
    'load_to_db'이면 스마트 분류기를 통해 DB 또는 문서 로더로 라우팅,
    'register_for_search'이면 문서 로더, 'ignore'이면 아무 작업도 하지 않습니다.
    """
    action = get_file_action(file_path)
    file_type = get_file_type(file_path)

    logger.info(f"Classified: {file_path} → type={file_type}, action={action}")

    if action == "ignore":
        logger.debug(f"Ignoring file: {file_path}")
        return

    if action == "load_to_db":
        _route_to_db_loader(file_path, file_type)
    elif action == "register_for_search":
        _route_to_doc_loader(file_path, file_type)
    elif action == "register_image":
        _route_to_image_loader(file_path, file_type)
    else:
        logger.warning(f"Unknown action: {action} for {file_path}")


def _route_to_db_loader(file_path: str, file_type: str):
    """
    정형 데이터(CSV/Excel) 파일을 스마트 분류 후 적절한 로더로 라우팅.

    1) 파일에서 DataFrame을 읽어 content_analyzer로 내용 분석
    2) 카테고리가 "document" 또는 "reference"이면 문서 파이프라인으로 전환
    3) "statistics" 또는 "log"이면 기존 DB 로더로 전달 (data_category 포함)
    4) 분석 실패 시 기본 동작(DB 적재)으로 폴백
    """
    # DataFrame 읽기 (분석용, 최대 100행)
    df = _read_dataframe_for_analysis(file_path, file_type)

    if df is None or df.empty:
        # 분석 불가 시 기본 동작 (DB 적재)
        logger.debug(f"Cannot analyze content, falling back to DB: {file_path}")
        _load_to_db_directly(file_path, file_type)
        return

    try:
        from watcher.content_analyzer import analyze_dataframe

        analysis = analyze_dataframe(df)
        logger.info(
            f"Smart classification: {file_path} → "
            f"category={analysis.data_category}, "
            f"numeric_ratio={analysis.numeric_ratio:.2f}, "
            f"avg_text_length={analysis.avg_text_length:.1f}, "
            f"has_long_text={analysis.has_long_text}"
        )

        if analysis.data_category in ("document", "reference"):
            # 문서형 데이터 → ChromaDB로 전환
            logger.info(
                f"Smart routing: {file_path} → register_for_search "
                f"(was load_to_db, category={analysis.data_category})"
            )
            _route_spreadsheet_as_document(
                file_path, file_type, df, analysis.data_category
            )
        else:
            # 통계형 데이터 → 기존 DB 적재 (data_category 전달)
            _load_to_db_directly(
                file_path, file_type, data_category=analysis.data_category
            )

    except Exception as e:
        logger.warning(
            f"Smart classification failed, falling back to DB: {file_path} ({e})"
        )
        _load_to_db_directly(file_path, file_type)


def _read_dataframe_for_analysis(
    file_path: str, file_type: str, max_rows: int = 100
) -> Optional[pd.DataFrame]:
    """
    분석을 위해 파일에서 처음 max_rows행을 읽어 DataFrame으로 반환.

    전체 파일을 읽지 않고 처음 max_rows행만 읽어 메모리와 시간을 절약합니다.
    파일이 깨졌거나 인코딩 문제 등으로 실패하면 None을 반환합니다.

    Args:
        file_path: 파일 경로.
        file_type: 파일 유형 ('csv' 또는 'excel').
        max_rows: 분석에 사용할 최대 행 수 (기본 100).

    Returns:
        분석용 DataFrame 또는 실패 시 None.
    """
    try:
        if file_type == "csv":
            from watcher.loader.csv_loader import detect_separator
            sep = detect_separator(file_path)
            for encoding in ("utf-8", "cp949", "euc-kr", "latin-1"):
                try:
                    return pd.read_csv(
                        file_path, sep=sep, encoding=encoding, nrows=max_rows
                    )
                except UnicodeDecodeError:
                    continue
            return None

        elif file_type == "excel":
            # XML Spreadsheet 2003 대응
            from watcher.loader.excel_loader import _detect_excel_format, _parse_xml_spreadsheet
            fmt = _detect_excel_format(Path(file_path))
            if fmt == "xml_spreadsheet":
                sheets = _parse_xml_spreadsheet(file_path)
                if sheets:
                    first_df = next(iter(sheets.values()))
                    return first_df.head(max_rows)
                return None
            engine = "xlrd" if fmt == "xls_binary" else "openpyxl"
            xls = pd.ExcelFile(file_path, engine=engine)
            # 첫 번째 시트만 분석 (대표성 확보)
            return pd.read_excel(xls, sheet_name=0, nrows=max_rows)

        else:
            return None

    except Exception as e:
        logger.debug(f"Failed to read DataFrame for analysis: {file_path} ({e})")
        return None


def _load_to_db_directly(
    file_path: str, file_type: str, data_category: str = "statistics"
):
    """
    기존 DB 로더를 호출하여 파일을 PostgreSQL에 적재.

    Args:
        file_path: 파일 경로.
        file_type: 파일 유형 ('csv' 또는 'excel').
        data_category: content_analyzer가 결정한 카테고리 (기본 "statistics").
    """
    if file_type == "csv":
        from watcher.loader.csv_loader import load_csv
        load_csv(file_path, data_category=data_category)
    elif file_type == "excel":
        from watcher.loader.excel_loader import load_excel
        load_excel(file_path, data_category=data_category)
    else:
        logger.warning(f"No DB loader for type: {file_type}")


def _route_spreadsheet_as_document(
    file_path: str, file_type: str, df: pd.DataFrame, data_category: str
):
    """
    문서형으로 판별된 CSV/Excel을 document_loader 파이프라인으로 전달.

    DataFrame의 각 행을 텍스트 문단으로 변환하여 ChromaDB에 저장합니다.

    Args:
        file_path: 원본 파일 경로.
        file_type: 파일 유형 ('csv' 또는 'excel').
        df: 분석용으로 이미 읽어둔 DataFrame (분석 시 max_rows로 제한된 상태).
        data_category: 데이터 카테고리 ('document' 또는 'reference').
    """
    try:
        # 전체 파일을 다시 읽어서 문서로 처리
        # (분석용으로 읽은 df는 max_rows로 제한되었을 수 있음)
        full_df = _read_full_dataframe(file_path, file_type)
        if full_df is None or full_df.empty:
            full_df = df  # 폴백: 분석용 DataFrame 사용

        from watcher.loader.document_loader import load_document_from_dataframe
        load_document_from_dataframe(file_path, file_type, full_df, data_category)

    except Exception as e:
        logger.exception(
            f"Failed to route spreadsheet as document: {file_path} ({e})"
        )
        # 폴백: DB에 적재
        logger.info(f"Falling back to DB loader: {file_path}")
        _load_to_db_directly(file_path, file_type)


def _read_full_dataframe(file_path: str, file_type: str) -> Optional[pd.DataFrame]:
    """
    전체 파일을 DataFrame으로 읽기 (문서 변환용).

    Args:
        file_path: 파일 경로.
        file_type: 파일 유형 ('csv' 또는 'excel').

    Returns:
        전체 DataFrame 또는 실패 시 None.
    """
    try:
        if file_type == "csv":
            from watcher.loader.csv_loader import detect_separator
            sep = detect_separator(file_path)
            for encoding in ("utf-8", "cp949", "euc-kr", "latin-1"):
                try:
                    return pd.read_csv(file_path, sep=sep, encoding=encoding)
                except UnicodeDecodeError:
                    continue
            return None
        elif file_type == "excel":
            from watcher.loader.excel_loader import _detect_excel_format, _parse_xml_spreadsheet
            fmt = _detect_excel_format(Path(file_path))
            if fmt == "xml_spreadsheet":
                sheets = _parse_xml_spreadsheet(file_path)
                if sheets:
                    return next(iter(sheets.values()))
                return None
            engine = "xlrd" if fmt == "xls_binary" else "openpyxl"
            return pd.read_excel(file_path, sheet_name=0, engine=engine)
        else:
            return None
    except Exception:
        return None


def _route_to_image_loader(file_path: str, file_type: str):
    """이미지 파일을 image_loader.load_image()로 라우팅."""
    from watcher.loader.image_loader import load_image
    load_image(file_path, file_type)


def _route_to_doc_loader(file_path: str, file_type: str):
    """
    비정형 문서(PDF, DOCX, TXT 등)를 document_loader.load_document()로 라우팅.

    지연 임포트를 사용하여 순환 참조를 방지합니다.
    """
    from watcher.loader.document_loader import load_document
    load_document(file_path, file_type)
