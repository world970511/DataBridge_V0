"""
CSV/Excel 파일의 내용을 분석하여 데이터 카테고리를 결정하는 모듈.

DataFrame의 통계적 특성(숫자 비율, 텍스트 길이, 행 수 등)을 분석하여
정형 데이터(statistics)와 문서형 데이터(document/reference)를 구분합니다.
LLM 호출 없이 순수 휴리스틱으로 동작합니다.

분류 규칙:
    - 숫자 컬럼 비율 >= 0.5 AND 행 수 >= 10  → "statistics" (DB 적재)
    - 텍스트 평균 길이 > 50 OR 긴 텍스트 존재  → "document"  (ChromaDB)
    - 행 수 < 5 AND 숫자 비율 < 0.3           → "reference" (ChromaDB)
    - 기본값                                   → "statistics" (DB 적재)

의존 모듈:
    - pandas (DataFrame 분석)

사용 예시:
    from watcher.content_analyzer import analyze_dataframe

    df = pd.read_csv("test_cases.csv")
    result = analyze_dataframe(df)
    print(result.data_category)  # "document"
"""

import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ContentAnalysis:
    """DataFrame 내용 분석 결과를 담는 데이터클래스.

    Attributes:
        numeric_ratio: 숫자 컬럼(int/float/bool)의 비율 (0.0 ~ 1.0).
        avg_text_length: 텍스트(object) 컬럼들의 평균 셀 문자열 길이.
        row_count: DataFrame의 데이터 행 수.
        has_long_text: threshold(기본 100자) 초과 텍스트 셀 존재 여부.
        data_category: 최종 분류 결과 ("statistics"|"document"|"reference"|"log").
    """
    numeric_ratio: float
    avg_text_length: float
    row_count: int
    has_long_text: bool
    data_category: str


def analyze_dataframe(df: pd.DataFrame) -> ContentAnalysis:
    """
    DataFrame의 내용을 분석하여 ContentAnalysis 결과를 반환.

    숫자 비율, 텍스트 평균 길이, 긴 텍스트 존재 여부를 계산하고
    classify_content()로 데이터 카테고리를 결정합니다.

    Args:
        df: 분석 대상 pandas DataFrame.

    Returns:
        ContentAnalysis 데이터클래스 인스턴스.
    """
    if df is None or df.empty:
        return ContentAnalysis(
            numeric_ratio=0.0,
            avg_text_length=0.0,
            row_count=0,
            has_long_text=False,
            data_category="statistics",
        )

    n_ratio = compute_numeric_ratio(df)
    avg_tl = compute_avg_text_length(df)
    has_lt = check_has_long_text(df)
    row_count = len(df)

    analysis = ContentAnalysis(
        numeric_ratio=n_ratio,
        avg_text_length=avg_tl,
        row_count=row_count,
        has_long_text=has_lt,
        data_category="",  # classify_content에서 채움
    )
    analysis.data_category = classify_content(analysis)

    logger.debug(
        f"Content analysis: numeric_ratio={n_ratio:.2f}, "
        f"avg_text_length={avg_tl:.1f}, row_count={row_count}, "
        f"has_long_text={has_lt} → category={analysis.data_category}"
    )
    return analysis


def compute_numeric_ratio(df: pd.DataFrame) -> float:
    """
    DataFrame에서 숫자형 컬럼의 비율을 계산.

    int64, float64, bool, Int64 등 숫자/불리언 dtype 컬럼을 숫자형으로 간주합니다.
    컬럼이 0개이면 0.0을 반환합니다.

    Args:
        df: 분석 대상 DataFrame.

    Returns:
        0.0 ~ 1.0 사이의 숫자 컬럼 비율.
    """
    if df.columns.empty:
        return 0.0

    total_cols = len(df.columns)
    numeric_cols = len(df.select_dtypes(include=["number", "bool"]).columns)
    return numeric_cols / total_cols


