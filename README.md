<p align="center">
  <h1 align="center">DataBridge</h1>
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
✅ 사진/이미지가 쌓여가는데 비슷한 사진을 찾거나 정리하기 힘들다
✅ 부서별 분석 테이블을 수작업으로 만들고, 배치 스크립트로 갱신하고 있다
✅ 전담 데이터 엔지니어가 없거나 1~2명이다
✅ 수천만 행, 수억 행 규모의 빅데이터는 아니지만, 관리가 점점 힘들어지고 있다
```

- AI의 발전이 늘어나면서 데이터 수집 및 관리의 필요성은 늘어났지만, 대부분의 서비스가 수천만 또는 억 단위의 빅데이터에 집중해 구현된 것이 아쉬웠습니다.
- 개인적인 경험으로, 빅데이터가 아니더라도 사람이 다루기는 어려운 데이터의 양을 AI를 사용하여 데이터를 수집하고 관리할 수 있으면 좋겠다는 생각으로 이 프로젝트를 진행하였습니다.

---

## 주요 기능

| 기능 | 설명 |
|---|---|
| **자동 파일 감시** | 공유 폴더에 파일을 넣으면 자동 분류 → DB 적재 또는 검색 등록 |
| **AI 채팅 조회** | 자연어로 데이터 조회 (SQL 자동 생성), 문서 검색, 복합 질의 |
| **이미지 AI 분석** | 유사 이미지 검색, 자동 그룹핑, 중복 탐지, EXIF 메타데이터 |
| **마트 구축** | 채팅으로 분석용 테이블 생성 요청 → 관리자 승인 후 실행 |
| **배치 작업** | 크론 기반 반복 SQL 작업 등록·관리 |
| **삭제 승인** | 데이터 삭제 시 관리자 승인 필요 (Human-in-the-Loop) |
| **멀티 LLM** | Ollama (폐쇄망) + OpenAI, Anthropic, HuggingFace (상용) |
| **인증** | 로그인 필수, 관리자/사용자 역할 구분 |

### 지원 파일 형식

| 정형 데이터 (→ DB) | 비정형 문서 (→ 검색) | 이미지 (→ AI 분석) |
|---|---|---|
| xlsx, xls, csv, tsv, json | pdf, hwp, hwpx, docx, doc, pptx, ppt | jpg, png, gif, bmp, tiff, webp |

---

## 빠른 시작

### 준비물

| 항목 | 조건 |
|---|---|
| **서버** | Linux (Ubuntu 22.04+) 또는 Windows |
| **RAM** | 16 GB 이상 (이미지 처리 시 32 GB 권장) |
| **Docker** | Docker Compose v2.20+ |
| **Ollama** | v0.3 이상 (폐쇄망) 또는 상용 API 키 |

### 설치

```bash
# 1. 다운로드
git clone https://github.com/world970511/DataBridge_V0.git
cd DataBridge_V0

# 2. 환경 설정
cp .env.example .env
vi .env                           # WATCH_DIR, 비밀번호, LLM 설정

# 3. Ollama 설치 + 모델 다운로드 (폐쇄망인 경우)
ollama serve &
ollama pull gemma2:2b

# 4. 실행
docker compose up -d
```

### 접속

```
웹 브라우저에서 http://서버IP:8501
초기 관리자 비밀번호: .env의 ADMIN_PASSWORD
```

> 📖 상세 설치 가이드: [Wiki — 설치 가이드](../../wiki/설치-가이드)

---

## 사용 예시

**데이터 조회:**
```
👤 "이번 달 매출 보여줘"
🤖 [SQL 생성 → 실행]
   총 매출: ₩45,200,000 (1,247건)
```

**문서 검색:**
```
👤 "보안 관련 보고서 찾아줘"
🤖 [벡터 검색]
   '기술검토_보안.hwp' (p.3): "접근 제어 정책..."
```

**이미지 분석:**
```
👤 "현장사진_001과 비슷한 사진 찾아줘"
🤖 [DINOv2 유사 검색]
   유사 이미지 3건:
   - 현장사진_003.jpg (94.2%)
   - 현장사진_007.jpg (91.8%)
```

**마트 생성:**
```
👤 "월간 제품별 매출 마트 만들어줘"
🤖 CREATE TABLE mart_monthly_sales AS SELECT ...
   ⏸️ [승인] [SQL 수정] [취소]
