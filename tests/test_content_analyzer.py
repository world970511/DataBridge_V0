"""
콘텐츠 분석기 모듈 테스트.

watcher/content_analyzer.py의 분류 규칙과 유틸리티 함수를 테스트합니다.
순수 pandas 연산만 사용하므로 외부 서비스(DB, LLM) 불필요합니다.

실행:
    pytest tests/test_content_analyzer.py -v -m unit
"""

import pytest
import pandas as pd

from watcher.content_analyzer import (
    ContentAnalysis,
    analyze_dataframe,
    compute_numeric_ratio,
    compute_avg_text_length,
    check_has_long_text,
    classify_content,
    extract_sample_values,
)


# ============================================
# 픽스처
# ============================================

@pytest.fixture
def statistics_df():
    """숫자 비율이 높은 통계형 DataFrame (→ statistics)."""
    return pd.DataFrame({
        "id": range(1, 21),
        "amount": [i * 1000 for i in range(1, 21)],
        "quantity": [i * 5 for i in range(1, 21)],
        "product": [f"제품{i}" for i in range(1, 21)],
    })


@pytest.fixture
def document_df():
    """긴 텍스트가 포함된 문서형 DataFrame (→ document)."""
    # 각 description이 100자를 확실히 초과하도록 ASCII 텍스트 사용
    long_text_1 = "A" * 150  # 150자
    long_text_2 = "B" * 200  # 200자
    long_text_3 = "C" * 120  # 120자
    return pd.DataFrame({
        "test_name": ["test1", "test2", "test3"],
        "description": [long_text_1, long_text_2, long_text_3],
        "expected_result": ["pass", "pass", "fail"],
    })


@pytest.fixture
def reference_df():
    """행이 적고 텍스트 위주인 참조형 DataFrame (→ reference)."""
    return pd.DataFrame({
        "code": ["A", "B", "C"],
        "name": ["카테고리A", "카테고리B", "카테고리C"],
        "description": ["첫번째", "두번째", "세번째"],
    })


@pytest.fixture
def numeric_only_df():
    """숫자 컬럼만 있는 DataFrame."""
    return pd.DataFrame({
        "id": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "value": [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000],
        "count": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    })


# ============================================
# compute_numeric_ratio() 테스트
# ============================================

@pytest.mark.unit
class TestComputeNumericRatio:
    """compute_numeric_ratio() — 숫자 컬럼 비율 계산 테스트."""

    def test_all_numeric(self, numeric_only_df):
        """모든 컬럼이 숫자면 비율 1.0을 반환합니다."""
        ratio = compute_numeric_ratio(numeric_only_df)
        assert ratio == 1.0

    def test_mixed_columns(self, statistics_df):
        """숫자와 텍스트가 혼합된 경우 올바른 비율을 계산합니다."""
        ratio = compute_numeric_ratio(statistics_df)
        # id(int), amount(int), quantity(int), product(object) → 3/4 = 0.75
        assert ratio == 0.75

    def test_no_numeric(self, reference_df):
        """텍스트만 있으면 비율 0.0을 반환합니다."""
        ratio = compute_numeric_ratio(reference_df)
        assert ratio == 0.0

    def test_empty_dataframe(self):
        """빈 DataFrame은 0.0을 반환합니다."""
        df = pd.DataFrame()
        ratio = compute_numeric_ratio(df)
        assert ratio == 0.0

    def test_bool_counts_as_numeric(self):
        """불리언 컬럼도 숫자형으로 카운트됩니다."""
        df = pd.DataFrame({
            "active": [True, False],
            "name": ["a", "b"],
        })
        ratio = compute_numeric_ratio(df)
        assert ratio == 0.5


# ============================================
# compute_avg_text_length() 테스트
# ============================================

@pytest.mark.unit
class TestComputeAvgTextLength:
    """compute_avg_text_length() — 텍스트 평균 길이 계산 테스트."""

    def test_short_text(self, reference_df):
        """짧은 텍스트의 평균 길이를 올바르게 계산합니다."""
        avg_len = compute_avg_text_length(reference_df)
        assert avg_len > 0
        assert avg_len < 50  # 짧은 텍스트이므로

    def test_long_text(self, document_df):
        """긴 텍스트의 평균 길이가 큽니다."""
        avg_len = compute_avg_text_length(document_df)
        assert avg_len > 20  # description 컬럼에 긴 텍스트

    def test_no_text_columns(self, numeric_only_df):
        """텍스트 컬럼이 없으면 0.0을 반환합니다."""
        avg_len = compute_avg_text_length(numeric_only_df)
        assert avg_len == 0.0

    def test_with_nan_values(self):
        """NaN 값은 제외하고 계산합니다."""
        df = pd.DataFrame({"text": ["hello", None, "world"]})
        avg_len = compute_avg_text_length(df)
        assert avg_len == 5.0  # "hello"=5, "world"=5, 평균=5


# ============================================
# check_has_long_text() 테스트
# ============================================

@pytest.mark.unit
class TestCheckHasLongText:
    """check_has_long_text() — 긴 텍스트 존재 여부 확인 테스트."""

    def test_has_long_text(self, document_df):
        """100자 초과 텍스트가 있으면 True를 반환합니다."""
        assert check_has_long_text(document_df) is True

    def test_no_long_text(self, reference_df):
        """100자 초과 텍스트가 없으면 False를 반환합니다."""
        assert check_has_long_text(reference_df) is False

    def test_custom_threshold(self):
        """커스텀 threshold를 지정할 수 있습니다."""
        df = pd.DataFrame({"text": ["a" * 60]})
        assert check_has_long_text(df, threshold=50) is True
        assert check_has_long_text(df, threshold=100) is False

    def test_no_text_columns(self, numeric_only_df):
        """텍스트 컬럼이 없으면 False를 반환합니다."""
        assert check_has_long_text(numeric_only_df) is False


