# DataBridge 테스트 및 실행 가이드

## 사전 요구사항

### 필수 의존성

```bash
pip install -r requirements.txt
```

주요 패키지:
- `pytest>=8.0` — 테스트 프레임워크
- `pytest-cov>=5.0` — 커버리지 측정
- `psycopg2-binary>=2.9` — PostgreSQL 드라이버
- `chromadb>=0.5` — 벡터 데이터베이스
- `requests>=2.31` — HTTP 클라이언트 (Ollama API)
- `pandas>=2.1` — 데이터 처리
- `pypdf>=4.0` — PDF 파싱
- `watchdog>=4.0` — 파일 시스템 감시

### Docker 서비스 (통합 테스트용)

통합 테스트(`@pytest.mark.integration`)는 Docker 서비스가 필요합니다:

```bash
docker compose up -d postgres chromadb
# Ollama는 로컬에서 별도 실행: ollama serve
```

서비스 확인:
- PostgreSQL: `localhost:5432` (user: adf, password: changeme, db: adf)
- ChromaDB: `localhost:8000`
- Ollama: `localhost:11434` (로컬 설치 — `ollama serve`로 실행)

## 테스트 실행

### 단위 테스트 (외부 서비스 불필요)

```bash
# 모든 단위 테스트 실행
pytest -m unit -v

# 특정 모듈만 실행
pytest tests/test_pdf_parsing.py -m unit -v
pytest tests/test_csv_excel_loading.py -m unit -v
pytest tests/test_folder_watching.py -m unit -v
pytest tests/test_agent_tools.py -m unit -v
pytest tests/test_sql_agent.py -m unit -v
pytest tests/test_doc_agent.py -m unit -v
pytest tests/test_orchestrator.py -m unit -v
```

### 통합 테스트 (Docker 서비스 필요)

```bash
# 모든 통합 테스트 실행
pytest -m integration -v

# 특정 모듈만 실행
pytest tests/test_csv_excel_loading.py -m integration -v
pytest tests/test_folder_watching.py -m integration -v
```

### 전체 테스트 + 커버리지

```bash
pytest --cov=agent --cov=rag --cov=watcher --cov=catalog --cov-report=term-missing -v
```

## 테스트 구조

```
tests/
├── conftest.py                 # 공통 픽스처 (샘플 데이터, 임시 파일)
├── test_pdf_parsing.py         # PDF 텍스트 추출 테스트
├── test_csv_excel_loading.py   # CSV/Excel → DB 적재 테스트
├── test_folder_watching.py     # 파일 분류 + 감시 테스트
├── test_agent_tools.py         # SQL 검증, 테이블 목록, 문서 검색 도구
├── test_sql_agent.py           # SQL 에이전트 파이프라인
├── test_doc_agent.py           # 문서 에이전트 파이프라인
└── test_orchestrator.py        # 의도 분류 + 라우팅
```

### 테스트 마커

| 마커 | 설명 | 서비스 필요 |
|------|------|------------|
| `@pytest.mark.unit` | 순수 로직, mock 기반 | 없음 |
| `@pytest.mark.integration` | 실제 DB/서비스 연동 | Docker |

## 샘플 데이터 생성

```bash
python scripts/generate_sample_data.py
```

`sample_data/` 디렉토리에 생성되는 파일:
- `products_sample.xlsx` — 2시트 Excel (제품목록, 재고현황)
- `sample_report.pdf` — 2페이지 PDF (영문 보고서)

## 에이전트 수동 테스트

### 전제 조건
1. Docker 서비스 실행 (`docker compose up -d`)
2. DB 스키마 초기화 (`psql -f db/init.sql`)
3. 샘플 데이터 적재 (샘플 CSV/Excel을 watch 디렉토리에 복사)

### 오케스트레이터 테스트

```python
from agent.orchestrator import process_query

# 데이터 조회 (SQL 에이전트)
result = process_query("sales 테이블에서 총 매출 보여줘")
print(result["intent"])   # "data"
print(result["answer"])   # 자연어 응답
print(result["sql"])      # 생성된 SQL

# 문서 검색 (문서 에이전트)
result = process_query("보고서에서 매출 동향 알려줘")
print(result["intent"])   # "document"
print(result["answer"])   # RAG 기반 응답
print(result["sources"])  # 참조 문서 목록
```

### 개별 도구 테스트

```python
# SQL 검증
from agent.tools.query_db import validate_sql
print(validate_sql("SELECT * FROM sales"))           # (True, "Valid SELECT query")
print(validate_sql("DROP TABLE sales"))               # (False, "SELECT 쿼리만 허용됩니다.")

# 카탈로그 스키마
from agent.tools.list_tables import get_all_tables_summary
print(get_all_tables_summary())

# 문서 검색
from agent.tools.search_docs import search
results = search("분기별 매출")
print(results)
```

## 에이전트 모듈 구조

```
agent/
├── __init__.py           # 패키지 초기화, process_query export
├── _llm.py               # Ollama REST API 래퍼 (generate)
├── _audit.py             # 감사 로그 기록 헬퍼 (log_action)
├── orchestrator.py       # 의도 분류 + 에이전트 라우팅
├── sql_agent.py          # 자연어 → SQL 생성 + 실행 + 요약
├── doc_agent.py          # 문서 검색 + RAG 답변 생성
└── tools/
    ├── __init__.py       # 도구 패키지 초기화
    ├── query_db.py       # SELECT 검증 + 안전 실행
    ├── list_tables.py    # 카탈로그 스키마 요약
    └── search_docs.py    # ChromaDB 문서 검색 래퍼
```

### 처리 흐름

```
사용자 질의
    ↓
orchestrator.process_query()
    ├─ classify_intent()  →  "data" | "document" | "composite"
    ↓
    ├─ "data"      → sql_agent.process()
    │   ├─ list_tables.get_all_tables_summary()  (스키마 조회)
    │   ├─ _llm.generate()                        (SQL 생성)
    │   ├─ query_db.validate_sql()                (보안 검증)
    │   ├─ query_db.execute_select()              (실행)
    │   └─ _llm.generate()                        (결과 요약)
    │
    ├─ "document"  → doc_agent.process()
    │   ├─ search_docs.search()                   (ChromaDB 검색)
    │   └─ _llm.generate()                        (RAG 응답 생성)
    │
    └─ "composite" → sql_agent + doc_agent 모두 호출
```

## 환경 변수

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama 서버 주소 (로컬 설치) |
| `LLM_MODEL` | `exaone3.5:7.8b` | 사용 LLM 모델 |
| `AGENT_MAX_QUERY_ROWS` | `5000` | SELECT 최대 반환 행 수 |
| `AGENT_QUERY_TIMEOUT` | `30` | SQL 실행 타임아웃 (초) |
| `POSTGRES_HOST` | `postgres` | PostgreSQL 호스트 |
| `CHROMA_HOST` | `chromadb` | ChromaDB 호스트 |
| `WATCH_DIR` | `/data` | 파일 감시 디렉토리 |