```

> 📖 전체 사용법: [Wiki — 사용법](../../wiki/사용법)

---

## 전체 구조

```
기존 환경 (바꿀 것 없음)            DataBridge (서버 1대)
┌──────────────────┐           ┌────────────────────────────┐
│                  │           │                            │
│  📁 공유 폴더    │──파일──▶   │  👁️ 파일 감시 (watchdog)    │
│  (SAMBA/NAS)     │  올리면    │  엑셀/CSV → DB 자동 적재     │
│                  │  자동감지   │  문서 → 검색 자동 등록       │
└──────────────────┘           │  이미지 → AI 분석 자동 등록   │
                               │                            │
┌──────────────────┐           │  🤖 AI 에이전트             │
│  💻 사용자 PC    │──웹──▶   │   ├ 데이터 조회 (SQL Agent)   │
│  (설치 필요 없음) │  브라우저   │  ├ 문서 검색 (Doc Agent)     │
└──────────────────┘           │  ├ 이미지 분석 (Image Agent) │
                               │  ├ 마트 구축 (Mart Builder)  │
                               │  └ 배치 관리 (Scheduler)     │
                               │                             │
                               │  🗄️ PostgreSQL + ChromaDB   │
                               │  ✅ 승인 레이어 + 감사 로그    │
                               │  🔐 로그인 + 역할 기반 접근    │
                               └──────────────────────────────┘
```

> **기존 환경을 건드리지 않습니다.** 공유 폴더도 그대로, 옆에 서버 하나 추가하는 것뿐입니다.

---

## 기술 스택

| 구성 요소 | 역할 | 실행 방식 |
|---|---|---|
| **Streamlit** | 채팅 + 데이터 관리 + 승인 UI | Docker 컨테이너 |
| **PostgreSQL** | 정형 데이터 + 마트 + 감사 로그 | Docker 컨테이너 |
| **ChromaDB** | 문서/이미지 벡터 검색 | Docker 컨테이너 |
| **Ollama / 상용 API** | AI 모델 실행 | 시스템 서비스 또는 외부 API |
| **DINOv2 (PyTorch)** | 이미지 유사도 분석 | 앱 컨테이너 내부 |

---

## 문서

| 문서 | 내용 |
|---|---|
| [설치 가이드](../../wiki/설치-가이드) | 상세 설치, Ollama 설정, 상용 모델 설정 |
| [사용법](../../wiki/사용법) | 파일 업로드, 채팅 질의, 마트/배치, 데이터 관리, 승인 |
| [설정](../../wiki/설정) | .env 전체 옵션, 파일 분류 규칙, LLM 멀티 프로바이더 |
| [아키텍처](../../wiki/아키텍처) | 디렉토리 구조, 에이전트 시스템, 파이프라인, DB 스키마 |
| [보안](../../wiki/보안) | 인증, SQL 안전장치, 승인 레이어, 감사 로그 |
| [MCP/OpenWebUI 비교](../../wiki/비교) | 기존 도구와의 차이점 |
| [FAQ](../../wiki/FAQ) | 자주 묻는 질문 전체 |

---

## FAQ

**Q: Ollama 말고 다른 AI 모델도 쓸 수 있나요?**
예. OpenAI, Anthropic, HuggingFace 등 상용 모델을 지원합니다. 질의 분석과 데이터 처리에 각각 다른 모델을 지정할 수도 있습니다.

**Q: 이미지 파일도 처리되나요?**
예. 이미지를 넣으면 AI가 특징을 분석하여 유사 검색, 중복 탐지, 그룹핑이 가능합니다. 촬영 정보(카메라, 날짜, GPS 등)도 자동 추출됩니다.

**Q: 기존 공유 폴더 구조를 바꿔야 하나요?**
아닙니다. 폴더를 "감시"만 하므로 기존 파일이나 구조에 영향을 주지 않습니다.

**Q: 에이전트가 데이터를 날릴 수 있나요?**
없습니다. 에이전트는 `SELECT`만 실행하며, 마트 생성이나 삭제는 반드시 관리자 승인이 필요합니다.

> 📖 전체 FAQ: [Wiki — FAQ](../../wiki/FAQ)

---

## License

[MIT License](LICENSE)

## Acknowledgements

[Ollama](https://ollama.ai/) · [PostgreSQL](https://www.postgresql.org/) · [ChromaDB](https://www.trychroma.com/) · [PyTorch](https://pytorch.org/) · [DINOv2](https://github.com/facebookresearch/dinov2) · [LangChain](https://github.com/langchain-ai/langchain) · [LangGraph](https://github.com/langchain-ai/langgraph) · [Streamlit](https://streamlit.io/) · [watchdog](https://github.com/gorakhargosh/watchdog) · [Pillow](https://python-pillow.org/) · [scikit-learn](https://scikit-learn.org/)

---

<p align="center">
  <sub>데이터 때문에 고통받는 모든 사람들을 위해.</sub>
</p>
