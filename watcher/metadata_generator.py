"""
DataFrame에서 Rich Catalog 메타데이터를 생성하는 모듈.

2단계 메타데이터 전략:
    1단계 (즉시): 파일명·컬럼명·통계 기반 규칙으로 description/tags 즉시 생성
    2단계 (백그라운드): LLM 호출로 고품질 description/tags/column_descriptions 보강

적재 시에는 1단계만 실행하여 빠르게 카탈로그에 등록하고,
별도 백그라운드 스레드가 등록된 테이블들의 LLM 메타데이터를 순차 보강합니다.

의존 모듈:
    - watcher.content_analyzer: compute_numeric_ratio(), compute_avg_text_length(),
                                extract_sample_values()
    - agent._llm: generate() — LLM 기반 보강 (2단계)
"""

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RichMetadata:
    """Rich Catalog에 저장할 메타데이터 묶음.

    Attributes:
        description: 데이터 설명 (규칙 기반 즉시 생성 → LLM 보강).
        data_category: 데이터 카테고리 (content_analyzer에서 전달).
        tags: 검색용 태그 리스트 (규칙 기반 즉시 생성 → LLM 보강).
        column_descriptions: 컬럼별 설명 (LLM 보강 시 생성, 초기에는 None).
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


# ── LLM 백그라운드 보강 큐 ──
_enrich_queue: list[dict] = []
_enrich_lock = threading.Lock()
_enrich_thread: Optional[threading.Thread] = None


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


# ============================================================
# 1단계: 규칙 기반 즉시 메타데이터 생성
# ============================================================


def generate_rich_metadata(
    df: pd.DataFrame,
    table_name: str,
    source_file: str,
    data_category: str = "statistics",
) -> RichMetadata:
    """
    DataFrame에서 Rich Catalog 메타데이터를 즉시 생성 (LLM 호출 없음).

    규칙 기반으로 description과 tags를 생성하고,
    LLM 보강은 별도 백그라운드 큐에 등록합니다.

    Args:
        df: 대상 DataFrame.
        table_name: 테이블명.
        source_file: 원본 파일 경로.
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

    # 규칙 기반 description + tags (즉시, LLM 없음)
    description = _generate_rule_description(df, table_name, source_file, data_category)
    tags = _generate_rule_tags(df, table_name, source_file)

    # LLM 보강 큐에 등록 (백그라운드에서 나중에 처리)
    _enqueue_llm_enrich(df, table_name, source_file)

    logger.info(
        f"Rule-based metadata for {table_name}: "
        f"desc={len(description)}chars, tags={len(tags)}"
    )

    return RichMetadata(
        description=description,
        data_category=data_category,
        tags=tags,
        column_descriptions=None,  # LLM 보강 시 채워짐
        sample_values=samples,
        numeric_ratio=n_ratio,
        avg_text_length=avg_tl,
    )


def _generate_rule_description(
    df: pd.DataFrame, table_name: str, source_file: str, data_category: str
) -> str:
    """
    파일명·컬럼명·통계로부터 규칙 기반 설명을 즉시 생성.

    파일명에서 키워드를 추출하고, 컬럼 수·행 수·카테고리를 조합하여
    "파일명 기반 데이터 (N행 x M컬럼, 통계형)" 형식으로 생성합니다.
    """
    file_name = Path(source_file).stem

    # 파일명에서 특수문자/언더스코어를 공백으로 변환
    clean_name = re.sub(r"[_\-]+", " ", file_name).strip()

    category_label = {
        "statistics": "통계/수치 데이터",
        "document": "문서형 데이터",
        "reference": "참조 데이터",
        "log": "로그 데이터",
    }.get(data_category, "데이터")

    # 주요 컬럼명 추출 (최대 5개)
    col_names = [str(c) for c in df.columns[:5]]
    col_hint = ", ".join(col_names)
    if len(df.columns) > 5:
        col_hint += f" 외 {len(df.columns) - 5}개"

    return f"{clean_name} ({category_label}, {len(df)}행 x {len(df.columns)}컬럼: {col_hint})"


def _generate_rule_tags(
    df: pd.DataFrame, table_name: str, source_file: str
) -> list[str]:
    """
    파일명·컬럼명에서 규칙 기반 검색 태그를 즉시 생성.

    1) 파일명을 공백/언더스코어로 분할하여 의미 있는 토큰 추출
    2) 컬럼명에서 한글/영문 키워드 추출
    3) 중복 제거 후 최대 10개 반환
    """
    tags = set()

    # 파일명에서 토큰 추출
    file_name = Path(source_file).stem
    tokens = re.split(r"[_\-\s\.\+]+", file_name)
    for token in tokens:
        token = token.strip()
        # 숫자만으로 된 토큰이나 2자 미만은 제외
        if len(token) >= 2 and not token.isdigit():
            tags.add(token.lower())

    # 컬럼명에서 키워드 추출
    for col in df.columns:
        col_str = str(col).strip()
        if len(col_str) >= 2:
            # 한글 포함 컬럼명은 그대로 태그로
            if re.search(r"[가-힣]", col_str):
                tags.add(col_str)
            else:
                # 영문은 소문자로
                tags.add(col_str.lower())

    return sorted(tags)[:10]


