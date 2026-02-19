"""
pytest 공통 픽스처 모듈.

모든 테스트 파일에서 공유하는 픽스처(샘플 데이터, mock 객체, 임시 파일 등)를
정의합니다. pytest가 자동으로 이 파일을 로드하여 각 테스트에 주입합니다.

픽스처 목록:
    - sample_catalog_tables: 카탈로그 테이블 메타데이터 샘플
    - sample_catalog_documents: 카탈로그 문서 메타데이터 샘플
    - sample_search_results: ChromaDB 검색 결과 샘플
    - sample_csv_file: 임시 CSV 파일 경로
    - sample_excel_file: 임시 Excel 파일 경로
    - sample_pdf_file: 임시 PDF 파일 경로
    - tmp_watch_dir: 임시 감시 디렉토리
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# 프로젝트 루트를 sys.path에 추가 (테스트에서 모듈 임포트 가능하도록)
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# .env 파일에서 환경 변수 로드 (테스트 실행 전)
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


@pytest.fixture(scope="session", autouse=True)
def reset_singletons():
    """
    테스트 세션 시작 시 싱글톤 초기화.

    .env 파일 로드 후 settings 캐시와 DB 연결 풀을 초기화하여
    새로운 설정값이 반영되도록 합니다.
    """
    # settings 싱글톤 초기화
    try:
        import config.settings as settings_module
        settings_module._settings = None
    except ImportError:
        pass

    # DB 연결 풀 초기화
    try:
        import db.connection as conn_module
        conn_module._pool = None
    except ImportError:
        pass

    yield


# ============================================
# 카탈로그 샘플 데이터
# ============================================

@pytest.fixture
def sample_catalog_tables():
    """카탈로그에 등록된 테이블 메타데이터 샘플 (list_tables() 반환 형식)."""
    return [
        {
            "table_name": "sales",
            "source_file": "/data/sales.csv",
            "file_type": "csv",
            "row_count": 15230,
            "column_count": 5,
            "columns_json": [
                {"name": "id", "type": "BIGINT"},
                {"name": "product_name", "type": "TEXT"},
                {"name": "amount", "type": "DOUBLE PRECISION"},
                {"name": "quantity", "type": "BIGINT"},
                {"name": "sale_date", "type": "TIMESTAMPTZ"},
            ],
        },
        {
            "table_name": "products",
            "source_file": "/data/products.xlsx",
            "file_type": "excel",
            "row_count": 324,
            "column_count": 4,
            "columns_json": [
                {"name": "id", "type": "BIGINT"},
                {"name": "name", "type": "TEXT"},
                {"name": "category", "type": "TEXT"},
                {"name": "price", "type": "DOUBLE PRECISION"},
            ],
        },
    ]


@pytest.fixture
def sample_catalog_documents():
    """카탈로그에 등록된 문서 메타데이터 샘플 (list_documents() 반환 형식)."""
    return [
        {
            "doc_name": "report.pdf",
            "source_file": "/data/report.pdf",
            "file_type": "pdf",
            "chunk_count": 12,
            "collection_name": "documents",
        },
        {
            "doc_name": "guide.docx",
            "source_file": "/data/guide.docx",
            "file_type": "docx",
            "chunk_count": 8,
            "collection_name": "documents",
        },
    ]


@pytest.fixture
def sample_search_results():
    """ChromaDB 검색 결과 샘플 (search_documents() 반환 형식)."""
    return [
        {
            "text": "Q1 2025 매출은 전년 대비 15% 증가하였습니다.",
            "metadata": {"source": "report.pdf", "chunk_index": 0},
            "distance": 0.15,
        },
        {
            "text": "주요 성장 요인은 온라인 채널 확대와 신규 제품 출시입니다.",
            "metadata": {"source": "report.pdf", "chunk_index": 1},
            "distance": 0.28,
        },
        {
            "text": "제품 사용 가이드: 설치 후 초기 설정을 완료하세요.",
            "metadata": {"source": "guide.docx", "chunk_index": 0},
            "distance": 0.65,
        },
    ]


# ============================================
# 임시 파일 픽스처
# ============================================

@pytest.fixture
def sample_csv_file(tmp_path):
    """테스트용 임시 CSV 파일을 생성하고 경로를 반환."""
    csv_content = "id,product_name,amount,quantity\n1,노트북,1200000,10\n2,모니터,450000,25\n3,키보드,85000,100\n"
    csv_path = tmp_path / "test_sales.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return str(csv_path)


@pytest.fixture
def sample_tsv_file(tmp_path):
    """테스트용 임시 TSV 파일을 생성하고 경로를 반환."""
    tsv_content = "id\tname\tprice\n1\t사과\t3000\n2\t바나나\t2000\n"
    tsv_path = tmp_path / "test_fruits.tsv"
    tsv_path.write_text(tsv_content, encoding="utf-8")
    return str(tsv_path)


@pytest.fixture
def sample_excel_file(tmp_path):
    """테스트용 임시 Excel 파일을 생성하고 경로를 반환. openpyxl 필요."""
    try:
        import openpyxl
    except ImportError:
        pytest.skip("openpyxl not installed")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["id", "name", "price"])
    ws.append([1, "노트북", 1200000])
    ws.append([2, "모니터", 450000])

    xlsx_path = tmp_path / "test_products.xlsx"
    wb.save(str(xlsx_path))
    return str(xlsx_path)


@pytest.fixture
def sample_pdf_file(tmp_path):
    """
    테스트용 임시 PDF 파일을 생성하고 경로를 반환.

    PDF 1.4 원시 바이너리를 직접 작성합니다 (pypdf로 파싱 가능).
    2페이지로 구성: 페이지1 'DataBridge Test Report', 페이지2 'Page Two Content'.
    """
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj

2 0 obj
<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>
endobj

3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj

4 0 obj
<< /Length 95 >>
stream
BT
/F1 12 Tf
72 750 Td
(DataBridge Test Report - Page One Content) Tj
ET
endstream
endobj

5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj

6 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 7 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj

7 0 obj
<< /Length 78 >>
stream
BT
/F1 12 Tf
72 750 Td
(Recommendations and Page Two Content) Tj
ET
endstream
endobj

xref
0 8
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000266 00000 n
0000000413 00000 n
0000000490 00000 n
0000000641 00000 n

trailer
<< /Size 8 /Root 1 0 R >>
startxref
771
%%EOF
"""
    pdf_path = tmp_path / "test_report.pdf"
    pdf_path.write_bytes(pdf_content)
    return str(pdf_path)


@pytest.fixture
def tmp_watch_dir(tmp_path):
    """테스트용 임시 감시 디렉토리를 생성하고 경로를 반환."""
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    return str(watch_dir)
