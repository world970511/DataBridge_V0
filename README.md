<p align="center">
  <h1 align="center">🏭 DataBridge_V0</h1>
  <p align="center">
    <strong>AI를 사용한 가장 간단한 데이터 관리</strong>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" />
    <img src="https://img.shields.io/badge/python-3.11+-green.svg" alt="Python" />
    <img src="https://img.shields.io/badge/version-0.1--alpha-orange.svg" alt="Version" />
  </p>
</p>

---

## 이런 분들을 위해 만들었습니다

```
✅ 회사 데이터가 외부로 나가면 안 되는데, AI를 쓰고 싶다
✅ 데이터 관리를 공유 폴더 + 엑셀 + 배치 스크립트로 하고 있다
✅ 외부 업체가 FTP/SFTP로 보내주는 파일을 수동으로 확인하고 정리하고 있다
✅ "이 데이터 좀 뽑아주세요" 요청이 올 때마다 SQL을 직접 짜고 있다
✅ 보고서 PDF 하나 찾으려고 공유 폴더를 뒤지고 있다
✅ 부서별 분석 테이블을 수작업으로 만들고, 배치 스크립트로 갱신하고 있다
✅ 전담 데이터 엔지니어가 없거나 1~2명이다
✅ 수천만 행 규모의 빅데이터는 아니지만, 관리가 점점 힘들어지고 있다
```
- AI 의 발전이 늘어나면서 데이터 수집 및 관리의 필요성은 늘어났지만, 대부분의 서비스가 수천만 또는 억 단위의 빅데이터에 집중해 구현된 것이 아쉬웠습니다.  
- 개인적인 경험으로, 빅데이터가 아니더라도 사람이 다루기는 어려운 데이터의 양을 AI를 사용하여 데이터를 수집하고 관리할 수 있으면 좋겠다는 생각으로 이 프로젝트를 진행하였습니다.

---

<h2 id="why-adf"> MCP, Open WebUI와 뭐가 다른가요? </h2>

"Ollama 설치하고, MCP로 파일시스템 연결하면 되는 거 아닌가요?"
"Open WebUI에서 파일 올리면 되지 않나요?"

맞습니다. 빠르게 시작할 수 있는 좋은 도구들입니다.
하지만 밑과 같은 문제가 발생합니다.

### 🔒 보안: LLM이 파일시스템에 직접 접근합니다

MCP filesystem을 연결하면 LLM이 지정된 디렉토리의 파일을 **직접 읽고 쓸 수 있는 권한**을 갖게 됩니다. 개인 프로젝트에서는 편리하지만, 회사 공유 폴더에 연결하면:

```
⚠️ LLM이 읽을 수 있는 범위를 세밀하게 제한하기 어렵습니다
⚠️ 프롬프트 인젝션으로 의도하지 않은 파일에 접근할 가능성이 있습니다
⚠️ 누가 어떤 파일을 어떤 질의로 열어봤는지 감사 추적이 되지 않습니다
⚠️ 쓰기 권한이 있는 경우, LLM이 원본 파일을 변경할 수도 있습니다
```

**ADF의 접근 방식:**
```
공유 폴더 ──(감시)──▶ 구조화하여 DB에 적재 ──(SELECT만)──▶ 에이전트
    │                                                          │
    │  원본 파일은 그대로.                    에이전트는 DB에서  │
    │  에이전트가 직접 접근하지 않음.          읽기만 가능.      │
    │                                         모든 질의 로그 기록│
```

LLM은 원본 파일에 접근하지 않습니다. 구조화된 DB에 대해 `SELECT`만 실행하며, 모든 질의와 응답은 감사 로그에 기록됩니다.

### 🎯 정확도: 엑셀을 "읽는 것"과 "계산하는 것"은 다릅니다

MCP나 Open WebUI에서 엑셀 파일을 올리면 LLM이 **텍스트로 읽습니다.** 여기서 "이번 달 매출 합계를 구해줘"라고 하면:

```
❌ MCP / Open WebUI 방식:
   엑셀 → 텍스트로 변환 → LLM이 숫자를 하나씩 읽으며 더함
   → 행이 100개만 넘어도 틀릴 확률이 급격히 올라감
   → 1,000행 이상은 컨텍스트 윈도우에 들어가지도 않음

✅ ADF 방식:
   엑셀 → DB 테이블로 변환 → SQL: SELECT SUM(amount) FROM sales
   → 100만 행이어도 정확한 답
   → 집계, 필터, 조인, 정렬 전부 SQL이 처리
```

이 차이는 데이터가 커질수록 벌어집니다:

