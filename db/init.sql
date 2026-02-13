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
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS catalog_documents (
    id              SERIAL PRIMARY KEY,
    doc_name        VARCHAR(500) NOT NULL,
    source_file     TEXT NOT NULL UNIQUE,
    file_type       VARCHAR(50),
    chunk_count     INTEGER DEFAULT 0,
    collection_name VARCHAR(255),
    summary_text    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 기존 배포 마이그레이션:
-- ALTER TABLE catalog_documents ADD COLUMN IF NOT EXISTS summary_text TEXT;
-- ALTER TABLE catalog_documents ADD CONSTRAINT uq_catalog_documents_source_file UNIQUE (source_file);

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
