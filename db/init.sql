-- DataBridge 초기 스키마
-- PostgreSQL 16

-- ============================================
-- 0. 사용자 관리 (인증/인가)
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(100) NOT NULL UNIQUE,
    password_hash   VARCHAR(256) NOT NULL,
    salt            VARCHAR(64) NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'user',
    -- 'admin' 또는 'user'
    display_name    VARCHAR(200),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- ============================================
-- 1. 데이터 카탈로그 (테이블/문서 메타데이터)
-- ============================================
CREATE TABLE IF NOT EXISTS catalog_tables (
    id              SERIAL PRIMARY KEY,
    table_name      VARCHAR(255) NOT NULL UNIQUE,
    source_file     TEXT,
    file_type       VARCHAR(50),
    row_count       INTEGER DEFAULT 0,
    column_count    INTEGER DEFAULT 0,
    columns_json    JSONB,
    -- Rich Catalog 메타데이터 (스마트 분류 + LLM 생성)
    description         TEXT,                               -- 데이터 설명 (LLM 생성, 예: "2월 제품별 일별 매출 데이터")
    data_category       VARCHAR(50) DEFAULT 'statistics',   -- statistics | document | reference | log
    tags                TEXT[],                             -- 검색용 태그 배열 (예: {'매출','제품','월별'})
    column_descriptions JSONB,                              -- 컬럼별 설명 (예: {"amount": "매출액(원)"})
    sample_values       JSONB,                              -- 컬럼별 샘플 값 (예: {"product": ["A상품","B상품"]})
    numeric_ratio       FLOAT,                              -- 숫자 컬럼 비율 (0.0~1.0, 스마트 분류 근거)
    avg_text_length     FLOAT,                              -- 텍스트 셀 평균 길이 (스마트 분류 근거)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 태그 기반 검색을 위한 GIN 인덱스
CREATE INDEX IF NOT EXISTS idx_catalog_tables_tags ON catalog_tables USING GIN (tags);

CREATE TABLE IF NOT EXISTS catalog_documents (
    id              SERIAL PRIMARY KEY,
    doc_name        VARCHAR(500) NOT NULL,
    source_file     TEXT NOT NULL UNIQUE,
    file_type       VARCHAR(50),
    chunk_count     INTEGER DEFAULT 0,
    collection_name VARCHAR(255),
    summary_text    TEXT,
    status          VARCHAR(20) DEFAULT 'active',
    -- active: 정상 등록, encrypted: 암호화 파일, failed: 처리 실패
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 기존 배포 마이그레이션 (catalog_documents):
-- ALTER TABLE catalog_documents ADD COLUMN IF NOT EXISTS summary_text TEXT;
-- ALTER TABLE catalog_documents ADD CONSTRAINT uq_catalog_documents_source_file UNIQUE (source_file);
-- ALTER TABLE catalog_documents ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active';

-- 기존 배포 마이그레이션 (Rich Catalog):
-- ALTER TABLE catalog_tables ADD COLUMN IF NOT EXISTS description TEXT;
-- ALTER TABLE catalog_tables ADD COLUMN IF NOT EXISTS data_category VARCHAR(50) DEFAULT 'statistics';
-- ALTER TABLE catalog_tables ADD COLUMN IF NOT EXISTS tags TEXT[];
-- ALTER TABLE catalog_tables ADD COLUMN IF NOT EXISTS column_descriptions JSONB;
-- ALTER TABLE catalog_tables ADD COLUMN IF NOT EXISTS sample_values JSONB;
-- ALTER TABLE catalog_tables ADD COLUMN IF NOT EXISTS numeric_ratio FLOAT;
-- ALTER TABLE catalog_tables ADD COLUMN IF NOT EXISTS avg_text_length FLOAT;
-- CREATE INDEX IF NOT EXISTS idx_catalog_tables_tags ON catalog_tables USING GIN (tags);

-- ============================================
-- 1a. 문서 청크 캐시 (원문 텍스트 청크를 PostgreSQL에 캐시)
-- ============================================
-- ChromaDB에는 요약만 임베딩하고, 원문 청크는 여기에 저장하여
-- 질의 시 파일 재파싱 없이 관련 청크를 즉시 로드합니다.
CREATE TABLE IF NOT EXISTS document_chunks (
    id              SERIAL PRIMARY KEY,
    document_id     INTEGER NOT NULL REFERENCES catalog_documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,           -- 문서 내 0-based 순서
    chunk_text      TEXT NOT NULL,              -- 원문 청크 텍스트
    char_offset     INTEGER DEFAULT 0,          -- 원본 텍스트 내 시작 위치
    char_length     INTEGER DEFAULT 0,          -- 청크 문자 수
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_doc_chunks_document_id ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_idx ON document_chunks(document_id, chunk_index);

-- 기존 배포 마이그레이션 (document_chunks):
-- CREATE TABLE IF NOT EXISTS document_chunks ( ... );  -- 위 DDL 참조
-- CREATE INDEX IF NOT EXISTS idx_doc_chunks_document_id ON document_chunks(document_id);
-- CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_idx ON document_chunks(document_id, chunk_index);

-- ============================================
-- 1b. 이미지 카탈로그 (EXIF + DINOv2 임베딩 메타데이터)
-- ============================================
CREATE TABLE IF NOT EXISTS catalog_images (
    id              SERIAL PRIMARY KEY,
    image_name      VARCHAR(500) NOT NULL,
    source_file     TEXT NOT NULL UNIQUE,
    file_type       VARCHAR(50),
    file_size_bytes BIGINT DEFAULT 0,
    -- 이미지 크기
    width           INTEGER,
    height          INTEGER,
    -- EXIF 구조화 데이터
    camera_make     VARCHAR(200),
    camera_model    VARCHAR(200),
    lens_info       VARCHAR(300),
    focal_length    FLOAT,
    aperture        FLOAT,                  -- f-number
    shutter_speed   VARCHAR(50),            -- 예: "1/250"
    iso             INTEGER,
    date_taken      TIMESTAMPTZ,            -- EXIF DateTimeOriginal
    gps_latitude    DOUBLE PRECISION,
    gps_longitude   DOUBLE PRECISION,
    gps_altitude    FLOAT,
    orientation     INTEGER,                -- EXIF orientation 태그
    -- DINOv2 임베딩 정보
    embedding_dim   INTEGER,                -- 384 (ViT-S/14) 등
    collection_name VARCHAR(255) DEFAULT 'images',
    -- 썸네일
    thumbnail_path  TEXT,
    -- 중복/그룹 정보
    duplicate_group_id  INTEGER,            -- NULL = 중복 그룹 없음
    is_duplicate    BOOLEAN DEFAULT FALSE,
    -- EXIF 원본 (JSON 백업)
    exif_json       JSONB,
    -- 타임스탬프
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_catalog_images_name ON catalog_images(image_name);
CREATE INDEX IF NOT EXISTS idx_catalog_images_date ON catalog_images(date_taken);
CREATE INDEX IF NOT EXISTS idx_catalog_images_dup_group ON catalog_images(duplicate_group_id);
CREATE INDEX IF NOT EXISTS idx_catalog_images_gps ON catalog_images(gps_latitude, gps_longitude)
    WHERE gps_latitude IS NOT NULL;

-- ============================================
-- 2. 감사 로그 (모든 질의/승인/실행 이력)
-- ============================================
CREATE TABLE IF NOT EXISTS audit_log (
    id              SERIAL PRIMARY KEY,
    action_type     VARCHAR(50) NOT NULL,
    -- query, approval_request, approval_granted, approval_denied, execution, error
    user_id         VARCHAR(100) DEFAULT 'system',
    query_text      TEXT,
    sql_generated   TEXT,
    result_summary  TEXT,
    status          VARCHAR(20) DEFAULT 'success',
    -- success, failed, pending, approved, denied
    metadata        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_user ON audit_log(user_id);

-- ============================================
-- 3. 배치 작업 관리
-- ============================================
CREATE TABLE IF NOT EXISTS batch_jobs (
    id              SERIAL PRIMARY KEY,
    job_name        VARCHAR(255) NOT NULL UNIQUE,
    description     TEXT,
    sql_text        TEXT NOT NULL,
    cron_expr       VARCHAR(100) NOT NULL,
    -- cron 표현식: "0 7 * * *" = 매일 07:00
    is_active       BOOLEAN DEFAULT TRUE,
    last_run_at     TIMESTAMPTZ,
    last_status     VARCHAR(20),
    -- success, failed, running
    created_by      VARCHAR(100) DEFAULT 'system',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS batch_job_history (
    id              SERIAL PRIMARY KEY,
    job_id          INTEGER NOT NULL REFERENCES batch_jobs(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          VARCHAR(20) NOT NULL,
    -- success, failed, running
    rows_affected   INTEGER DEFAULT 0,
    error_message   TEXT,
    execution_time  FLOAT
);

CREATE INDEX IF NOT EXISTS idx_job_history_job ON batch_job_history(job_id);
CREATE INDEX IF NOT EXISTS idx_job_history_started ON batch_job_history(started_at);

-- ============================================
-- 4. 승인 요청
-- ============================================
CREATE TABLE IF NOT EXISTS approval_requests (
    id              SERIAL PRIMARY KEY,
    request_type    VARCHAR(50) NOT NULL,
    -- sql_needs_approval, create_mart, create_batch, delete_batch
    title           VARCHAR(500),
    sql_text        TEXT NOT NULL,
    sql_category    VARCHAR(30),
    -- SAFE, AUTO_ALLOWED, NEEDS_APPROVAL, FORBIDDEN
    status          VARCHAR(20) DEFAULT 'pending',
    -- pending, approved, denied, executed
    requested_by    VARCHAR(100) DEFAULT 'system',
    reviewed_by     VARCHAR(100),
    reviewed_at     TIMESTAMPTZ,
    result_summary  TEXT,
    -- 실행 결과 요약 (execute 후 기록)
    metadata        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_requests(status);

-- ============================================
-- 5. 파일 처리 이력
-- ============================================
CREATE TABLE IF NOT EXISTS file_process_log (
    id              SERIAL PRIMARY KEY,
    file_path       TEXT NOT NULL,
    file_name       VARCHAR(500) NOT NULL,
    file_type       VARCHAR(50),
    file_size       BIGINT,
    action          VARCHAR(50) NOT NULL,
    -- load_to_db, register_for_search, delete, ignore, error
    target_table    VARCHAR(255),
    status          VARCHAR(20) DEFAULT 'success',
    -- success, failed, processing
    error_message   TEXT,
    processed_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_file_log_path ON file_process_log(file_path);
CREATE INDEX IF NOT EXISTS idx_file_log_processed ON file_process_log(processed_at);

-- ============================================
-- 6. LLM 설정 (런타임 모델 설정)
-- ============================================
-- 관리자가 UI에서 설정한 LLM 프로바이더/모델 정보를 저장합니다.
-- 환경 변수보다 우선 적용되어 재시작 없이 모델 변경이 가능합니다.
CREATE TABLE IF NOT EXISTS llm_settings (
    id              SERIAL PRIMARY KEY,
    setting_key     VARCHAR(100) NOT NULL UNIQUE,
    -- orchestrator_provider, orchestrator_model, orchestrator_api_key, orchestrator_base_url
    -- agent_provider, agent_model, agent_api_key, agent_base_url
    -- airgapped_mode
    setting_value   TEXT,
    is_encrypted    BOOLEAN DEFAULT FALSE,
    -- API 키 등 민감 정보는 암호화 표시
    description     VARCHAR(500),
    updated_by      VARCHAR(100) DEFAULT 'system',
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 초기 설정값 삽입 (ON CONFLICT로 중복 방지)
INSERT INTO llm_settings (setting_key, setting_value, description) VALUES
    ('orchestrator_provider', 'ollama', '오케스트레이터 LLM 프로바이더 (ollama/openai/anthropic)'),
    ('orchestrator_model', '', '오케스트레이터 LLM 모델명 (비어 있으면 앱 시작 시 env에서 자동 설정)'),
    ('orchestrator_api_key', '', '오케스트레이터 API 키 (상용 모델용)'),
    ('orchestrator_base_url', '', '오케스트레이터 API URL (비어 있으면 OLLAMA_HOST 환경변수 사용)'),
    ('agent_provider', 'ollama', '에이전트 LLM 프로바이더 (ollama/openai/anthropic)'),
    ('agent_model', '', '에이전트 LLM 모델명 (비어 있으면 앱 시작 시 env에서 자동 설정)'),
    ('agent_api_key', '', '에이전트 API 키 (상용 모델용)'),
    ('agent_base_url', '', '에이전트 API URL (비어 있으면 OLLAMA_HOST 환경변수 사용)'),
    ('airgapped_mode', 'false', '폐쇄망 모드 (true면 상용 API 비활성화)')
ON CONFLICT (setting_key) DO NOTHING;
