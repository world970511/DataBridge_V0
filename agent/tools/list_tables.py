"""
카탈로그 테이블·컬럼 목록 도구.

catalog.catalog 모듈을 래핑하여 AI 에이전트(특히 SQL 에이전트)가 사용하기 적합한
형태로 데이터베이스 스키마 정보를 제공합니다. SQL 생성 시 LLM의 시스템 프롬프트에
삽입되어 어떤 테이블과 컬럼이 존재하는지 알려주는 핵심 도구입니다.

주요 함수:
    get_all_tables_summary() -> str
        - 전체 테이블의 이름, 행 수, 컬럼 목록을 한눈에 볼 수 있는 텍스트 요약.
        - SQL 에이전트의 시스템 프롬프트에 포함되어 LLM이 참조합니다.
    get_table_names() -> list[str]
        - 등록된 테이블명만 리스트로 반환. 의도 분류 시 사용됩니다.

의존 모듈:
    - catalog.catalog: list_tables(), get_table_info() — 카탈로그 데이터 조회

사용 예시:
    from agent.tools.list_tables import get_all_tables_summary, get_table_names

    schema_text = get_all_tables_summary()
    # "## 사용 가능한 테이블\n\n### 1. sales (15,230행)\n컬럼: id(BIGINT), ..."

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

    catalog.list_tables()로 전체 테이블 메타데이터를 조회한 뒤, 각 테이블의
    이름, 행 수, 컬럼명과 데이터 타입을 읽기 쉬운 형식으로 포맷팅합니다.
    이 텍스트는 SQL 에이전트의 시스템 프롬프트에 삽입되어 LLM이 올바른 테이블·컬럼명으로
    SQL을 생성할 수 있도록 합니다.

    columns_json 필드는 JSONB로 저장된 컬럼 정보이며, 문자열인 경우 json.loads()로
    파싱합니다. 파싱 실패 시 "(컬럼 정보 없음)"으로 표시합니다.

    Returns:
        마크다운 형식의 스키마 요약 텍스트.
        테이블이 하나도 없으면 "등록된 테이블이 없습니다." 를 반환합니다.

    반환 형식 예시:
        ## 사용 가능한 테이블

        ### 1. sales (15,230행)
        컬럼: id(BIGINT), product_name(TEXT), amount(DOUBLE PRECISION), ...

        ### 2. products (324행)
        컬럼: id(BIGINT), name(TEXT), category(TEXT), price(DOUBLE PRECISION)
    """
    try:
        tables = list_tables()
    except Exception as e:
        logger.error(f"Failed to list tables from catalog: {e}")
        return "카탈로그 조회에 실패했습니다."

    if not tables:
        return "등록된 테이블이 없습니다."

    lines = ["## 사용 가능한 테이블\n"]

    for idx, table in enumerate(tables, 1):
        table_name = table.get("table_name", "unknown")
        row_count = table.get("row_count", 0)

        # 컬럼 정보 포맷팅
        columns_text = _format_columns(table.get("columns_json"))

        lines.append(f"### {idx}. {table_name} ({row_count:,}행)")
        lines.append(f"컬럼: {columns_text}")
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


def _format_columns(columns_json) -> str:
    """
    columns_json 필드를 '컬럼명(타입), ...' 형식의 문자열로 포맷팅.

    columns_json은 catalog_tables에 JSONB로 저장되며, 조회 시 딕셔너리 리스트 또는
    JSON 문자열로 반환될 수 있습니다. 두 경우 모두 처리합니다.

    Args:
        columns_json: 컬럼 정보. list[dict] 또는 JSON 문자열. 각 딕셔너리는
                      {"name": "컬럼명", "type": "데이터타입"} 형태를 기대합니다.

    Returns:
        "id(BIGINT), name(TEXT), ..." 형식의 문자열.
        파싱 실패 또는 데이터가 없으면 "(컬럼 정보 없음)".
    """
    if not columns_json:
        return "(컬럼 정보 없음)"

    try:
        # JSONB가 문자열로 반환된 경우 파싱
        if isinstance(columns_json, str):
            columns_json = json.loads(columns_json)

        if not isinstance(columns_json, list):
            return "(컬럼 정보 없음)"

        parts = []
        for col in columns_json:
            col_name = col.get("name", col.get("column", "?"))
            col_type = col.get("type", col.get("dtype", "?"))
            parts.append(f"{col_name}({col_type})")

        return ", ".join(parts) if parts else "(컬럼 정보 없음)"

    except (json.JSONDecodeError, TypeError, AttributeError):
        return "(컬럼 정보 없음)"
