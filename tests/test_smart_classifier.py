"""
스마트 분류기 통합 테스트.

watcher/classifier.py의 스마트 라우팅 로직을 테스트합니다.
DB, LLM, ChromaDB 호출은 mock 처리하여 분류 및 라우팅 로직만 검증합니다.

실행:
    pytest tests/test_smart_classifier.py -v -m unit
"""

from unittest.mock import patch, MagicMock, ANY
from pathlib import Path

import pytest
import pandas as pd

from watcher.classifier import (
    get_file_action,
    get_file_type,
    classify_file,
    _route_to_db_loader,
    _read_dataframe_for_analysis,
)


# ============================================
# 픽스처
# ============================================

@pytest.fixture
def statistics_csv(tmp_path):
    """통계형 CSV 파일 (숫자 비율 높음)."""
    content = "id,amount,quantity,product\n"
    for i in range(1, 21):
        content += f"{i},{i*1000},{i*5},제품{i}\n"
    csv_path = tmp_path / "statistics.csv"
    csv_path.write_text(content, encoding="utf-8")
    return str(csv_path)


@pytest.fixture
def document_csv(tmp_path):
    """문서형 CSV 파일 (긴 텍스트 포함)."""
    content = (
        "test_name,description,expected_result\n"
        "로그인 테스트,"
        "사용자가 올바른 이메일과 비밀번호를 입력하여 시스템에 정상적으로 로그인할 수 있는지 "
        "확인하는 테스트 케이스입니다. 다양한 브라우저 환경에서도 동작해야 합니다.,"
        "로그인 성공\n"
        "로그아웃 테스트,"
        "로그인 상태에서 로그아웃 버튼을 클릭하면 세션이 종료되고 로그인 페이지로 "
        "리다이렉트되는지 확인합니다. 세션 쿠키가 정상적으로 삭제되는지도 검증합니다.,"
        "로그아웃 후 리다이렉트\n"
    )
    csv_path = tmp_path / "test_cases.csv"
    csv_path.write_text(content, encoding="utf-8")
    return str(csv_path)


@pytest.fixture
def reference_csv(tmp_path):
    """참조형 CSV 파일 (행 적고 텍스트 위주)."""
    content = "code,name,description\nA,카테고리A,첫번째\nB,카테고리B,두번째\n"
    csv_path = tmp_path / "categories.csv"
    csv_path.write_text(content, encoding="utf-8")
    return str(csv_path)


# ============================================
# get_file_action() / get_file_type() 테스트
# ============================================

@pytest.mark.unit
class TestFileClassification:
    """파일 확장자 기반 1차 분류 테스트."""

    def test_csv_action(self):
        """CSV 파일은 load_to_db 액션입니다."""
        assert get_file_action("data.csv") == "load_to_db"

    def test_xlsx_action(self):
        """Excel 파일은 load_to_db 액션입니다."""
        assert get_file_action("data.xlsx") == "load_to_db"

    def test_pdf_action(self):
        """PDF 파일은 register_for_search 액션입니다."""
        assert get_file_action("doc.pdf") == "register_for_search"

    def test_tmp_ignored(self):
        """임시 파일은 ignore 액션입니다."""
        assert get_file_action("~$temp.xlsx") == "ignore"

    def test_csv_file_type(self):
        """CSV 확장자는 'csv' 타입을 반환합니다."""
        assert get_file_type("data.csv") == "csv"

    def test_tsv_file_type(self):
        """TSV 확장자는 'csv' 타입을 반환합니다."""
        assert get_file_type("data.tsv") == "csv"

    def test_xlsx_file_type(self):
        """XLSX 확장자는 'excel' 타입을 반환합니다."""
        assert get_file_type("data.xlsx") == "excel"

    def test_unknown_file_type(self):
        """알 수 없는 확장자는 'unknown'을 반환합니다."""
        assert get_file_type("data.xyz") == "unknown"


# ============================================
# _read_dataframe_for_analysis() 테스트
# ============================================

