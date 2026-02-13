"""
파일 유형 자동 분류 및 적절한 로더로 라우팅하는 모듈.

config/classification.yaml에 정의된 규칙(패턴 → 액션 매핑)을 기반으로
파일 확장자를 분석하여 세 가지 액션 중 하나를 결정합니다:
- load_to_db: CSV/Excel → PostgreSQL 적재 (csv_loader, excel_loader)
- register_for_search: PDF/DOCX/TXT → 텍스트 추출 후 ChromaDB 저장 (document_loader)
- ignore: 임시 파일, 시스템 파일 등 무시

YAML 파일이 없으면 DEFAULT_RULES를 폴백으로 사용합니다.
"""

import fnmatch
import logging
from pathlib import Path

import yaml

from config.settings import get_settings

logger = logging.getLogger(__name__)

# 기본 분류 규칙 (yaml 로드 실패 시 폴백)
DEFAULT_RULES = [
    {"patterns": ["*.xlsx", "*.xls", "*.csv", "*.tsv"], "action": "load_to_db"},
    {"patterns": ["*.pdf", "*.hwp", "*.hwpx", "*.docx", "*.txt"], "action": "register_for_search"},
    {"patterns": ["~$*", "*.tmp", "Thumbs.db", ".DS_Store", "*.swp", "*.lock"], "action": "ignore"},
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
    .hwp/.hwpx→'hwp', .docx→'docx', .txt→'text', .json→'json'.
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
        ".docx": "docx",
        ".txt": "text",
        ".json": "json",
    }
    return type_map.get(suffix, "unknown")


def classify_file(file_path: str):
    """
    파일의 액션과 유형을 판별한 뒤 적절한 로더 함수를 호출.

    get_file_action()으로 액션을, get_file_type()으로 유형을 결정하고,
    'load_to_db'이면 DB 로더, 'register_for_search'이면 문서 로더,
    'ignore'이면 아무 작업도 하지 않습니다.
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
    else:
        logger.warning(f"Unknown action: {action} for {file_path}")


def _route_to_db_loader(file_path: str, file_type: str):
    """
    정형 데이터(CSV/Excel) 파일을 해당하는 DB 로더 함수로 라우팅.

    file_type이 'csv'이면 csv_loader.load_csv(), 'excel'이면
    excel_loader.load_excel()을 지연 임포트하여 호출합니다.
    """
    if file_type == "csv":
        from watcher.loader.csv_loader import load_csv
        load_csv(file_path)
    elif file_type == "excel":
        from watcher.loader.excel_loader import load_excel
        load_excel(file_path)
    else:
        logger.warning(f"No DB loader for type: {file_type}")


def _route_to_doc_loader(file_path: str, file_type: str):
    """
    비정형 문서(PDF, DOCX, TXT 등)를 document_loader.load_document()로 라우팅.

    지연 임포트를 사용하여 순환 참조를 방지합니다.
    """
    from watcher.loader.document_loader import load_document
    load_document(file_path, file_type)
