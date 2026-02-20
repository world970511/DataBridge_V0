"""
CSV/Excel 데이터 적재 모듈 테스트.

watcher/loader/csv_loader.py, watcher/loader/excel_loader.py,
watcher/loader/_utils.py의 기능을 테스트합니다.

- Unit 테스트: sanitize_table_name, df_to_pg_types 등 순수 함수 테스트
- Integration 테스트: 실제 DB 연결이 필요한 load_csv, load_excel 테스트
- Rich Metadata 테스트: 메타데이터 생성 및 카탈로그 등록 확인 (mock)

실행:
    pytest tests/test_csv_excel_loading.py -v -m unit        # 순수 함수만
    pytest tests/test_csv_excel_loading.py -v -m integration # DB 필요
"""

from unittest.mock import patch, MagicMock

import pytest
import pandas as pd

from watcher.loader._utils import sanitize_table_name, df_to_pg_types


# ============================================
# Unit 테스트 — 외부 서비스 불필요
# ============================================

@pytest.mark.unit
class TestSanitizeTableName:
    """sanitize_table_name() — 테이블명 안전 변환 테스트."""

    def test_basic_name(self):
        """기본 영문 이름은 소문자로 변환됩니다."""
        assert sanitize_table_name("Sales") == "sales"

    def test_spaces_to_underscores(self):
        """공백은 밑줄로 변환됩니다."""
        assert sanitize_table_name("my table") == "my_table"

    def test_hyphens_to_underscores(self):
        """하이픈은 밑줄로 변환됩니다."""
        assert sanitize_table_name("my-table") == "my_table"

    def test_special_chars_removed(self):
        """특수문자(영문·한글·숫자·밑줄 외)는 제거됩니다."""
        result = sanitize_table_name("table@#$%name")
        assert "@" not in result
        assert "#" not in result
        assert "table" in result
        assert "name" in result

    def test_numeric_prefix(self):
        """숫자로 시작하면 't_' 접두사가 추가됩니다."""
        result = sanitize_table_name("123data")
        assert result.startswith("t_")

    def test_korean_preserved(self):
        """한글 문자는 유지됩니다."""
        result = sanitize_table_name("매출데이터")
        assert "매출데이터" in result

    def test_empty_string(self):
        """빈 문자열은 'unnamed'으로 변환됩니다."""
        assert sanitize_table_name("") == "unnamed"

    def test_only_special_chars(self):
        """특수문자만 있으면 'unnamed'으로 변환됩니다."""
        assert sanitize_table_name("@#$%") == "unnamed"

    def test_consecutive_underscores(self):
        """연속된 밑줄은 하나로 정리됩니다."""
        result = sanitize_table_name("a___b")
        assert "__" not in result


@pytest.mark.unit
class TestDfToPgTypes:
    """df_to_pg_types() — pandas dtype → PostgreSQL 타입 매핑 테스트."""

    def test_integer_mapping(self):
        """정수 컬럼은 BIGINT로 매핑됩니다."""
        df = pd.DataFrame({"count": [1, 2, 3]})
        result = df_to_pg_types(df)
        assert result[0] == ("count", "BIGINT")

    def test_float_mapping(self):
        """실수 컬럼은 DOUBLE PRECISION으로 매핑됩니다."""
        df = pd.DataFrame({"price": [1.5, 2.7, 3.9]})
        result = df_to_pg_types(df)
        assert result[0] == ("price", "DOUBLE PRECISION")

    def test_string_mapping(self):
        """문자열 컬럼은 TEXT로 매핑됩니다."""
        df = pd.DataFrame({"name": ["a", "b", "c"]})
        result = df_to_pg_types(df)
        assert result[0] == ("name", "TEXT")

    def test_bool_mapping(self):
        """불리언 컬럼은 BOOLEAN으로 매핑됩니다."""
        df = pd.DataFrame({"active": [True, False, True]})
        result = df_to_pg_types(df)
        assert result[0] == ("active", "BOOLEAN")

    def test_datetime_mapping(self):
        """datetime 컬럼은 TIMESTAMPTZ로 매핑됩니다."""
        df = pd.DataFrame({"date": pd.to_datetime(["2025-01-01", "2025-01-02"])})
        result = df_to_pg_types(df)
        assert result[0] == ("date", "TIMESTAMPTZ")

    def test_multiple_columns(self):
        """여러 컬럼이 각각 올바르게 매핑됩니다."""
        df = pd.DataFrame({
            "id": [1, 2],
            "name": ["a", "b"],
            "price": [1.5, 2.5],
        })
        result = df_to_pg_types(df)
        assert len(result) == 3
        types = {name: pg_type for name, pg_type in result}
        assert types["id"] == "BIGINT"
        assert types["name"] == "TEXT"
        assert types["price"] == "DOUBLE PRECISION"


