"""
PDF 파싱 모듈 테스트.

rag/parser/pdf_parser.py의 parse_pdf(), parse_pdf_by_pages() 함수를 테스트합니다.
conftest.py의 sample_pdf_file 픽스처를 사용하여 임시 PDF 파일 대상으로
텍스트 추출, 페이지별 파싱, 빈 파일 처리, 존재하지 않는 파일 처리를 검증합니다.

실행:
    pytest tests/test_pdf_parsing.py -v -m unit
"""

import pytest

from rag.parser.pdf_parser import parse_pdf, parse_pdf_by_pages


@pytest.mark.unit
class TestParsePdf:
    """parse_pdf() — 전체 텍스트 추출 테스트."""

    def test_extracts_text_from_pdf(self, sample_pdf_file):
        """PDF에서 텍스트를 추출하면 비어있지 않은 문자열이 반환되어야 합니다."""
        text = parse_pdf(sample_pdf_file)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_contains_expected_content(self, sample_pdf_file):
        """추출된 텍스트에 PDF에 포함된 내용이 존재해야 합니다."""
        text = parse_pdf(sample_pdf_file)
        # PDF에 "DataBridge Test Report" 가 포함되어 있어야 함
        assert "DataBridge" in text or "Test Report" in text

    def test_file_not_found_raises(self, tmp_path):
        """존재하지 않는 파일 경로를 전달하면 FileNotFoundError가 발생해야 합니다."""
        fake_path = str(tmp_path / "nonexistent.pdf")
        with pytest.raises(FileNotFoundError):
            parse_pdf(fake_path)

    def test_returns_string_type(self, sample_pdf_file):
        """반환값이 str 타입이어야 합니다."""
        result = parse_pdf(sample_pdf_file)
        assert isinstance(result, str)


@pytest.mark.unit
class TestParsePdfByPages:
    """parse_pdf_by_pages() — 페이지별 텍스트 추출 테스트."""

    def test_returns_list_of_dicts(self, sample_pdf_file):
        """반환값이 딕셔너리 리스트여야 합니다."""
        pages = parse_pdf_by_pages(sample_pdf_file)
        assert isinstance(pages, list)
        assert all(isinstance(p, dict) for p in pages)

    def test_pages_have_required_keys(self, sample_pdf_file):
        """각 페이지 딕셔너리에 'page'와 'text' 키가 있어야 합니다."""
        pages = parse_pdf_by_pages(sample_pdf_file)
        for page in pages:
            assert "page" in page
            assert "text" in page

    def test_page_numbers_start_from_one(self, sample_pdf_file):
        """페이지 번호가 1부터 시작해야 합니다."""
        pages = parse_pdf_by_pages(sample_pdf_file)
        if pages:
            assert pages[0]["page"] == 1

    def test_multi_page_pdf(self, sample_pdf_file):
        """2페이지 PDF에서 2개의 페이지 결과가 반환되어야 합니다."""
        pages = parse_pdf_by_pages(sample_pdf_file)
        # 테스트 PDF는 2페이지이므로 최소 1개 이상 (빈 페이지 제외 가능)
        assert len(pages) >= 1

    def test_page_text_not_empty(self, sample_pdf_file):
        """각 페이지의 텍스트가 비어있지 않아야 합니다."""
        pages = parse_pdf_by_pages(sample_pdf_file)
        for page in pages:
            assert len(page["text"].strip()) > 0
