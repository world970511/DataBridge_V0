"""
DataFrame에서 Rich Catalog 메타데이터를 생성하는 모듈.

순수 계산 메트릭(numeric_ratio, avg_text_length, sample_values)은
content_analyzer에서 가져오고, LLM 기반 메타데이터(description, tags,
column_descriptions)는 하나의 LLM 호출로 한꺼번에 생성합니다.

LLM 호출 실패 시에도 계산 메트릭은 정상적으로 반환되므로
카탈로그에 최소한의 메타데이터는 항상 저장됩니다.

의존 모듈:
    - watcher.content_analyzer: compute_numeric_ratio(), compute_avg_text_length(),
                                extract_sample_values()
    - agent._llm: generate() — description/tags/column_descriptions 생성

사용 예시:
    from watcher.metadata_generator import generate_rich_metadata

    metadata = generate_rich_metadata(df, "sales", "/data/sales.csv", "statistics")
    print(metadata.description)  # "2월 제품별 일별 매출 데이터"
    print(metadata.tags)         # ["매출", "제품", "월별"]
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RichMetadata:
    """Rich Catalog에 저장할 메타데이터 묶음.

    Attributes:
        description: 데이터 설명 (LLM 생성, 예: "2월 제품별 일별 매출 데이터").
        data_category: 데이터 카테고리 (content_analyzer에서 전달).
        tags: 검색용 태그 리스트 (LLM 생성, 예: ["매출", "제품", "월별"]).
        column_descriptions: 컬럼별 설명 (LLM 생성, 예: {"amount": "매출액(원)"}).
        sample_values: 컬럼별 샘플 값 (순수 계산).
        numeric_ratio: 숫자 컬럼 비율 (순수 계산, 0.0~1.0).
        avg_text_length: 텍스트 셀 평균 길이 (순수 계산).
    """
    description: Optional[str]
    data_category: str
    tags: Optional[list]
    column_descriptions: Optional[dict]
    sample_values: dict
    numeric_ratio: float
    avg_text_length: float


# LLM 메타데이터 생성용 시스템 프롬프트
_METADATA_SYSTEM_PROMPT = """You are a data catalog expert.
Analyze the given table's column names and sample data, then generate the following JSON:

{
  "description": "A 1-2 sentence description of this table",
  "tags": ["tag1", "tag2", ...],
  "column_descriptions": {"column1": "description1", "column2": "description2", ...}
}