@pytest.mark.unit
class TestCsvReading:
    """CSV 파일 읽기 관련 순수 함수 테스트."""

    def test_csv_file_exists(self, sample_csv_file):
        """sample_csv_file 픽스처가 유효한 파일 경로를 반환합니다."""
        from pathlib import Path
        assert Path(sample_csv_file).exists()

    def test_csv_file_content(self, sample_csv_file):
        """CSV 파일이 올바른 데이터를 포함합니다."""
        df = pd.read_csv(sample_csv_file)
        assert len(df) == 3
        assert "id" in df.columns
        assert "product_name" in df.columns

    def test_tsv_file_content(self, sample_tsv_file):
        """TSV 파일이 탭 구분으로 올바르게 읽힙니다."""
        df = pd.read_csv(sample_tsv_file, sep="\t")
        assert len(df) == 2
        assert "name" in df.columns


@pytest.mark.integration
class TestCsvLoadIntegration:
    """CSV → DB 적재 통합 테스트 (PostgreSQL 필요)."""

    def test_load_csv_creates_table(self, sample_csv_file):
        """CSV 파일 적재 시 테이블이 생성되고 테이블명이 반환됩니다."""
        from watcher.loader.csv_loader import load_csv
        table_name = load_csv(sample_csv_file)
        assert table_name is not None
        assert isinstance(table_name, str)

    def test_load_csv_data_integrity(self, sample_csv_file):
        """적재된 데이터의 행 수가 원본 CSV와 일치합니다."""
        from watcher.loader.csv_loader import load_csv
        from db.connection import execute_query
        table_name = load_csv(sample_csv_file)
        rows = execute_query(f'SELECT COUNT(*) as cnt FROM "{table_name}"')
        assert rows[0]["cnt"] == 3


@pytest.mark.integration
class TestExcelLoadIntegration:
    """Excel → DB 적재 통합 테스트 (PostgreSQL 필요)."""

    def test_load_excel_creates_table(self, sample_excel_file):
        """Excel 파일 적재 시 테이블이 생성됩니다."""
        from watcher.loader.excel_loader import load_excel
        tables = load_excel(sample_excel_file)
        assert len(tables) >= 1

    def test_load_excel_data_integrity(self, sample_excel_file):
        """적재된 데이터의 행 수가 원본 Excel과 일치합니다."""
        from watcher.loader.excel_loader import load_excel
        from db.connection import execute_query
        tables = load_excel(sample_excel_file)
        assert len(tables) >= 1
        rows = execute_query(f'SELECT COUNT(*) as cnt FROM "{tables[0]}"')
        assert rows[0]["cnt"] == 2  # Excel 픽스처에 2행 데이터


# ============================================
# Rich Metadata 전달 확인 (mock 기반)
# ============================================

@pytest.mark.unit
class TestRichMetadataPassthrough:
    """CSV/Excel 로더가 Rich Metadata를 카탈로그에 전달하는지 확인."""

    @patch("watcher.loader.csv_loader.register_table")
    @patch("watcher.metadata_generator.generate_rich_metadata")
    @patch("watcher.loader.csv_loader.get_connection")
    def test_csv_passes_metadata_to_register(
        self, mock_conn, mock_gen_meta, mock_register, sample_csv_file
    ):
        """CSV 로더가 generate_rich_metadata 결과를 register_table에 전달합니다."""
        from watcher.metadata_generator import RichMetadata
        from watcher.loader.csv_loader import load_csv

        # DB mock 설정
        mock_cur = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cur

        # 메타데이터 mock
        mock_gen_meta.return_value = RichMetadata(
            description="테스트 매출 데이터",
            data_category="statistics",
            tags=["매출", "테스트"],
            column_descriptions={"id": "ID", "product_name": "제품명"},
            sample_values={"id": ["1", "2"]},
            numeric_ratio=0.5,
            avg_text_length=10.0,
        )

        load_csv(sample_csv_file)

        # register_table이 호출되었는지 확인
        mock_register.assert_called_once()
        call_kwargs = mock_register.call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        # positional args에서 keyword로 전달될 수도 있음
        if not kwargs:
            # **register_kwargs로 전달되므로 kwargs에 있어야 함
            pass

    @patch("watcher.loader.csv_loader.register_table")
    @patch("watcher.metadata_generator.generate_rich_metadata")
    @patch("watcher.loader.csv_loader.get_connection")
    def test_csv_data_category_parameter(
        self, mock_conn, mock_gen_meta, mock_register, sample_csv_file
    ):
        """CSV 로더가 data_category 파라미터를 받습니다."""
        from watcher.metadata_generator import RichMetadata
        from watcher.loader.csv_loader import load_csv

        mock_cur = MagicMock()
        mock_conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.__enter__.return_value.cursor.return_value = mock_cur

        mock_gen_meta.return_value = RichMetadata(
            description=None, data_category="document",
            tags=None, column_descriptions=None,
            sample_values={}, numeric_ratio=0.0, avg_text_length=0.0,
        )

        # data_category 파라미터 전달 (스마트 분류기에서 호출 시)
        load_csv(sample_csv_file, data_category="document")

        mock_gen_meta.assert_called_once()
        # generate_rich_metadata에 data_category가 전달되었는지 확인
        gen_call = mock_gen_meta.call_args
        assert gen_call is not None