def compute_avg_text_length(df: pd.DataFrame) -> float:
    """
    텍스트(object) 컬럼들의 평균 셀 문자열 길이를 계산.

    object dtype 컬럼만 대상으로 각 셀을 str()로 변환 후 길이를 측정하고,
    전체 평균을 반환합니다. NaN 값은 제외합니다.
    텍스트 컬럼이 없으면 0.0을 반환합니다.

    Args:
        df: 분석 대상 DataFrame.

    Returns:
        텍스트 셀의 평균 문자열 길이 (float).
    """
    text_cols = df.select_dtypes(include=["object"]).columns
    if len(text_cols) == 0:
        return 0.0

    lengths = []
    for col in text_cols:
        # NaN 제외 후 문자열 길이 측정
        non_null = df[col].dropna()
        if not non_null.empty:
            col_lengths = non_null.astype(str).str.len()
            lengths.extend(col_lengths.tolist())

    if not lengths:
        return 0.0

    return sum(lengths) / len(lengths)


def check_has_long_text(df: pd.DataFrame, threshold: int = 100) -> bool:
    """
    DataFrame에 threshold 글자 수를 초과하는 텍스트 셀이 있는지 확인.

    object dtype 컬럼의 NaN이 아닌 셀들을 검사합니다.

    Args:
        df: 분석 대상 DataFrame.
        threshold: 긴 텍스트 판별 기준 글자 수 (기본 100).

    Returns:
        True if 임계값을 초과하는 텍스트 셀이 하나라도 존재.
    """
    text_cols = df.select_dtypes(include=["object"]).columns
    if len(text_cols) == 0:
        return False

    for col in text_cols:
        non_null = df[col].dropna()
        if not non_null.empty:
            max_len = non_null.astype(str).str.len().max()
            if max_len > threshold:
                return True

    return False


def classify_content(analysis: ContentAnalysis) -> str:
    """
    ContentAnalysis 결과를 기반으로 데이터 카테고리를 결정.

    보수적 분류 전략: 기본값은 "statistics"(DB 적재)로,
    명확히 문서형 특성을 보이는 경우에만 "document"나 "reference"로 분류합니다.
    이를 통해 기존 동작(모든 CSV/Excel → DB)과의 호환성을 유지합니다.

    분류 규칙 (순서 중요):
        1. numeric_ratio >= 0.5 AND row_count >= 10 → "statistics"
        2. avg_text_length > 50 OR has_long_text    → "document"
        3. row_count < 5 AND numeric_ratio < 0.3    → "reference"
        4. 기본값                                   → "statistics"

    Args:
        analysis: ContentAnalysis 인스턴스.

    Returns:
        데이터 카테고리 문자열: "statistics", "document", "reference".
    """
    # Rule 1: 숫자 비율 높고 행 수 충분 → 통계형 데이터
    if analysis.numeric_ratio >= 0.5 and analysis.row_count >= 10:
        return "statistics"

    # Rule 2: 긴 텍스트 존재 또는 텍스트 평균이 길면 → 문서형
    if analysis.avg_text_length > 50 or analysis.has_long_text:
        return "document"

    # Rule 3: 행이 매우 적고 대부분 텍스트 → 참조 테이블/용어집
    if analysis.row_count < 5 and analysis.numeric_ratio < 0.3:
        return "reference"

    # 기본값: 통계형 (기존 동작 유지)
    return "statistics"


def extract_sample_values(
    df: pd.DataFrame, max_values: int = 5
) -> dict:
    """
    각 컬럼의 대표 샘플 값을 추출.

    각 컬럼에서 최대 max_values개의 고유 값을 추출하여 문자열로 변환합니다.
    NaN 값은 제외하며, 모든 값을 문자열로 통일합니다.

    Args:
        df: 대상 DataFrame.
        max_values: 컬럼당 최대 샘플 수 (기본 5).

    Returns:
        {"col_name": ["val1", "val2", ...], ...} 형태의 딕셔너리.
    """
    if df is None or df.empty:
        return {}

    result = {}
    for col in df.columns:
        try:
            # NaN 제외 후 고유 값 추출
            unique_vals = df[col].dropna().unique()
            # 최대 max_values개까지만
            sample = unique_vals[:max_values]
            # 모든 값을 문자열로 변환
            result[col] = [str(v) for v in sample]
        except Exception:
            # 변환 실패 시 해당 컬럼 건너뛰기
            continue

    return result