# ============================================================
# 2단계: LLM 백그라운드 보강
# ============================================================


def _enqueue_llm_enrich(df: pd.DataFrame, table_name: str, source_file: str):
    """LLM 메타데이터 보강 요청을 큐에 추가하고 워커 스레드를 시작."""
    # DataFrame에서 필요한 최소 정보만 보존 (메모리 절약)
    prompt = _build_llm_prompt(df, table_name, source_file)

    with _enrich_lock:
        _enrich_queue.append({
            "table_name": table_name,
            "prompt": prompt,
        })

    _ensure_enrich_worker()


def _ensure_enrich_worker():
    """백그라운드 보강 워커 스레드가 실행 중이 아니면 시작."""
    global _enrich_thread
    if _enrich_thread and _enrich_thread.is_alive():
        return

    _enrich_thread = threading.Thread(
        target=_enrich_worker_loop,
        name="metadata-enrich",
        daemon=True,
    )
    _enrich_thread.start()


def _enrich_worker_loop():
    """큐에서 하나씩 꺼내 LLM 메타데이터를 생성하고 catalog_tables를 갱신."""
    logger.info("LLM metadata enrichment worker started")

    while True:
        with _enrich_lock:
            if not _enrich_queue:
                logger.info("LLM metadata enrichment worker finished (queue empty)")
                return
            item = _enrich_queue.pop(0)

        table_name = item["table_name"]
        prompt = item["prompt"]

        try:
            description, tags, col_descs = _call_llm_metadata(prompt, table_name)

            if description or tags or col_descs:
                _update_catalog_metadata(table_name, description, tags, col_descs)
                logger.info(
                    f"LLM enriched: {table_name} "
                    f"(desc={'yes' if description else 'no'}, "
                    f"tags={len(tags) if tags else 0}, "
                    f"col_descs={len(col_descs) if col_descs else 0})"
                )
            else:
                logger.warning(f"LLM enrichment returned empty for: {table_name}")

        except Exception as e:
            logger.warning(f"LLM enrichment failed for {table_name}: {e}")


def _call_llm_metadata(prompt: str, table_name: str) -> tuple:
    """
    단일 LLM 호출로 description, tags, column_descriptions를 생성.

    Returns:
        (description, tags, column_descriptions) 튜플.
        실패 시 (None, None, None).
    """
    try:
        from agent._llm import generate

        response = generate(
            prompt=prompt,
            system=_METADATA_SYSTEM_PROMPT,
            purpose="agent",
            temperature=0.1,
        )

        if not response:
            return (None, None, None)

        parsed = _parse_llm_json(response)
        if not parsed:
            logger.warning(f"Failed to parse LLM metadata JSON: {table_name}")
            return (None, None, None)

        description = parsed.get("description")
        tags = parsed.get("tags")
        col_descs = parsed.get("column_descriptions")

        if tags and not isinstance(tags, list):
            tags = None
        if col_descs and not isinstance(col_descs, dict):
            col_descs = None

        return (description, tags, col_descs)

    except Exception as e:
        logger.warning(f"LLM metadata call failed for {table_name}: {e}")
        return (None, None, None)


def _update_catalog_metadata(
    table_name: str,
    description: Optional[str],
    tags: Optional[list],
    column_descriptions: Optional[dict],
):
    """catalog_tables의 메타데이터를 LLM 결과로 갱신."""
    from db.connection import execute_command

    updates = []
    params = []

    if description:
        updates.append("description = %s")
        params.append(description)
    if tags:
        updates.append("tags = %s")
        params.append(json.dumps(tags, ensure_ascii=False))
    if column_descriptions:
        updates.append("column_descriptions = %s")
        params.append(json.dumps(column_descriptions, ensure_ascii=False))

    if not updates:
        return

    updates.append("updated_at = NOW()")
    params.append(table_name)

    try:
        execute_command(
            f"UPDATE catalog_tables SET {', '.join(updates)} WHERE table_name = %s",
            tuple(params),
        )
    except Exception as e:
        logger.error(f"Failed to update catalog metadata for {table_name}: {e}")


# ============================================================
# 공통 유틸리티
# ============================================================


def _build_llm_prompt(df: pd.DataFrame, table_name: str, source_file: str) -> str:
    """
    LLM 메타데이터 생성을 위한 프롬프트를 구성.

    토큰 절약을 위해 컬럼명, dtype, 처음 3행의 샘플 데이터만 포함합니다.
    """
    file_name = Path(source_file).name

    col_info_lines = []
    for col in df.columns:
        col_info_lines.append(f"  - {col} ({df[col].dtype})")
    col_info = "\n".join(col_info_lines)

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

    return (
        f"Table name: {table_name}\n"
        f"Source file: {file_name}\n"
        f"Row count: {len(df)}\n"
        f"Column info:\n{col_info}\n\n"
        f"Sample data (first 3 rows):\n{sample_text}"
    )


def _parse_llm_json(response: str) -> Optional[dict]:
    """
    LLM 응답에서 JSON을 추출하여 파싱.

    ```json ... ``` 코드블록이나 순수 JSON 형태를 모두 처리합니다.
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