# ============================================
# classify_content() 테스트
# ============================================

@pytest.mark.unit
class TestClassifyContent:
    """classify_content() — 분류 규칙 테스트."""

    def test_statistics_high_numeric_many_rows(self):
        """숫자 비율 높고 행 많으면 statistics로 분류됩니다."""
        analysis = ContentAnalysis(
            numeric_ratio=0.7, avg_text_length=10, row_count=50,
            has_long_text=False, data_category="",
        )
        assert classify_content(analysis) == "statistics"

    def test_statistics_boundary(self):
        """숫자 비율 0.5, 행 수 10은 정확히 statistics 경계입니다."""
        analysis = ContentAnalysis(
            numeric_ratio=0.5, avg_text_length=10, row_count=10,
            has_long_text=False, data_category="",
        )
        assert classify_content(analysis) == "statistics"

    def test_document_long_text(self):
        """긴 텍스트가 있으면 document로 분류됩니다."""
        analysis = ContentAnalysis(
            numeric_ratio=0.2, avg_text_length=30, row_count=5,
            has_long_text=True, data_category="",
        )
        assert classify_content(analysis) == "document"

    def test_document_high_avg_text(self):
        """텍스트 평균 길이 > 50이면 document로 분류됩니다."""
        analysis = ContentAnalysis(
            numeric_ratio=0.3, avg_text_length=60, row_count=20,
            has_long_text=False, data_category="",
        )
        assert classify_content(analysis) == "document"

    def test_reference_few_rows_text_heavy(self):
        """행 수 < 5이고 숫자 비율 < 0.3이면 reference로 분류됩니다."""
        analysis = ContentAnalysis(
            numeric_ratio=0.1, avg_text_length=20, row_count=3,
            has_long_text=False, data_category="",
        )
        assert classify_content(analysis) == "reference"

    def test_default_statistics(self):
        """어떤 규칙에도 해당하지 않으면 기본값 statistics입니다."""
        analysis = ContentAnalysis(
            numeric_ratio=0.4, avg_text_length=30, row_count=8,
            has_long_text=False, data_category="",
        )
        assert classify_content(analysis) == "statistics"

    def test_statistics_overrides_document(self):
        """Rule 1(statistics)이 Rule 2(document)보다 우선합니다."""
        # 숫자 비율 높고 행 많지만 텍스트도 길 때 → statistics 우선
        analysis = ContentAnalysis(
            numeric_ratio=0.6, avg_text_length=60, row_count=20,
            has_long_text=True, data_category="",
        )
        assert classify_content(analysis) == "statistics"


# ============================================
# analyze_dataframe() 통합 테스트
# ============================================

@pytest.mark.unit
class TestAnalyzeDataframe:
    """analyze_dataframe() — 전체 분석 파이프라인 테스트."""

    def test_statistics_dataframe(self, statistics_df):
        """통계형 DataFrame은 'statistics'로 분류됩니다."""
        result = analyze_dataframe(statistics_df)
        assert result.data_category == "statistics"
        assert result.numeric_ratio >= 0.5
        assert result.row_count == 20

    def test_document_dataframe(self, document_df):
        """문서형 DataFrame은 'document'로 분류됩니다."""
        result = analyze_dataframe(document_df)
        assert result.data_category == "document"
        assert result.has_long_text is True

    def test_reference_dataframe(self, reference_df):
        """참조형 DataFrame은 'reference'로 분류됩니다."""
        result = analyze_dataframe(reference_df)
        assert result.data_category == "reference"
        assert result.row_count < 5
        assert result.numeric_ratio < 0.3

    def test_empty_dataframe(self):
        """빈 DataFrame은 기본값 'statistics'로 분류됩니다."""
        result = analyze_dataframe(pd.DataFrame())
        assert result.data_category == "statistics"
        assert result.row_count == 0

    def test_none_dataframe(self):
        """None 입력도 기본값 'statistics'로 처리됩니다."""
        result = analyze_dataframe(None)
        assert result.data_category == "statistics"


# ============================================
# extract_sample_values() 테스트
# ============================================

@pytest.mark.unit
class TestExtractSampleValues:
    """extract_sample_values() — 샘플 값 추출 테스트."""

    def test_basic_extraction(self, statistics_df):
        """각 컬럼에서 샘플 값을 추출합니다."""
        samples = extract_sample_values(statistics_df)
        assert "id" in samples
        assert "product" in samples
        assert len(samples["id"]) <= 5

    def test_max_values_limit(self):
        """max_values 파라미터로 샘플 수를 제한합니다."""
        df = pd.DataFrame({"col": list(range(100))})
        samples = extract_sample_values(df, max_values=3)
        assert len(samples["col"]) == 3

    def test_string_conversion(self, statistics_df):
        """모든 샘플 값이 문자열로 변환됩니다."""
        samples = extract_sample_values(statistics_df)
        for col, values in samples.items():
            for v in values:
                assert isinstance(v, str)

    def test_empty_dataframe(self):
        """빈 DataFrame은 빈 딕셔너리를 반환합니다."""
        samples = extract_sample_values(pd.DataFrame())
        assert samples == {}

    def test_none_dataframe(self):
        """None 입력은 빈 딕셔너리를 반환합니다."""
        samples = extract_sample_values(None)
        assert samples == {}
