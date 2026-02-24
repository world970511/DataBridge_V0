-- ==========================================================
-- DataBridge 외부 DB 테스트 시드 데이터
-- company_erp 데이터베이스: 거래처/상품/거래내역 (100,000건)
-- ==========================================================

-- 거래처 마스터 (100개)
CREATE TABLE partners (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    business_type VARCHAR(50),
    region VARCHAR(50),
    contact_email VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO partners (name, business_type, region, contact_email)
SELECT
    '거래처_' || i,
    (ARRAY['제조','유통','서비스','IT','건설'])[1 + (i % 5)],
    (ARRAY['서울','경기','부산','대구','인천','광주','대전','울산'])[1 + (i % 8)],
    'partner' || i || '@example.com'
FROM generate_series(1, 100) AS i;

-- 상품 마스터 (500개)
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    product_code VARCHAR(50) NOT NULL,
    product_name VARCHAR(200) NOT NULL,
    category VARCHAR(100),
    unit_price NUMERIC(12,2)
);

INSERT INTO products (product_code, product_name, category, unit_price)
SELECT
    'PRD-' || LPAD(i::TEXT, 4, '0'),
    (ARRAY['부품','소재','반제품','완제품','소모품'])[1 + (i % 5)] || '_' || i,
    (ARRAY['전자','기계','화학','식품','섬유'])[1 + (i % 5)],
    ROUND((RANDOM() * 500000 + 1000)::NUMERIC, 2)
FROM generate_series(1, 500) AS i;

-- 거래 내역 (100,000건 — 최근 2년)
CREATE TABLE transactions (
    id SERIAL PRIMARY KEY,
    transaction_date DATE NOT NULL,
    partner_id INTEGER REFERENCES partners(id),
    product_id INTEGER REFERENCES products(id),
    transaction_type VARCHAR(20) NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price NUMERIC(12,2) NOT NULL,
    total_amount NUMERIC(15,2) NOT NULL,
    payment_status VARCHAR(20) DEFAULT 'completed',
    notes TEXT
);

INSERT INTO transactions (
    transaction_date, partner_id, product_id,
    transaction_type, quantity, unit_price, total_amount,
    payment_status, notes
)
SELECT
    CURRENT_DATE - (RANDOM() * 730)::INTEGER,
    1 + (RANDOM() * 99)::INTEGER,
    1 + (RANDOM() * 499)::INTEGER,
    (ARRAY['sale','purchase'])[1 + (i % 2)],
    1 + (RANDOM() * 100)::INTEGER,
    ROUND((RANDOM() * 500000 + 1000)::NUMERIC, 2),
    0,
    (ARRAY['completed','pending','cancelled'])[1 + (RANDOM() * 2)::INTEGER],
    CASE WHEN RANDOM() > 0.7 THEN '비고_' || i ELSE NULL END
FROM generate_series(1, 100000) AS i;

-- total_amount = quantity * unit_price
UPDATE transactions SET total_amount = quantity * unit_price;

-- 인덱스
CREATE INDEX idx_tx_date ON transactions(transaction_date);
CREATE INDEX idx_tx_partner ON transactions(partner_id);
CREATE INDEX idx_tx_product ON transactions(product_id);
CREATE INDEX idx_tx_type ON transactions(transaction_type);
CREATE INDEX idx_tx_status ON transactions(payment_status);

-- 월별 거래 요약 뷰
CREATE VIEW monthly_summary AS
SELECT
    DATE_TRUNC('month', transaction_date)::DATE AS month,
    transaction_type,
    COUNT(*) AS tx_count,
    SUM(total_amount) AS total_amount,
    ROUND(AVG(total_amount), 2) AS avg_amount
FROM transactions
WHERE payment_status = 'completed'
GROUP BY 1, 2;
