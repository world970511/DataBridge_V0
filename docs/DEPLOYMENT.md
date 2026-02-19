# DataBridge 운영 배포 가이드

이 문서는 DataBridge를 운영 환경에 배포하기 위한 설정 및 점검 절차를 설명합니다.

---

## 시스템 아키텍처

```
┌─ 서버 (Linux/Windows) ───────────────────────────────────────┐
│                                                              │
│  ┌─ Docker ────────────────────────────────────────────┐     │
│  │  ┌─────────┐  ┌─────────────┐  ┌─────────────┐     │     │
│  │  │   App   │  │ PostgreSQL  │  │  ChromaDB   │     │     │
│  │  │ :8501   │  │   :5432     │  │   :8000     │     │     │
│  │  └────┬────┘  └─────────────┘  └─────────────┘     │     │
│  │       │                                            │     │
│  └───────┼────────────────────────────────────────────┘     │
│          │ host.docker.internal:11434                       │
│          ▼                                                  │
│  ┌─────────────┐                                            │
│  │   Ollama    │  ◀── 시스템 서비스 (Docker 외부)            │
│  │   :11434    │      GPU 직접 접근, 효율적 메모리 관리       │
│  └─────────────┘                                            │
└──────────────────────────────────────────────────────────────┘
         ▲
    웹 브라우저 (http://서버IP:8501)
         ▲
┌─ 사용자 PC ─────┐
│  설치 필요 없음  │
└─────────────────┘
```

**핵심 포인트:**
- **Docker 컨테이너**: App, PostgreSQL, ChromaDB (3개)
- **시스템 서비스**: Ollama (Docker 외부에서 실행)
- **사용자**: 웹 브라우저만 있으면 됨 (설치 불필요)

> ⚠️ **Ollama를 Docker 외부에서 실행하는 이유:**
> - GPU 패스스루 설정 없이 GPU 직접 사용 가능
> - Docker 메모리 제한에 영향받지 않음
> - 모델 로딩/언로딩이 빠르고 안정적

---

## 목차

