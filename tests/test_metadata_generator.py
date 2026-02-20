"""
메타데이터 생성기 모듈 테스트.

watcher/metadata_generator.py의 Rich Metadata 생성 로직을 테스트합니다.
LLM 호출은 mock 처리하여 외부 의존성 없이 테스트합니다.

실행:
    pytest tests/test_metadata_generator.py -v -m unit
"""

import json
from unittest.mock import patch, MagicMock

import pytest
import pandas as pd

from watcher.metadata_generator import (
    RichMetadata,
    generate_rich_metadata,
    _build_llm_prompt,
    _parse_llm_json,
)


# ============================================
# 픽스처
# ============================================

@pytest.fixture
def sample_df():
    """메타데이터 생성 테스트용 DataFrame."""
    return pd.DataFrame({
        "id": [1, 2, 3],
        "product_name": ["노트북", "모니터", "키보드"],
        "amount": [1200000, 450000, 85000],
        "sale_date": ["2025-01-15", "2025-01-16", "2025-01-17"],
    })


@pytest.fixture
def llm_json_response():
    """LLM이 반환하는 메타데이터 JSON 형식."""
    return json.dumps({
        "description": "2025년 1월 제품별 매출 데이터",
        "tags": ["매출", "제품", "월별"],
        "column_descriptions": {
            "id": "고유 식별자",
            "product_name": "제품명",
            "amount": "매출액 (원)",
            "sale_date": "판매일자",
        },
    })


# ============================================
# _parse_llm_json() 테스트
# ============================================

@pytest.mark.unit
class TestParseLlmJson:
    """_parse_llm_json() — LLM 응답 JSON 파싱 테스트."""

    def test_pure_json(self):
        """순수 JSON 문자열을 파싱합니다."""
        data = '{"description": "테스트", "tags": ["a", "b"]}'
        result = _parse_llm_json(data)
        assert result["description"] == "테스트"
        assert result["tags"] == ["a", "b"]

    def test_json_code_block(self):
        """```json 코드블록 안의 JSON을 추출합니다."""
        data = '```json\n{"description": "테스트"}\n```'
        result = _parse_llm_json(data)
        assert result["description"] == "테스트"

    def test_json_with_surrounding_text(self):
        """주변 텍스트가 있어도 JSON을 추출합니다."""
        data = '다음은 결과입니다:\n{"description": "테스트"}\n이상입니다.'
        result = _parse_llm_json(data)
        assert result["description"] == "테스트"

    def test_empty_string(self):
        """빈 문자열은 None을 반환합니다."""
        assert _parse_llm_json("") is None

    def test_none_input(self):
        """None 입력은 None을 반환합니다."""
        assert _parse_llm_json(None) is None

    def test_invalid_json(self):
        """유효하지 않은 JSON은 None을 반환합니다."""
        assert _parse_llm_json("이것은 JSON이 아닙니다") is None

    def test_bare_code_block(self):
        """``` 코드블록(json 없이)도 파싱합니다."""
        data = '```\n{"description": "테스트"}\n```'
        result = _parse_llm_json(data)
        assert result["description"] == "테스트"


# ============================================
# _build_llm_prompt() 테스트
# ============================================

@pytest.mark.unit
class TestBuildLlmPrompt:
    """_build_llm_prompt() — LLM 프롬프트 생성 테스트."""

    def test_includes_table_name(self, sample_df):
        """프롬프트에 테이블명이 포함됩니다."""
        prompt = _build_llm_prompt(sample_df, "sales", "/data/sales.csv")
        assert "sales" in prompt

    def test_includes_file_name(self, sample_df):
        """프롬프트에 파일명이 포함됩니다."""
        prompt = _build_llm_prompt(sample_df, "sales", "/data/sales.csv")
        assert "sales.csv" in prompt

    def test_includes_column_names(self, sample_df):
        """프롬프트에 모든 컬럼명이 포함됩니다."""
        prompt = _build_llm_prompt(sample_df, "sales", "/data/sales.csv")
        assert "id" in prompt
        assert "product_name" in prompt
        assert "amount" in prompt

    def test_includes_row_count(self, sample_df):
        """프롬프트에 행 수가 포함됩니다."""
        prompt = _build_llm_prompt(sample_df, "sales", "/data/sales.csv")
        assert "3" in prompt  # 3행

    def test_includes_sample_data(self, sample_df):
        """프롬프트에 샘플 데이터가 포함됩니다."""
        prompt = _build_llm_prompt(sample_df, "sales", "/data/sales.csv")
        assert "노트북" in prompt or "모니터" in prompt