Rules:
- description: Include the subject, time period, and scope of the data (e.g., "Daily product sales data for February")
- tags: 3-7 search keywords (topic/domain/period, etc.)
- column_descriptions: Briefly describe the meaning of each column
- Output ONLY valid JSON. Do not add any other text."""


def generate_rich_metadata(
    df: pd.DataFrame,
    table_name: str,
    source_file: str,
    data_category: str = "statistics",
) -> RichMetadata:
    """
    DataFrame에서 Rich Catalog 메타데이터를 일괄 생성.

    순수 계산 메트릭은 즉시 계산하고, LLM 기반 메타데이터는
    하나의 LLM 호출로 description + tags + column_descriptions를 생성합니다.
    LLM 호출 실패 시에도 계산 메트릭은 정상적으로 반환됩니다.

    Args:
        df: 대상 DataFrame.
        table_name: 테이블명 (LLM 프롬프트에 포함).
        source_file: 원본 파일 경로 (LLM 프롬프트에 포함).
        data_category: content_analyzer가 결정한 카테고리 (기본 "statistics").

    Returns:
        RichMetadata 데이터클래스 인스턴스.
    """
    from watcher.content_analyzer import (
        compute_numeric_ratio,
        compute_avg_text_length,
        extract_sample_values,
    )

    # 순수 계산 메트릭 (항상 성공)
    n_ratio = compute_numeric_ratio(df)
    avg_tl = compute_avg_text_length(df)
    samples = extract_sample_values(df)

    # LLM 기반 메타데이터 (실패 가능)
    description, tags, col_descs = _generate_llm_metadata(
        df, table_name, source_file
    )

    return RichMetadata(
        description=description,
        data_category=data_category,
        tags=tags,
        column_descriptions=col_descs,
        sample_values=samples,
        numeric_ratio=n_ratio,
        avg_text_length=avg_tl,
    )


def _generate_llm_metadata(
    df: pd.DataFrame, table_name: str, source_file: str
) -> tuple:
    """
    단일 LLM 호출로 description, tags, column_descriptions를 생성.

    LLM에게 컬럼명 + dtype + 샘플 3행을 전달하고, JSON 형태로
    세 가지 메타데이터를 동시에 생성하도록 요청합니다.

    Args:
        df: 대상 DataFrame.
        table_name: 테이블명.
        source_file: 원본 파일 경로.

    Returns:
        (description, tags, column_descriptions) 튜플.
        LLM 호출 실패 시 (None, None, None) 반환.
    """
    try:
        from agent._llm import generate

        prompt = _build_llm_prompt(df, table_name, source_file)

        response = generate(
            prompt=prompt,
            system=_METADATA_SYSTEM_PROMPT,
            purpose="agent",
            temperature=0.1,
        )

        if not response:
            logger.warning(f"LLM returned empty response for metadata: {table_name}")
            return (None, None, None)

        parsed = _parse_llm_json(response)
        if not parsed:
            logger.warning(f"Failed to parse LLM metadata JSON: {table_name}")
            return (None, None, None)

        description = parsed.get("description")
        tags = parsed.get("tags")
        col_descs = parsed.get("column_descriptions")

        # tags 타입 검증
        if tags and not isinstance(tags, list):
            tags = None
        # column_descriptions 타입 검증
        if col_descs and not isinstance(col_descs, dict):
            col_descs = None

        logger.info(
            f"LLM metadata generated for {table_name}: "
            f"desc={'yes' if description else 'no'}, "
            f"tags={len(tags) if tags else 0}, "
            f"col_descs={len(col_descs) if col_descs else 0}"
        )
        return (description, tags, col_descs)

    except Exception as e:
        logger.warning(f"LLM metadata generation failed for {table_name}: {e}")
        return (None, None, None)


def _build_llm_prompt(df: pd.DataFrame, table_name: str, source_file: str) -> str:
    """
    LLM 메타데이터 생성을 위한 프롬프트를 구성.

    토큰 절약을 위해 컬럼명, dtype, 처음 3행의 샘플 데이터만 포함합니다.

    Args:
        df: 대상 DataFrame.
        table_name: 테이블명.
        source_file: 원본 파일 경로.

    Returns:
        LLM에 전달할 프롬프트 문자열.
    """
    file_name = Path(source_file).name

    # 컬럼 정보
    col_info_lines = []
    for col in df.columns:
        col_info_lines.append(f"  - {col} ({df[col].dtype})")
    col_info = "\n".join(col_info_lines)

    # 샘플 데이터 (최대 3행, 각 셀은 50자 제한)
    sample_rows = df.head(3)
    sample_lines = []
    for idx, row in sample_rows.iterrows():
        row_parts = []
        for col in df.columns:
            val = row[col]
            val_str = str(val) if pd.notna(val) else "NULL"
            if len(val_str) > 50:
                val_str = val_str[:47] + "..."
            row_parts.append(f"{col}={val_str}")
        sample_lines.append(f"  Row{idx}: {', '.join(row_parts)}")
    sample_text = "\n".join(sample_lines)

    prompt = (
        f"Table name: {table_name}\n"
        f"Source file: {file_name}\n"
        f"Row count: {len(df)}\n"
        f"Column info:\n{col_info}\n\n"
        f"Sample data (first 3 rows):\n{sample_text}"
    )

    return prompt


def _parse_llm_json(response: str) -> Optional[dict]:
    """
    LLM 응답에서 JSON을 추출하여 파싱.

    ```json ... ``` 코드블록이나 순수 JSON 형태를 모두 처리합니다.

    Args:
        response: LLM의 텍스트 응답.

    Returns:
        파싱된 딕셔너리 또는 실패 시 None.
    """
    if not response:
        return None

    # 1차: ```json ... ``` 코드블록 추출
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 2차: 중괄호로 감싸진 JSON 추출
    brace_match = re.search(r"\{.*\}", response, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    # 3차: 전체 텍스트를 JSON으로 시도
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        return None