1. [필수 변경 항목](#1-필수-변경-항목)
2. [환경별 설정](#2-환경별-설정)
3. [Ollama 서비스 운영화](#3-ollama-서비스-운영화)
4. [네트워크 설정](#4-네트워크-설정)
5. [배포 점검 시나리오](#5-배포-점검-시나리오)
6. [문제 해결](#6-문제-해결)

---

## 1. 필수 변경 항목

`.env.example`을 복사하여 `.env` 파일을 생성한 후, 아래 항목들을 **반드시** 운영 환경에 맞게 변경하세요.

### 1.1 보안 관련 (필수)

| 변수명 | 기본값 (개발용) | 운영 환경 권장 |
|--------|----------------|---------------|
| `ADMIN_PASSWORD` | `admin1234` | 12자 이상, 대소문자+숫자+특수문자 조합 |
| `SECRET_KEY` | `databridge-secret-key-change-me` | 무작위 64자 이상 |
| `WEBHOOK_SECRET` | `changeme` | 무작위 32자 이상 |
| `POSTGRES_PASSWORD` | `admin1234` | 강력한 비밀번호 |

#### 비밀키 생성 방법

**Linux/macOS:**
```bash
# SECRET_KEY 생성 (64자 hex)
openssl rand -hex 32

# WEBHOOK_SECRET 생성 (32자 hex)
openssl rand -hex 16

# 또는 Python 사용
python -c "import secrets; print(secrets.token_hex(32))"
```

**Windows (PowerShell):**
```powershell
# SECRET_KEY 생성
-join ((1..64) | ForEach-Object { '{0:X}' -f (Get-Random -Maximum 16) })

# 또는 Python 사용
python -c "import secrets; print(secrets.token_hex(32))"
```

### 1.2 경로 설정 (필수)

| 변수명 | 설명 | 예시 |
|--------|------|------|
| `WATCH_DIR_HOST` | 감시할 공유 폴더의 **호스트 경로** | `/mnt/shared/data` (Linux)<br>`C:\SharedData` (Windows) |

> **중요**: `WATCH_DIR`은 컨테이너 내부 경로(`/data`)이므로 변경하지 마세요.
> Docker가 `WATCH_DIR_HOST` → `/data`로 마운트합니다.

### 1.3 선택 항목

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `LLM_MODEL` | `exaone3.5:7.8b` | 사용할 Ollama 모델 |
| `AGENT_MAX_QUERY_ROWS` | `5000` | SQL 조회 최대 행 수 |
| `AGENT_QUERY_TIMEOUT` | `30` | SQL 타임아웃 (초) |
| `APP_PORT` | `8501` | 웹 UI 포트 |

---

## 2. 환경별 설정

### 2.1 Linux 서버 (권장)

```bash
# .env 파일 예시
WATCH_DIR_HOST=/mnt/nas/shared_data
WATCH_DIR=/data

POSTGRES_DB=databridge
POSTGRES_USER=databridge
POSTGRES_PASSWORD=Str0ng!P@ssw0rd#2024

OLLAMA_HOST=http://localhost:11434
LLM_MODEL=exaone3.5:7.8b

ADMIN_PASSWORD=Adm!n#Secure2024
SECRET_KEY=a3f8c2d1e5b9a7c4f6d8e2b1a9c3f5d7e8b2a4c6f1d9e3b5a7c9f2d4e6b8a1c3
WEBHOOK_SECRET=7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c
```

#### 경로 권한 확인

```bash
# 감시 폴더 존재 확인
ls -la /mnt/nas/shared_data

# Docker가 읽을 수 있는지 확인
# (Docker는 root로 실행되므로 일반적으로 문제없음)
sudo docker run --rm -v /mnt/nas/shared_data:/test:ro alpine ls /test
```

### 2.2 Windows 서버

```bash
# .env 파일 예시
WATCH_DIR_HOST=C:/SharedData
WATCH_DIR=/data

POSTGRES_DB=databridge
POSTGRES_USER=databridge
POSTGRES_PASSWORD=Str0ng!P@ssw0rd#2024

# Windows Docker Desktop: host.docker.internal 자동 지원
OLLAMA_HOST=http://localhost:11434
LLM_MODEL=exaone3.5:7.8b

ADMIN_PASSWORD=Adm!n#Secure2024
SECRET_KEY=a3f8c2d1e5b9a7c4f6d8e2b1a9c3f5d7e8b2a4c6f1d9e3b5a7c9f2d4e6b8a1c3
WEBHOOK_SECRET=7f8a9b2c3d4e5f6a7b8c9d0e1f2a3b4c
```

> **Windows 경로 주의**: 백슬래시(`\`) 대신 슬래시(`/`) 사용
> 예: `C:\Users\Data` → `C:/Users/Data`

### 2.3 개발 환경 (Docker Desktop)

```bash
# 개발용 .env (sample_data 사용)
WATCH_DIR_HOST=./sample_data
WATCH_DIR=/data

# 개발 환경에서는 기본값 사용 가능
POSTGRES_PASSWORD=admin1234
ADMIN_PASSWORD=admin1234
```

---

## 3. Ollama 서비스 운영화

Ollama는 Docker 외부에서 실행되므로, 시스템 부팅 시 자동 시작되도록 설정해야 합니다.

### 3.1 Linux (systemd)

```bash
# 1. 서비스 파일 복사
sudo cp scripts/ollama.service /etc/systemd/system/

# 2. 서비스 등록 및 시작
sudo systemctl daemon-reload
sudo systemctl enable ollama
sudo systemctl start ollama

# 3. 상태 확인
sudo systemctl status ollama
```

**서비스 파일 내용** (`scripts/ollama.service`):
```ini
[Unit]
Description=Ollama LLM Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/ollama serve
Restart=always
RestartSec=3
Environment="OLLAMA_HOST=0.0.0.0"

[Install]
WantedBy=multi-user.target
```

### 3.2 Windows

**방법 A: 시작 프로그램 등록**
1. `Win + R` → `shell:startup` 입력
2. `ollama serve` 실행 배치 파일 생성:
   ```batch
   @echo off
   start /B ollama serve
   ```

**방법 B: NSSM 서비스 등록 (권장)**
```powershell
# 1. NSSM 설치 (관리자 PowerShell)
choco install nssm

# 2. 서비스 등록
nssm install Ollama "C:\Users\<사용자>\AppData\Local\Programs\Ollama\ollama.exe" serve

# 3. 서비스 시작
nssm start Ollama
```

### 3.3 모델 다운로드

```bash
# 권장 모델 (한국어 최적)
ollama pull exaone3.5:7.8b

# 대안 모델
ollama pull qwen2.5:7b

# 모델 확인
ollama list
```

---

## 4. 네트워크 설정

### 4.1 컨테이너 → Ollama 연결

`docker-compose.yml`에서 앱 컨테이너는 `host.docker.internal`로 호스트의 Ollama에 접근합니다.

| 환경 | `host.docker.internal` 지원 |
|------|---------------------------|
| Docker Desktop (Win/Mac) | 기본 지원 |
| Linux Docker Engine | 추가 설정 필요 |
| WSL2 | 대부분 지원 |

#### Linux Docker Engine 설정

`docker-compose.yml`에 아래 추가:
```yaml
services:
  app:
    # ... 기존 설정 ...
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

### 4.2 네트워크 연결 테스트

```bash
# 스크립트로 테스트
python scripts/check_network.py

# 수동 테스트
# 1. 호스트에서 Ollama 확인
curl http://localhost:11434/api/tags

# 2. 컨테이너 내부에서 확인
docker compose exec app curl http://host.docker.internal:11434/api/tags
```

### 4.3 방화벽 설정

내부망에서 접근이 필요한 포트:

| 포트 | 서비스 | 외부 노출 |
|------|--------|----------|
| 8501 | Web UI (Streamlit) | 필요 |
| 5432 | PostgreSQL | 불필요 (내부용) |
| 8000 | ChromaDB | 불필요 (내부용) |
| 11434 | Ollama | 불필요 (로컬만) |

---

## 5. 배포 점검 시나리오

배포 후 아래 체크리스트를 순서대로 수행하세요.

### 5.1 사전 준비

```
[ ] Ollama 설치 완료
[ ] 모델 다운로드: ollama pull exaone3.5:7.8b
[ ] Ollama 서비스 실행 중: ollama serve 또는 systemctl status ollama
[ ] .env 파일 설정 완료 (보안 값 변경)
[ ] WATCH_DIR_HOST 경로 존재 및 권한 확인
```

### 5.2 서비스 시작

```bash
# 1. 서비스 시작
docker compose up -d

# 2. 로그 확인 (에러 없는지)
docker compose logs -f

# 3. 컨테이너 상태 확인
docker compose ps
# 모든 서비스가 "healthy" 상태여야 함
```

### 5.3 연결 확인

```
[ ] 브라우저에서 http://<서버IP>:8501 접속
[ ] 로그인: admin / [설정한 ADMIN_PASSWORD]
[ ] 로그인 성공 후 채팅 페이지 표시
```

### 5.4 기능 테스트

#### 파일 감시 테스트
```bash
# 1. 감시 폴더에 테스트 CSV 복사
cp sample_data/sales_sample.csv /mnt/nas/shared_data/

# 2. 채팅에서 확인 (1분 이내 등록됨)
# "등록된 테이블 보여줘"
# "sales_sample 테이블 내용 보여줘"
```

#### SQL 조회 테스트
```
채팅 입력: "sales_sample에서 총 매출 합계 구해줘"
기대 결과: SQL 실행 결과 + 자연어 요약
```

#### 문서 검색 테스트
```bash
# 1. PDF 문서 복사
cp sample_data/sample_report.pdf /mnt/nas/shared_data/

# 2. 채팅에서 검색
# "보고서에서 매출 관련 내용 찾아줘"
```

### 5.5 승인 워크플로우 테스트

```
[ ] 채팅: "월별 매출 마트 만들어줘"
[ ] 응답에서 승인 요청 메시지 확인
[ ] 사이드바 → "승인 관리" 클릭
[ ] 대기 중인 요청 목록에 표시 확인
[ ] [승인 및 실행] 또는 [거부] 버튼 동작 확인
```

### 5.6 최종 체크리스트

```
[ ] 파일 업로드 → DB 적재 정상
[ ] SQL 조회 정상 (SELECT)
[ ] 문서 검색 정상 (RAG)
[ ] 승인 워크플로우 정상
[ ] 감사 로그 기록 확인 (DB: audit_log 테이블)
```

---

## 6. 문제 해결

### 6.1 Ollama 연결 실패

**증상**: 채팅 응답이 없거나 "LLM 연결 실패" 메시지

**확인**:
```bash
# 1. Ollama 실행 중인지 확인
curl http://localhost:11434/api/tags

# 2. 컨테이너에서 연결 가능한지 확인
docker compose exec app curl http://host.docker.internal:11434/api/tags
```

**해결**:
- Ollama 서비스 재시작: `systemctl restart ollama`
- Linux의 경우 `extra_hosts` 설정 확인

### 6.2 파일 감시 안 됨

**증상**: 폴더에 파일을 넣어도 DB에 등록되지 않음

**확인**:
```bash
# 1. 마운트 확인
docker compose exec app ls -la /data

# 2. 파일 감시 로그 확인
docker compose logs app | grep -i watcher
```

**해결**:
- `WATCH_DIR_HOST` 경로 확인
- 볼륨 마운트 권한 확인 (`:ro` 읽기 전용 정상)

### 6.3 DB 연결 실패

**증상**: "PostgreSQL 연결 실패" 에러

**확인**:
```bash
# 1. DB 컨테이너 상태
docker compose ps postgres

# 2. DB 로그
docker compose logs postgres
```

**해결**:
- `.env`의 `POSTGRES_*` 설정 확인
- `docker compose down -v && docker compose up -d` (볼륨 초기화)

### 6.4 메모리 부족

**증상**: 컨테이너가 자주 재시작되거나 OOM 에러

**확인**:
```bash
docker stats
```

**해결**:
- 최소 16GB RAM 권장
- Ollama 모델을 더 작은 것으로 변경 (예: `qwen2.5:3b`)
- `AGENT_MAX_QUERY_ROWS` 값 줄이기

---

## 부록: 운영 .env 템플릿

```bash
# ===========================================
# DataBridge 운영 환경 설정
# ===========================================

# === 감시 폴더 ===
WATCH_DIR_HOST=/mnt/nas/shared_data    # 실제 경로로 변경
WATCH_DIR=/data

# === 데이터베이스 ===
POSTGRES_DB=databridge
POSTGRES_USER=databridge
POSTGRES_PASSWORD=<강력한_비밀번호>     # 필수 변경
POSTGRES_PORT=5432

# === ChromaDB ===
CHROMA_PORT=8000

# === AI 모델 ===
OLLAMA_HOST=http://localhost:11434
OLLAMA_PORT=11434
LLM_MODEL=exaone3.5:7.8b

# === 에이전트 ===
AGENT_MAX_QUERY_ROWS=5000
AGENT_QUERY_TIMEOUT=30

# === 마트/배치 ===
MART_PREFIX=mart_
JOB_LOG_DIR=./logs/jobs

# === Webhook ===
WEBHOOK_ENABLED=true
WEBHOOK_SECRET=<무작위_32자>            # 필수 변경

# === 인증 ===
ADMIN_PASSWORD=<강력한_비밀번호>        # 필수 변경
SECRET_KEY=<무작위_64자>                # 필수 변경

# === 앱 ===
APP_PORT=8501
```

---

*문서 버전: 1.0*
*최종 수정: 2024-02*