| 작업 | MCP/Open WebUI (텍스트 기반) | ADF (DB 기반) |
|---|---|---|
| "매출 합계 구해줘" (50행) | ⚠️ 대체로 맞음 | ✅ 정확 |
| "매출 합계 구해줘" (5,000행) | ❌ 컨텍스트 초과 또는 오류 | ✅ 정확 |
| "월별 매출 추이 보여줘" | ⚠️ 근사치 | ✅ 정확한 GROUP BY |
| "전년 대비 매출 증감률" | ❌ 복잡한 계산에서 자주 오류 | ✅ SQL 윈도우 함수로 정확 |
| "A제품 매출 중 서울 지역 비율" | ❌ 다중 조건에서 거의 불가능 | ✅ WHERE + JOIN + 집계 |

### 🏗️ 조회를 넘어서: 테이블을 만들고 배치를 등록합니다

MCP와 Open WebUI는 "질문하면 답해주는" 도구입니다.
우리는 거기서 한걸음 더 나가서, — **에이전트가 분석용 테이블을 설계하고, 배치 작업을 등록합니다.**

```
❌ MCP / Open WebUI:
   "월별 제품 매출 마트 만들어줘" → 할 수 없음

✅ ADF:
   "월별 제품 매출 마트 만들어줘"
   → 에이전트가 CREATE TABLE + INSERT SQL 생성
   → 사용자가 확인하고 [승인] 버튼 클릭
   → 테이블 생성 + 매일 자동 갱신 배치 등록
```

> 에이전트는 "제안"하고, **사람이 확인하고 "결정"**합니다.
> 승인 없이 실행되는 것은 없습니다.

### 📄 문서 검색도 다릅니다

| | MCP filesystem | Open WebUI | **ADF** |
|---|---|---|---|
| 문서 등록 | 수동으로 경로 지정 | 수동 업로드 | **폴더에 넣으면 자동** |
| 검색 방식 | 파일 내용 직접 읽기 | 업로드된 문서 내 검색 | **벡터 검색 (의미 기반)** |
| 새 문서 추가 | 매번 설정 필요 | 매번 업로드 필요 | **자동 감지·등록** |
| 한글 문서 (.hwp) | 미지원 | 미지원 | **지원** |

> **여러 사람이, 회사 데이터를, 안전하고 정확하게** 다루는 환경을 가장 쉽게 만드는 것이 우리의 목표입니다.

---

<h2 id="how-it-works">어떻게 동작하나요?</h2>

### 1단계: 파일을 넣습니다 (지금 하던 대로)

**방법 A — 공유 폴더** (SAMBA / NAS / 로컬 폴더)

```
  📁 /data/
    ├── 매출/
    │   ├── 2025년1월_매출.xlsx     ← 엑셀, CSV 넣으면
    │   └── 2025년2월_매출.csv          자동으로 DB에 들어갑니다
    ├── 보고서/
    │   ├── 시장분석_2025Q1.pdf     ← PDF, 한글, Word 넣으면
    │   └── 기술검토_보안.hwp            검색 가능하게 등록됩니다
    └── 로그/
        └── 서버로그_0210.csv       ← 무슨 파일이든 알아서 분류합니다
```

**방법 B — FTP / SFTP / FTPS 서버** (외부 업체 연동, 지점 데이터 수집)

```
  외부 업체 / 지점에서 FTP/SFTP로 파일 업로드
       │
       ▼
  FTP/SFTP 서버 (ProFTPD, vsftpd, OpenSSH 등)
       │
       │  업로드 완료 → 서버 훅이 DataBridge에 알림 (자동)
       ▼
  DataBridge 수신 → 분류 → DB 적재 / 문서 검색 등록
```

> 💡 FTP/SFTP 서버에 **업로드 완료 훅**을 설정하면, 파일이 올라오는 즉시 DataBridge가 처리합니다.
> 주기적으로 폴더를 확인하는 방식이 아니라 **이벤트 기반**으로 동작합니다.

### 2단계: AI 채팅으로 물어봅니다

```
👤 "1월 대비 2월 매출이 떨어진 제품이 있어? 관련 보고서도 있으면 찾아줘"
🤖 [DB 조회 + 문서 검색 동시 실행]
   매출 하락 제품 3건:
   - C제품: -18% (₩8,800,000 → ₩7,200,000)
   ...
   관련 문서: '시장분석_2025Q1.pdf' (p.12)
   "C제품 카테고리 시장이 경쟁사 신제품 출시로 위축..."
```

### 3단계: 분석 테이블을 만들고 배치를 등록합니다

