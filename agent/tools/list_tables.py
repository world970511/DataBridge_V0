"""
카탈로그 테이블·컬럼 목록 도구.

catalog.catalog 모듈을 래핑하여 AI 에이전트(특히 SQL 에이전트)가 사용하기 적합한
형태로 데이터베이스 스키마 정보를 제공합니다. SQL 생성 시 LLM의 시스템 프롬프트에
삽입되어 어떤 테이블과 컬럼이 존재하는지 알려주는 핵심 도구입니다.

Rich Catalog이 활성화된 테이블의 경우 description, tags, column_descriptions도
함께 포맷팅하여 LLM이 더 정확한 SQL을 생성할 수 있도록 합니다.

주요 함수:
    get_all_tables_summary() -> str
        - 전체 테이블의 이름, 행 수, 설명, 태그, 컬럼 목록을 한눈에 볼 수 있는 텍스트 요약.
        - SQL 에이전트의 시스템 프롬프트에 포함되어 LLM이 참조합니다.
    get_table_names() -> list[str]
        - 등록된 테이블명만 리스트로 반환. 의도 분류 시 사용됩니다.
    get_table_tags() -> dict[str, list[str]]
        - 모든 테이블의 태그 매핑 반환. 오케스트레이터 태그 매칭에 사용됩니다.

의존 모듈:
    - catalog.catalog: list_tables(), get_table_info(), get_table_tags()

사용 예시:
    from agent.tools.list_tables import get_all_tables_summary, get_table_names

    schema_text = get_all_tables_summary()
    # "## 사용 가능한 테이블\\n\\n### 1. sales (15,230행)\\n설명: 2월 매출 데이터\\n..."

    table_names = get_table_names()
    # ["sales", "products", "customers"]
"""

import json
import logging
from typing import Optional

from catalog.catalog import list_tables, get_table_info

logger = logging.getLogger(__name__)


def get_all_tables_summary() -> str:
    """
    카탈로그에 등록된 모든 테이블의 스키마 정보를 마크다운 형식 텍스트로 요약.

    Rich Catalog이 활성화된 테이블의 경우 description, tags,
    column_descriptions도 함께 표시하여 LLM이 더 정확한 SQL을 생성하도록 합니다.

    Returns:
        마크다운 형식의 스키마 요약 텍스트.
        테이블이 하나도 없으면 "등록된 테이블이 없습니다." 를 반환합니다.

    반환 형식 예시 (Rich Catalog 활성화 시):
        ## 사용 가능한 테이블

        ### 1. sales (15,230행)
        설명: 2024년 2월 제품별 일별 매출 데이터
        태그: 매출, 제품, 월별
        컬럼: id(BIGINT), product_name(TEXT) - 제품명, amount(DOUBLE PRECISION) - 매출액(원)
    """
    try:
        tables = list_tables()
    except Exception as e:
        logger.error(f"Failed to list tables from catalog: {e}")
        return "Failed to query catalog."

    if not tables:
        return "No tables registered."

    lines = ["## Available Tables\n"]

    for idx, table in enumerate(tables, 1):
        table_name = table.get("table_name", "unknown")
        row_count = table.get("row_count", 0)
        description = table.get("description")
        tags = table.get("tags")
        column_descs = table.get("column_descriptions")

        # Basic info
        lines.append(f"### {idx}. {table_name} ({row_count:,} rows)")

        # Description (Rich Catalog)
        if description:
            lines.append(f"Description: {description}")

        # Tags (Rich Catalog)
        if tags:
            lines.append(f"Tags: {', '.join(tags)}")

        # Column info (with descriptions if available)
        columns_text = _format_columns_rich(
            table.get("columns_json"), column_descs
        )
        lines.append(f"Columns: {columns_text}")
        lines.append("")  # 빈 줄 구분

    return "\n".join(lines)


def get_table_names() -> list[str]:
    """
    카탈로그에 등록된 모든 테이블명을 문자열 리스트로 반환.

    의도 분류(orchestrator) 단계에서 사용자 질의에 테이블명이 포함되어 있는지
    매칭할 때 활용됩니다. catalog.list_tables()의 결과에서 table_name 필드만 추출합니다.

    Returns:
        테이블명 문자열 리스트. 예: ["sales", "products", "customers"].
        카탈로그가 비어 있거나 조회 실패 시 빈 리스트.
    """
    try:
        tables = list_tables()
        return [t["table_name"] for t in tables if "table_name" in t]
    except Exception as e:
        logger.error(f"Failed to get table names: {e}")
        return []


def get_table_tags() -> dict:
    """
    모든 테이블의 태그를 {table_name: [tags]} 매핑으로 반환.

    오케스트레이터의 의도 분류 시 사용자 질의에 포함된 태그를 매칭하여
    데이터 조회 의도를 강화하는 데 사용됩니다.

    Returns:
        {"table_name": ["tag1", "tag2", ...], ...} 형태의 딕셔너리.
        태그가 없는 테이블은 제외됩니다.
    """
    try:
        from catalog.catalog import get_table_tags as _get_table_tags
        return _get_table_tags()
    except Exception as e:
        logger.error(f"Failed to get table tags: {e}")
        return {}