# ============================================
# generate_rich_metadata() 테스트
# ============================================

@pytest.mark.unit
class TestGenerateRichMetadata:
    """generate_rich_metadata() — Rich Metadata 생성 통합 테스트."""

    @patch("agent._llm.generate")
    def test_successful_generation(self, mock_gen, sample_df, llm_json_response):
        """LLM 호출 성공 시 모든 필드가 채워진 RichMetadata를 반환합니다."""
        mock_gen.return_value = llm_json_response

        result = generate_rich_metadata(sample_df, "sales", "/data/sales.csv")

        assert isinstance(result, RichMetadata)
        assert result.description == "2025년 1월 제품별 매출 데이터"
        assert result.tags == ["매출", "제품", "월별"]
        assert "product_name" in result.column_descriptions
        assert result.data_category == "statistics"  # 기본값
        assert result.numeric_ratio > 0
        assert isinstance(result.sample_values, dict)

    @patch("agent._llm.generate")
    def test_llm_failure_still_returns_metrics(self, mock_gen, sample_df):
        """LLM 호출 실패 시에도 계산 메트릭은 정상 반환됩니다."""
        mock_gen.side_effect = Exception("LLM connection error")

        result = generate_rich_metadata(sample_df, "sales", "/data/sales.csv")

        assert isinstance(result, RichMetadata)
        assert result.description is None
        assert result.tags is None
        assert result.column_descriptions is None
        # 계산 메트릭은 정상
        assert result.numeric_ratio > 0
        assert isinstance(result.sample_values, dict)
        assert len(result.sample_values) > 0

    @patch("agent._llm.generate")
    def test_llm_empty_response(self, mock_gen, sample_df):
        """LLM이 빈 응답을 반환해도 안전하게 처리됩니다."""
        mock_gen.return_value = ""

        result = generate_rich_metadata(sample_df, "sales", "/data/sales.csv")

        assert isinstance(result, RichMetadata)
        assert result.description is None
        assert result.tags is None

    @patch("agent._llm.generate")
    def test_llm_invalid_json(self, mock_gen, sample_df):
        """LLM이 유효하지 않은 JSON을 반환해도 안전합니다."""
        mock_gen.return_value = "이것은 JSON이 아닙니다"

        result = generate_rich_metadata(sample_df, "sales", "/data/sales.csv")

        assert isinstance(result, RichMetadata)
        assert result.description is None

    @patch("agent._llm.generate")
    def test_data_category_passed_through(self, mock_gen, sample_df, llm_json_response):
        """data_category 파라미터가 결과에 그대로 전달됩니다."""
        mock_gen.return_value = llm_json_response

        result = generate_rich_metadata(
            sample_df, "sales", "/data/sales.csv", data_category="document"
        )

        assert result.data_category == "document"

    @patch("agent._llm.generate")
    def test_invalid_tags_type_ignored(self, mock_gen, sample_df):
        """LLM이 tags를 리스트가 아닌 타입으로 반환하면 None으로 처리됩니다."""
        mock_gen.return_value = json.dumps({
            "description": "테스트",
            "tags": "매출, 제품",  # 문자열 — 잘못된 타입
            "column_descriptions": {},
        })

        result = generate_rich_metadata(sample_df, "sales", "/data/sales.csv")

        assert result.description == "테스트"
        assert result.tags is None  # 잘못된 타입은 None 처리

    @patch("agent._llm.generate")
    def test_invalid_col_descs_type_ignored(self, mock_gen, sample_df):
        """LLM이 column_descriptions를 dict가 아닌 타입으로 반환하면 None으로 처리됩니다."""
        mock_gen.return_value = json.dumps({
            "description": "테스트",
            "tags": ["a"],
            "column_descriptions": ["잘못된 형식"],  # 리스트 — 잘못된 타입
        })

        result = generate_rich_metadata(sample_df, "sales", "/data/sales.csv")

        assert result.column_descriptions is None