```
👤 "마케팅팀이 볼 수 있는 월간 제품별 매출 마트를 만들어줘"

🤖 [기존 테이블 분석]
   사용 가능한 테이블:
   - sales (일별 매출), products (제품 마스터)

   아래 마트를 생성할까요?

   ┌─────────────────────────────────────────────┐
   │  CREATE TABLE mart_monthly_product_sales (   │
   │    month         DATE,                       │
   │    product_id    BIGINT,                     │
   │    product_name  VARCHAR(100),               │
   │    total_amount  DECIMAL(15,2),              │
   │    order_count   INT                         │
   │  );                                          │
   │                                              │
   │  + 초기 데이터 적재 SQL                       │
   │  + 매일 06:00 자동 갱신 배치                  │
   └─────────────────────────────────────────────┘

   ⏸️ [승인] [SQL 수정] [취소]

👤 [승인] 클릭

🤖 ✅ 마트 생성 완료 (1,247행 적재)
   ✅ 배치 등록 완료 (매일 06:00 자동 갱신)

   "mart_monthly_product_sales에서 데이터 보여줘" 로 조회할 수 있습니다.
```

```
👤 "매일 아침 7시에 전일 매출 요약을 만들어줘"

🤖 아래 배치 작업을 등록할까요?

   ┌─────────────────────────────────────────────┐
   │  작업명:  daily_sales_summary                │
   │  스케줄:  매일 07:00                          │
   │  실행 SQL:                                    │
   │    SELECT product_name, SUM(amount)           │
   │    FROM sales                                 │
   │    WHERE sale_date = CURRENT_DATE - 1         │
   │    GROUP BY product_name                      │
   │    ORDER BY SUM(amount) DESC                  │
   │  결과 저장: reports/daily_sales/              │
   └─────────────────────────────────────────────┘

   ⏸️ [등록] [SQL 수정] [취소]

👤 [등록] 클릭

🤖 ✅ 배치 등록 완료
   매일 07:00에 실행되며, 결과는 reports/daily_sales/ 에 저장됩니다.
   "등록된 배치 목록 보여줘" 로 관리할 수 있습니다.
```

> ⚠️ **Human-in-the-Loop**: 에이전트가 생성한 SQL은 사용자가 **직접 확인하고 승인**해야만 실행됩니다.
> 승인 없이 테이블이 만들어지거나 배치가 등록되는 일은 없습니다.

### 전체 구조

```
기존 환경 (바꿀 것 없음)          추가 설치하는 것 (서버 1대)
┌──────────────────┐           ┌──────────────────────────────┐
│                  │           │                              │
│  📁 공유 폴더    │──파일──▶  │  👁️ 파일 감시                │
│  (SAMBA/NAS)     │  올리면   │  엑셀/CSV → DB 자동 적재     │
│                  │  자동감지  │  문서 → 검색 자동 등록       │
│                  │           │                              │
└──────────────────┘           │  🔗 Webhook API              │
                               │  FTP/SFTP 서버 훅 수신       │
┌──────────────────┐           │  외부 시스템 연동 가능       │
│  FTP/SFTP 서버   │──업로드──▶│                              │
│  (ProFTPD,       │  완료 훅  │  🗄️ DB (PostgreSQL)          │
│   OpenSSH 등)    │           │  정리된 데이터 저장           │
└──────────────────┘           │                              │
                               │  🔍 문서 검색 (ChromaDB)     │
┌──────────────────┐           │  PDF/HWP/Word 내용 검색      │
│  기존 DB         │           │                              │
│  (있으면 연결,   │──선택──▶  │  🤖 AI 에이전트 (Ollama)     │
│   없어도 됨)     │           │  ├ 데이터 조회 (SQL Agent)   │
└──────────────────┘           │  ├ 문서 검색 (Doc Agent)     │
                               │  ├ 마트 구축 (Mart Builder)  │
┌──────────────────┐           │  └ 배치 관리 (Scheduler)     │
│  기존 cron 배치  │           │                              │
│  (그대로 유지)   │           │  ✅ 승인 레이어               │
└──────────────────┘           │  DDL/배치는 사용자 승인 필수  │
                               │                              │
                               │  💬 채팅 UI (웹브라우저)     │
                               │  http://서버IP:8501          │
                               │                              │
                               │  🔒 전부 사내망 안에서 동작  │
                               └──────────────────────────────┘
```

> **기존 환경을 건드리지 않습니다.** 공유 폴더도 그대로, FTP 서버도 그대로, cron도 그대로.
> 옆에 서버 하나 설치하는 것뿐입니다.

---

<h2 id="quickstart">설치하기</h2>

### 준비물

| 항목 | 조건 | 비고 |
|---|---|---|
| **서버** | Linux (Ubuntu 22.04+) | 사내 여유 서버 1대 |
| **RAM** | 16 GB 이상 | 권장 32 GB |
| **디스크** | 50 GB 이상 | 데이터 규모에 따라 |
| **GPU** | 없어도 됩니다 | 있으면 응답이 빨라짐 |
| **Docker** | Docker Compose v2.20+ | |
| **네트워크** | 사내망 접근 가능 | 인터넷 불필요 (설치 후) |