def _format_columns_rich(columns_json, column_descriptions=None) -> str:
    """
    컬럼 정보를 Rich Catalog 설명과 함께 포맷팅.

    column_descriptions가 있으면 "컬럼명(타입) - 설명" 형식으로,
    없으면 기존 "컬럼명(타입)" 형식으로 반환합니다.

    Args:
        columns_json: 컬럼 정보. list[dict] 또는 JSON 문자열. 각 딕셔너리는
                      {"name": "컬럼명", "type": "데이터타입"} 형태를 기대합니다.
        column_descriptions: 컬럼별 설명 딕셔너리 또는 JSON 문자열.
                             {"컬럼명": "설명", ...} 형태.

    Returns:
        "id(BIGINT), name(TEXT) - 제품명, ..." 형식의 문자열.
        파싱 실패 또는 데이터가 없으면 "(컬럼 정보 없음)".
    """
    if not columns_json:
        return "(No column info)"

    try:
        # Parse if JSONB returned as string
        if isinstance(columns_json, str):
            columns_json = json.loads(columns_json)

        if not isinstance(columns_json, list):
            return "(No column info)"

        # column_descriptions를 dict로 파싱
        col_descs = {}
        if column_descriptions:
            if isinstance(column_descriptions, str):
                try:
                    col_descs = json.loads(column_descriptions)
                except (json.JSONDecodeError, TypeError):
                    col_descs = {}
            elif isinstance(column_descriptions, dict):
                col_descs = column_descriptions

        parts = []
        for col in columns_json:
            col_name = col.get("name", col.get("column", "?"))
            col_type = col.get("type", col.get("dtype", "?"))

            desc = col_descs.get(col_name)
            if desc:
                parts.append(f"{col_name}({col_type}) - {desc}")
            else:
                parts.append(f"{col_name}({col_type})")

        return ", ".join(parts) if parts else "(No column info)"

    except (json.JSONDecodeError, TypeError, AttributeError):
        return "(No column info)"


def get_table_column_info(table_name: str) -> str | None:
    """
    특정 테이블의 컬럼 정보를 상세 포맷으로 반환.

    카탈로그에서 해당 테이블의 컬럼명, 타입, 설명(Rich Catalog)을 조회하여
    사용자가 읽기 쉬운 텍스트로 포맷팅합니다.
    "컬럼이 뭐야?", "스키마 알려줘" 같은 메타데이터 질의에 LLM 없이 즉시 응답하는 데 사용됩니다.

    Args:
        table_name: 조회할 테이블명.

    Returns:
        컬럼 정보 포맷팅 텍스트. 테이블이 없으면 None.
    """
    try:
        info = get_table_info(table_name)
        if not info:
            return None

        columns_json = info.get("columns_json")
        column_descs = info.get("column_descriptions")
        description = info.get("description")
        tags = info.get("tags")
        row_count = info.get("row_count", 0)

        lines = [f"📊 **{table_name}** ({row_count:,}행)"]

        if description:
            lines.append(f"설명: {description}")
        if tags:
            tag_list = tags if isinstance(tags, list) else []
            if tag_list:
                lines.append(f"태그: {', '.join(tag_list)}")

        lines.append(f"\n컬럼 목록 ({_count_columns(columns_json)}개):")

        # 컬럼 상세 정보
        col_descs = _parse_column_descriptions(column_descs)
        col_lines = _format_column_detail(columns_json, col_descs)
        lines.extend(col_lines)

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Failed to get column info for {table_name}: {e}")
        return None


def _count_columns(columns_json) -> int:
    """컬럼 수를 반환."""
    if not columns_json:
        return 0
    try:
        if isinstance(columns_json, str):
            import json as _json
            columns_json = _json.loads(columns_json)
        return len(columns_json) if isinstance(columns_json, list) else 0
    except Exception:
        return 0


def _parse_column_descriptions(column_descriptions) -> dict:
    """column_descriptions를 dict로 파싱."""
    if not column_descriptions:
        return {}
    if isinstance(column_descriptions, dict):
        return column_descriptions
    if isinstance(column_descriptions, str):
        try:
            return json.loads(column_descriptions)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _format_column_detail(columns_json, col_descs: dict) -> list[str]:
    """컬럼 정보를 상세 리스트로 포맷팅."""
    if not columns_json:
        return ["  (컬럼 정보 없음)"]

    try:
        if isinstance(columns_json, str):
            columns_json = json.loads(columns_json)

        if not isinstance(columns_json, list):
            return ["  (컬럼 정보 없음)"]

        lines = []
        for i, col in enumerate(columns_json, 1):
            col_name = col.get("name", col.get("column", "?"))
            col_type = col.get("type", col.get("dtype", "?"))

            desc = col_descs.get(col_name, "")
            if desc:
                lines.append(f"  {i}. {col_name} ({col_type}) — {desc}")
            else:
                lines.append(f"  {i}. {col_name} ({col_type})")

        return lines if lines else ["  (컬럼 정보 없음)"]

    except (json.JSONDecodeError, TypeError, AttributeError):
        return ["  (컬럼 정보 없음)"]


# 하위 호환: 기존 _format_columns 이름으로도 접근 가능
_format_columns = _format_columns_rich