@pytest.mark.unit
class TestReadDataframeForAnalysis:
    """_read_dataframe_for_analysis() — 분석용 DataFrame 읽기 테스트."""

    def test_read_csv(self, statistics_csv):
        """CSV 파일을 DataFrame으로 읽습니다."""
        df = _read_dataframe_for_analysis(statistics_csv, "csv")
        assert df is not None
        assert len(df) == 20

    def test_read_csv_max_rows(self, statistics_csv):
        """max_rows 파라미터로 읽을 행 수를 제한합니다."""
        df = _read_dataframe_for_analysis(statistics_csv, "csv", max_rows=5)
        assert df is not None
        assert len(df) == 5

    def test_unsupported_type_returns_none(self, statistics_csv):
        """지원하지 않는 파일 타입은 None을 반환합니다."""
        df = _read_dataframe_for_analysis(statistics_csv, "json")
        assert df is None

    def test_invalid_path_returns_none(self):
        """존재하지 않는 파일 경로는 None을 반환합니다."""
        df = _read_dataframe_for_analysis("/nonexistent/file.csv", "csv")
        assert df is None


# ============================================
# 스마트 라우팅 통합 테스트
# ============================================

@pytest.mark.unit
class TestSmartRouting:
    """스마트 분류기의 라우팅 로직 통합 테스트."""

    @patch("watcher.classifier._load_to_db_directly")
    @patch("watcher.classifier._route_spreadsheet_as_document")
    def test_statistics_routes_to_db(
        self, mock_doc_route, mock_db_route, statistics_csv
    ):
        """통계형 CSV는 DB로 라우팅됩니다."""
        _route_to_db_loader(statistics_csv, "csv")

        mock_db_route.assert_called_once()
        mock_doc_route.assert_not_called()

        # data_category 확인
        call_kwargs = mock_db_route.call_args
        assert call_kwargs is not None

    @patch("watcher.classifier._load_to_db_directly")
    @patch("watcher.classifier._route_spreadsheet_as_document")
    def test_document_routes_to_chromadb(
        self, mock_doc_route, mock_db_route, document_csv
    ):
        """문서형 CSV는 ChromaDB로 라우팅됩니다."""
        _route_to_db_loader(document_csv, "csv")

        mock_doc_route.assert_called_once()
        mock_db_route.assert_not_called()

    @patch("watcher.classifier._load_to_db_directly")
    @patch("watcher.classifier._route_spreadsheet_as_document")
    def test_reference_routes_to_chromadb(
        self, mock_doc_route, mock_db_route, reference_csv
    ):
        """참조형 CSV는 ChromaDB로 라우팅됩니다."""
        _route_to_db_loader(reference_csv, "csv")

        mock_doc_route.assert_called_once()
        mock_db_route.assert_not_called()

    @patch("watcher.classifier._load_to_db_directly")
    def test_analysis_failure_falls_back_to_db(self, mock_db_route):
        """분석 실패 시 기본 DB 적재로 폴백합니다."""
        # 존재하지 않는 파일 → DataFrame 읽기 실패 → 폴백
        _route_to_db_loader("/nonexistent/file.csv", "csv")

        mock_db_route.assert_called_once()


# ============================================
# classify_file() 전체 흐름 테스트
# ============================================

@pytest.mark.unit
class TestClassifyFile:
    """classify_file() — 전체 분류 흐름 테스트."""

    @patch("watcher.classifier._route_to_db_loader")
    def test_csv_triggers_db_routing(self, mock_route, statistics_csv):
        """CSV 파일은 DB 라우팅 함수를 호출합니다."""
        classify_file(statistics_csv)
        mock_route.assert_called_once_with(statistics_csv, "csv")

    @patch("watcher.classifier._route_to_doc_loader")
    def test_pdf_triggers_doc_routing(self, mock_route, tmp_path):
        """PDF 파일은 문서 라우팅 함수를 호출합니다."""
        pdf_path = str(tmp_path / "test.pdf")
        Path(pdf_path).write_bytes(b"%PDF-1.4")
        classify_file(pdf_path)
        mock_route.assert_called_once_with(pdf_path, "pdf")

    def test_tmp_file_ignored(self, tmp_path):
        """임시 파일은 아무 로더도 호출하지 않습니다."""
        tmp_file = str(tmp_path / "~$temp.xlsx")
        Path(tmp_file).write_bytes(b"")
        # 예외 없이 정상 종료되면 성공
        classify_file(tmp_file)