### 설치

```bash
# 1. 다운로드
git clone https://github.com/world970511/DataBridge_V0.git
cd DataBridge_V0

# 2. 설정 — 감시할 공유 폴더 경로를 지정합니다
cp .env.example .env
vi .env
# WATCH_DIR=/mnt/shared/data   ← 공유 폴더 경로 지정

# 3. 실행
docker compose up -d

# 4. AI 모델 다운로드 (최초 1회, 약 5GB)
docker compose exec ollama ollama pull exaone3.5:7.8b
```

### 접속

```
웹 브라우저에서 http://서버IP:8501
```

### 동작 확인

공유 폴더에 아무 엑셀/CSV 파일을 넣어보세요.
채팅에서 `"방금 올린 파일 내용 보여줘"` 라고 물어보세요.

---

<h2 id="usage">사용법</h2>

### 데이터 올리기

데이터를 넣는 방법은 3가지입니다. 동시에 사용할 수 있습니다.

**방법 1 — 공유 폴더** (가장 기본)

공유 폴더 안에 파일을 넣으면 됩니다. 하위 폴더를 자유롭게 만들 수 있습니다.

| 파일 종류 | 처리 방식 | 예시 |
|---|---|---|
| **엑셀 (.xlsx, .xls)** | 시트별로 DB 테이블로 변환 | 매출현황.xlsx → `매출현황` 테이블 |
| **CSV / TSV** | DB 테이블로 변환 | sales_202502.csv → `sales_202502` 테이블 |
| **PDF** | 텍스트 추출 → 검색 등록 | 보고서.pdf → 채팅에서 내용 검색 가능 |
| **한글 (.hwp, .hwpx)** | 텍스트 추출 → 검색 등록 | 기안서.hwp → 채팅에서 내용 검색 가능 |
| **Word (.docx)** | 텍스트 추출 → 검색 등록 | 회의록.docx → 채팅에서 내용 검색 가능 |
| **JSON** | DB 테이블로 변환 | api_data.json → 테이블로 변환 |

> 💡 파일을 넣으면 보통 **몇 초~1분 이내**에 자동으로 처리됩니다.
> 처리 상태는 채팅에서 `"최근 등록된 파일 목록"` 으로 확인할 수 있습니다.

**방법 2 — FTP / SFTP / FTPS 서버 훅**

