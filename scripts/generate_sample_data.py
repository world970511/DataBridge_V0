"""
테스트 및 데모용 샘플 데이터를 sample_data/ 디렉토리에 생성하는 스크립트.

openpyxl로 2시트(제품목록, 재고현황) Excel 파일을 생성하고,
PDF 1.4 원시 바이너리를 직접 작성하여 2페이지짜리 샘플 보고서 PDF를 생성합니다.
외부 데이터 없이 DataBridge 파이프라인을 테스트할 수 있도록 합니다.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sample_data")


def generate_excel():
    """
    openpyxl을 사용하여 2개 시트가 있는 샘플 Excel 파일을 생성.

    시트1(제품목록): 제품코드, 제품명, 카테고리, 가격 5행.
    시트2(재고현황): 제품코드, 창고, 수량, 최종입고일 5행.
    sample_data/products_sample.xlsx로 저장됩니다.
    """
    import openpyxl

    wb = openpyxl.Workbook()

    # Sheet 1: 제품
    ws1 = wb.active
    ws1.title = "제품목록"
    ws1.append(["제품코드", "제품명", "카테고리", "가격"])
    ws1.append(["P001", "노트북", "전자기기", 1200000])
    ws1.append(["P002", "모니터", "전자기기", 450000])
    ws1.append(["P003", "키보드", "주변기기", 85000])
    ws1.append(["P004", "마우스", "주변기기", 35000])
    ws1.append(["P005", "헤드셋", "주변기기", 120000])

    # Sheet 2: 재고
    ws2 = wb.create_sheet("재고현황")
    ws2.append(["제품코드", "창고", "수량", "최종입고일"])
    ws2.append(["P001", "서울", 50, "2025-01-15"])
    ws2.append(["P002", "서울", 120, "2025-01-14"])
    ws2.append(["P003", "부산", 300, "2025-01-13"])
    ws2.append(["P004", "부산", 500, "2025-01-12"])
    ws2.append(["P005", "서울", 80, "2025-01-11"])

    path = os.path.join(SAMPLE_DIR, "products_sample.xlsx")
    wb.save(path)
    print(f"Created: {path}")


def generate_pdf():
    """
    PDF 1.4 규격의 원시 바이너리를 직접 작성하여 2페이지 샘플 PDF를 생성.

    Helvetica 폰트를 사용한 영문 텍스트로 구성되며, pypdf로 파싱 가능합니다.
    페이지1: Summary 섹션, 페이지2: Recommendations 섹션.
    sample_data/sample_report.pdf로 저장됩니다.
    """
    content = """%PDF-1.4
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
<< /Length 244 >>
stream
BT
/F1 16 Tf
220 750 Td
(DataBridge Sample Report) Tj
/F1 12 Tf
72 720 Td
(1. Summary) Tj
/F1 10 Tf
72 700 Td
(This is a sample report for testing the DataBridge document pipeline.) Tj
72 685 Td
(Q1 2025 sales showed a 15 percent increase compared to Q4 2024.) Tj
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
<< /Length 185 >>
stream
BT
/F1 12 Tf
72 750 Td
(3. Recommendations) Tj
/F1 10 Tf
72 730 Td
(We recommend increasing inventory for notebooks and monitors.) Tj
72 715 Td
(The peripheral category shows stable demand.) Tj
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
0000000560 00000 n
0000000637 00000 n
0000000788 00000 n

trailer
<< /Size 8 /Root 1 0 R >>
startxref
1023
%%EOF
"""
    path = os.path.join(SAMPLE_DIR, "sample_report.pdf")
    with open(path, "wb") as f:
        f.write(content.encode("latin-1"))
    print(f"Created: {path}")


if __name__ == "__main__":
    os.makedirs(SAMPLE_DIR, exist_ok=True)
    generate_excel()
    generate_pdf()
    print("All sample data generated.")
