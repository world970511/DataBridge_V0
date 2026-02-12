"""
파일 유형 자동 분류 → 적절한 로더로 라우팅.
config/classification.yaml 기반.
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
    """classification.yaml에서 규칙 로드."""
    yaml_path = Path(__file__).parent.parent / "config" / "classification.yaml"
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("rules", DEFAULT_RULES)
    except FileNotFoundError:
        logger.warning("classification.yaml not found, using defaults")
        return DEFAULT_RULES


def get_file_action(file_path: str) -> str:
    """파일 경로를 받아 수행할 action을 반환."""
    name = Path(file_path).name.lower()
    rules = _load_rules()

    for rule in rules:
        for pattern in rule["patterns"]:
            if fnmatch.fnmatch(name, pattern.lower()):
                return rule["action"]

    logger.info(f"No matching rule for: {name}, defaulting to ignore")
    return "ignore"


def get_file_type(file_path: str) -> str:
    """확장자 기반 파일 유형 반환."""
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
    """파일을 분류하고 적절한 로더 호출."""
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
    """정형 데이터 로더로 라우팅."""
    if file_type == "csv":
        from watcher.loader.csv_loader import load_csv
        load_csv(file_path)
    elif file_type == "excel":
        from watcher.loader.excel_loader import load_excel
        load_excel(file_path)
    else:
        logger.warning(f"No DB loader for type: {file_type}")


def _route_to_doc_loader(file_path: str, file_type: str):
    """문서 로더로 라우팅."""
    from watcher.loader.document_loader import load_document
    load_document(file_path, file_type)