FTP/SFTP 서버에 업로드 완료 훅을 설정하면, 외부 업체나 지점에서 파일을 올릴 때마다 자동으로 처리됩니다. 설정 방법은 [FTP/SFTP 서버 훅 설정](#ftp-sftp-hook) 을 참고하세요.

```
외부 업체가 SFTP로 납품 데이터 업로드
  → 서버 훅이 DataBridge에 알림
  → 자동으로 DB 적재
  → "오늘 들어온 납품 데이터 보여줘" 로 즉시 조회
```

**방법 3 — Webhook API** (커스텀 연동)

기존 배치 스크립트나 외부 시스템에서 직접 호출할 수 있습니다.

```bash
# 기존 스크립트 끝에 한 줄 추가
curl -X POST http://databridge:8501/api/webhook/file-uploaded \
  -H "Content-Type: application/json" \
  -d '{"path": "/data/new_file.csv", "source": "legacy_batch"}'
```

### 채팅으로 조회하기

**데이터 조회:**
```
"이번 달 매출 보여줘"
"제품별 매출 합계를 내림차순으로"
"지난달 대비 매출이 줄어든 항목은?"
"customers 테이블에서 서울 거주 고객 수는?"
```

**문서 검색:**
```
"보안 관련 보고서 찾아줘"
"최근 올라온 문서 목록 보여줘"
"계약서에서 해지 조건 관련 내용 찾아줘"
"지난 분기 실적 보고서 요약해줘"
```

**복합 질의 (DB + 문서 동시 검색):**
```
"매출이 떨어진 제품이 있어? 관련 보고서도 있으면 찾아줘"
"신규 고객 수 추이를 보여주고, 마케팅 보고서에서 관련 분석도 찾아줘"
```

**데이터 현황 파악:**
```
"지금 어떤 테이블들이 있어?"
"sales 테이블 구조 보여줘"
"등록된 문서가 몇 개야?"
```

### 마트 구축하기

채팅으로 분석용 테이블(마트)을 만들 수 있습니다.

**마트 생성:**
```
"월간 제품별 매출 마트 만들어줘"
"고객별 최근 3개월 구매 이력 테이블 만들어줘"
"지역별/카테고리별 매출 요약 테이블 만들어줘"
```

**마트 관리:**
```
"지금 만들어진 마트 목록 보여줘"
"mart_monthly_sales 마트 삭제해줘"
"mart_customer_history 갱신 주기를 주 1회로 바꿔줘"
```

> 모든 마트는 `mart_` 접두사로 생성됩니다.
> 에이전트는 기존 원본 테이블을 변경할 수 없습니다.

### 배치 작업 등록하기

반복적인 데이터 작업을 자동화할 수 있습니다.

**배치 등록:**
```
"매일 아침 7시에 전일 매출 요약 만들어줘"
"매주 월요일에 주간 보고서용 데이터를 정리해줘"
"매월 1일에 전월 마감 테이블을 갱신해줘"
```

**배치 관리:**
```
"등록된 배치 목록 보여줘"
"daily_sales_summary 배치 실행 이력 보여줘"
"weekly_report 배치 중지해줘"
```

### 승인이 필요한 작업

아래 작업은 에이전트가 SQL을 생성한 뒤, **사용자가 직접 확인하고 승인**해야 실행됩니다.

| 작업 | 승인 필요 | 설명 |
|---|---|---|
| **데이터 조회 (SELECT)** | ❌ 불필요 | 바로 실행. 읽기만 하므로 안전 |
| **문서 검색** | ❌ 불필요 | 바로 실행 |
| **마트 생성 (CREATE TABLE)** | ✅ **필요** | SQL 확인 후 [승인] 클릭 |
| **마트 데이터 적재 (INSERT)** | ✅ **필요** | SQL 확인 후 [승인] 클릭 |
| **배치 작업 등록** | ✅ **필요** | SQL + 스케줄 확인 후 [등록] 클릭 |
| **배치 작업 삭제** | ✅ **필요** | 확인 후 [삭제] 클릭 |

### 기존 DB 연결하기 (선택)

이미 사용 중인 DB가 있으면 연결할 수 있습니다.

```bash
vi .env

# 기존 DB 연결 (PostgreSQL, MySQL, MSSQL, Oracle 등)
EXTERNAL_DB_TYPE=mysql
EXTERNAL_DB_HOST=192.168.1.100
EXTERNAL_DB_PORT=3306
EXTERNAL_DB_NAME=company_db
EXTERNAL_DB_USER=readonly_user
EXTERNAL_DB_PASSWORD=xxxxx

# 재시작
docker compose restart
```

> ⚠️ 외부 DB는 **읽기 전용(SELECT만)**으로 연결됩니다.
> 기존 DB의 데이터를 변경하거나 삭제하지 않습니다.
> 마트는 ADF 내부 PostgreSQL에만 생성됩니다.

---

## Architecture

```
agentic-data-factory/
├── docker-compose.yml           # 전체 실행 (PostgreSQL, ChromaDB, Ollama, App)
├── .env.example                 # 환경 변수 템플릿
├── Dockerfile                   # 앱 이미지
│
├── watcher/                     # 👁️ 파일 감시
│   ├── file_watcher.py          #   공유 폴더 변경 감지 (watchdog)
│   ├── webhook.py               #   Webhook API (FTP/SFTP 훅, 외부 시스템 연동)
│   ├── classifier.py            #   파일 유형 자동 분류
│   └── loader/                  #   유형별 로더
│       ├── excel_loader.py      #     엑셀 → DB
│       ├── csv_loader.py        #     CSV → DB
│       ├── json_loader.py       #     JSON → DB
│       └── document_loader.py   #     PDF/HWP/DOCX → 문서 검색 등록
│
├── agent/                       # 🤖 AI 에이전트
│   ├── orchestrator.py          #   질의 분석 → 적절한 에이전트 호출
│   ├── sql_agent.py             #   자연어 → SQL 생성 → 실행 (SELECT)
│   ├── doc_agent.py             #   자연어 → 문서 검색 → 요약
│   ├── mart_builder.py          #   마트 설계 (DDL + INSERT SQL 생성)
│   ├── scheduler.py             #   배치 작업 등록/관리 (cron)
│   └── tools/                   #   에이전트 도구
│       ├── query_db.py          #     DB 조회 (SELECT only)
│       ├── search_docs.py       #     문서 검색
│       ├── list_tables.py       #     테이블/문서 목록 조회
│       ├── create_mart.py       #     마트 생성 (승인 후 실행)
│       └── manage_jobs.py       #     배치 작업 CRUD
│
├── approval/                    # ✅ 승인 레이어
│   ├── approval_manager.py      #   승인 요청/처리/이력 관리
│   ├── sql_validator.py         #   SQL 안전성 검증 (화이트리스트)
│   └── audit_log.py             #   모든 승인/거부/실행 이력 기록
│
├── rag/                         # 📄 문서 처리
│   ├── parser/                  #   문서 파서
│   │   ├── pdf_parser.py
│   │   ├── hwp_parser.py        #   한글 문서
│   │   └── docx_parser.py
│   ├── chunker.py               #   텍스트 분할
│   └── embedder.py              #   텍스트 → 벡터 변환
│
├── catalog/                     # 📋 데이터 목록 (자동 생성)
│   └── catalog.py               #   테이블/문서/마트/배치 메타데이터 관리
│
├── ui/                          # 💬 채팅 UI + 승인 UI
│   ├── app.py                   #   Streamlit 웹 인터페이스
│   ├── chat.py                  #   채팅 화면
│   └── approval_ui.py           #   승인 요청 목록 + 승인/거부 버튼
│
├── jobs/                        # ⏰ 배치 작업 저장소
│   ├── job_store.py             #   등록된 배치 작업 관리
│   └── job_runner.py            #   배치 실행 엔진
│
├── sample_data/                 # 📦 샘플 데이터 (테스트용)
│   ├── sales_sample.csv
│   ├── products_sample.xlsx
│   └── sample_report.pdf
│
└── tests/
```

### 사용하는 기술

서버 안에서 Docker 컨테이너 4개가 실행됩니다.

| 컨테이너 | 역할 | 비고 |
|---|---|---|
| **App** | 파일 감시 + AI 에이전트 + 승인 + 채팅 UI | Python |
| **PostgreSQL** | 정형 데이터 + 마트 + 감사 로그 | 경량 DB |
| **ChromaDB** | 문서 검색용 저장소 | 경량 벡터 DB |
| **Ollama** | AI 모델 실행 (사내망 안에서) | 로컬 LLM |

---

<h2 id="configuration">Configuration</h2>

### .env 기본 설정

```bash
# === 감시할 폴더 ===
WATCH_DIR=/mnt/shared/data         # 공유 폴더 경로 (가장 중요한 설정)

# === DB ===
POSTGRES_DB=adf
POSTGRES_USER=adf
POSTGRES_PASSWORD=changeme         # 반드시 변경하세요

# === AI 모델 ===
LLM_MODEL=exaone3.5:7.8b          # 한국어 최적 모델
# LLM_MODEL=qwen2.5:7b            # 대안

# === 에이전트 ===
AGENT_MAX_QUERY_ROWS=5000         # 한 번에 조회 가능한 최대 행 수
AGENT_QUERY_TIMEOUT=30            # 쿼리 타임아웃 (초)

# === 마트/배치 ===
MART_PREFIX=mart_                  # 마트 테이블 접두사 (변경 비권장)
JOB_LOG_DIR=./logs/jobs            # 배치 실행 로그 저장 경로

# === Webhook API ===
WEBHOOK_ENABLED=true               # FTP/SFTP 훅, 외부 시스템 연동용
WEBHOOK_SECRET=changeme            # Webhook 인증 토큰 (반드시 변경하세요)

# === 외부 DB 연결 (선택) ===
# EXTERNAL_DB_TYPE=mysql
# EXTERNAL_DB_HOST=192.168.1.100
# EXTERNAL_DB_PORT=3306
# EXTERNAL_DB_NAME=company_db
# EXTERNAL_DB_USER=readonly_user
# EXTERNAL_DB_PASSWORD=xxxxx
```

### 파일 분류 규칙 커스터마이징 (선택)

기본 규칙으로 대부분 동작하지만, 필요하면 커스터마이징할 수 있습니다.

```yaml
# config/classification.yaml
rules:
  # 정형 데이터 → DB 적재
  - patterns: ["*.xlsx", "*.xls", "*.csv", "*.tsv", "*.json"]
    action: load_to_db

  # 비정형 문서 → 검색 등록
  - patterns: ["*.pdf", "*.hwp", "*.hwpx", "*.docx", "*.txt"]
    action: register_for_search

  # 무시할 파일
  - patterns: ["~$*", "*.tmp", "Thumbs.db"]
    action: ignore
```

### AI 모델 선택

| 모델 | GPU 필요 | 한국어 | 응답 속도 (CPU) | 비고 |
|---|---|---|---|---|
| `exaone3.5:7.8b` | 아니오 (GPU 권장) | ⭐⭐⭐ | 10~30초 | **기본값. 한국어 최적** |
| `qwen2.5:7b` | 아니오 (GPU 권장) | ⭐⭐ | 10~30초 | 다국어 범용 |
| `exaone3.5:32b` | 예 (24GB+) | ⭐⭐⭐ | 2~5초 | 더 정확한 한국어 |
| `qwen2.5:32b` | 예 (24GB+) | ⭐⭐⭐ | 2~5초 | 고품질 대안 |

> 💡 GPU 없이도 됩니다. 다만 응답이 느립니다.
> 처음에 CPU로 써보고, 유용하면 GPU를 추가하는 걸 추천합니다.

<h3 id="ftp-sftp-hook">FTP/SFTP 서버 훅 설정 (선택)</h3>

FTP/SFTP 서버에서 파일 업로드가 완료되면 DataBridge에 자동으로 알리도록 설정합니다.
서버 종류에 따라 아래 중 하나를 선택하세요.

**ProFTPD (FTP/FTPS)**

```apache
# /etc/proftpd/conf.d/databridge-hook.conf
<IfModule mod_exec.c>
  ExecOnCommand STOR,STOU "/usr/local/bin/notify_databridge.sh %f"
</IfModule>
```

**vsftpd + incron (FTP/FTPS)**

vsftpd는 자체 훅이 없으므로, `incron`으로 업로드 디렉토리를 감시합니다.

```bash
# incron 설치
sudo apt install incron

# /etc/incron.d/databridge
/var/ftp/upload IN_CLOSE_WRITE /usr/local/bin/notify_databridge.sh $@/$#
```

**OpenSSH SFTP**

```bash
# /etc/ssh/sshd_config
Subsystem sftp internal-sftp
Match Group dataupload
    ForceCommand internal-sftp -l INFO
    ChrootDirectory /data/sftp/%u

# SFTP 로그를 incron으로 감시하거나, 업로드 디렉토리를 직접 감시
# /etc/incron.d/databridge-sftp
/data/sftp IN_CLOSE_WRITE,recursive /usr/local/bin/notify_databridge.sh $@/$#
```

**공통 알림 스크립트 (모든 FTP/SFTP 서버에서 사용)**

```bash
#!/bin/bash
# /usr/local/bin/notify_databridge.sh
FILEPATH="$1"
WEBHOOK_URL="http://databridge서버IP:8501/api/webhook/file-uploaded"
WEBHOOK_SECRET="changeme"    # .env의 WEBHOOK_SECRET과 동일하게

curl -s -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $WEBHOOK_SECRET" \
  -d "{\"path\": \"$FILEPATH\", \"source\": \"ftp\"}"
```

```bash
# 실행 권한 부여
chmod +x /usr/local/bin/notify_databridge.sh
```

> 💡 **FTP 서버와 DataBridge가 같은 머신이라면?**
> FTP 업로드 디렉토리를 `WATCH_DIR`에 직접 지정하면 됩니다. 훅 설정 없이 자동 감지됩니다.
> 예: `WATCH_DIR=/var/ftp/upload`

**Webhook API 직접 호출** (기존 시스템 연동)

FTP/SFTP 외에도, 어떤 시스템이든 HTTP로 호출할 수 있습니다.

```bash
# 단일 파일 알림
curl -X POST http://databridge:8501/api/webhook/file-uploaded \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $WEBHOOK_SECRET" \
  -d '{"path": "/data/upload/sales_0210.csv", "source": "legacy_batch"}'

# 응답
# {"status": "accepted", "file": "sales_0210.csv"}
```

| API 엔드포인트 | 메서드 | 설명 |
|---|---|---|
| `/api/webhook/file-uploaded` | POST | 파일 업로드 알림. 분류 → 적재 파이프라인 실행 |
| `/api/webhook/health` | GET | Webhook API 상태 확인 |

---

## 보안

### 쓰기 작업 안전장치

| 항목 | 설명 |
|---|---|
| **승인 필수** | 마트 생성, 배치 등록 등 모든 쓰기 작업은 사용자 승인 후 실행 |
| **SQL 화이트리스트** | `CREATE TABLE mart_*`, `INSERT INTO mart_*`, `CREATE VIEW mart_*`, `CREATE INDEX`만 허용 |
| **차단 목록** | `DROP`, `TRUNCATE`, `DELETE`, `UPDATE`, `ALTER` — 원천 차단 |
| **네이밍 규칙** | 마트는 반드시 `mart_` 접두사. 기존 테이블명 사용 불가 |
| **실행 제한** | 배치 SQL 타임아웃, 결과 행 수 제한 |
| **감사 로그** | 모든 승인/거부/실행 이력을 기록. 누가, 언제, 무슨 SQL을 실행했는지 추적 |

```
에이전트가 할 수 있는 것           에이전트가 할 수 없는 것
─────────────────────             ─────────────────────
✅ SELECT (모든 테이블)            ❌ DROP / TRUNCATE
✅ CREATE TABLE mart_*             ❌ DELETE / UPDATE
✅ INSERT INTO mart_*              ❌ ALTER TABLE
✅ CREATE VIEW mart_*              ❌ 원본 테이블에 쓰기
✅ CREATE INDEX                    ❌ 외부 DB에 쓰기
✅ 배치 등록 (승인 후)             ❌ 승인 없이 실행
```

---

## FAQ

**Q: Docker를 몰라도 되나요?**  
설치할 때 명령어 3개만 입력하면 됩니다. Docker 자체를 다룰 필요는 없습니다.

**Q: 인터넷이 안 되는 환경에서도 쓸 수 있나요?**  
최초 설치 시에만 인터넷이 필요합니다 (프로그램과 AI 모델 다운로드). 이후에는 인터넷 없이 동작합니다.

**Q: 기존 공유 폴더 구조를 바꿔야 하나요?**  
아닙니다. 기존 폴더 구조 그대로 사용하면 됩니다. 해당 폴더를 "감시"만 하므로 기존 파일이나 구조에 영향을 주지 않습니다.

**Q: 엑셀 파일을 수정하면 DB도 업데이트되나요?**  
파일이 변경되면 감지하여 DB를 갱신합니다. 기존 데이터를 덮어쓸지, 이력을 남길지는 설정에서 선택할 수 있습니다.

**Q: 어떤 DB(MySQL, Oracle 등)든 연결할 수 있나요?**  
v0는 PostgreSQL만 지원합니다.

**Q: 데이터가 많아지면 느려지나요?**  
v0.1은 수만~수십만 행 규모를 고려하고 구현하였습니다.  
그 이상의 데이터를 다뤄야 할 경우에는 다른 서비스를 고려해보시는 것을 추천드립니다.

**Q: 여러 명이 동시에 쓸 수 있나요?**  
채팅 UI에 여러 명이 동시 접속할 수 있습니다. 관리자는 사용자의 권한을 제어 가능합니다.

**Q: 에이전트가 실수로 데이터를 날릴 수 있나요?**  
없습니다. 에이전트는 `mart_` 접두사 테이블에만 쓸 수 있고, `DROP`/`DELETE` 등 파괴적 SQL은 원천 차단됩니다. 원본 테이블과 외부 DB에는 어떤 경우에도 쓸 수 없습니다. 그리고 쓰기 작업은 항상 사용자 승인이 필요합니다.  
추후 영구삭제/복원/임시삭제 등의 작업도 지원을 고려하고 있습니다.

**Q: 배치 작업이 실패하면 어떻게 되나요?**  
실행 이력과 에러 로그가 저장되며, 채팅에서 `"배치 실행 이력 보여줘"`로 확인할 수 있습니다. 실패한 배치는 자동 재시도하지 않으며, 원인 확인 후 수동으로 재실행할 수 있습니다.

**Q: FTP/SFTP로 데이터를 받고 있는데 연동할 수 있나요?**  
예. FTP/SFTP 서버에 업로드 완료 훅을 설정하면, 파일이 올라오는 즉시 DataBridge가 자동으로 처리합니다. ProFTPD, vsftpd, OpenSSH SFTP 모두 지원하는 것을 목표로 합니다.

**Q: FTP 서버와 DataBridge가 같은 서버에 있어야 하나요?**  
아닙니다. 같은 서버에 있으면 FTP 업로드 디렉토리를 직접 감시할 수 있어 가장 간단하지만 그렇지 않더라도 Webhook API를 통해 연동할 수 있습니다.

**Q: 우리는 이걸 관리할 만한 개발자도 없는데, 관리 작업도 AI에게 넘길 수 있을까요?**  
현재 시스템은 관리자가 SQL이나 인프라 구조를 어느 정도 이해한다는 전제하에 설계되었습니다. 하지만 정말 관리자를 두기 어려운 상황이라면, 상용모델을 활용하여 관리자 에이전트를 설계해 활용하는 것이 가능합니다.  
관리자 에이전트 구현은 AdminAgent.md 를 참고하시길 바랍니다.
하지만 개인적으로 AI에게 모든 관리 권한을 넘기는 것은 추천하지 않습니다.

---

## License

[MIT License](LICENSE)

---

## Acknowledgements

- [Ollama](https://ollama.ai/) — 로컬 AI 모델 실행
- [PostgreSQL](https://www.postgresql.org/) — 데이터베이스
- [ChromaDB](https://www.trychroma.com/) — 문서 검색 엔진
- [LangGraph](https://github.com/langchain-ai/langgraph) — AI 에이전트 프레임워크
- [Streamlit](https://streamlit.io/) — 채팅 UI
- [watchdog](https://github.com/gorakhargosh/watchdog) — 파일 변경 감지할

---

<p align="center">
  <sub>데이터 때문에 고통받는 모든 사람들을 위해. ❤️</sub>
</p>
