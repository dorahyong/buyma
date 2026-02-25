-- =====================================================
-- 재고 동기화를 위한 테이블 변경
-- 실행일: 2026-01-29
-- =====================================================

-- 1. ace_product_variants 컬럼 추가 (필수)
ALTER TABLE ace_product_variants
ADD COLUMN IF NOT EXISTS source_stock_status VARCHAR(20) DEFAULT 'in_stock' COMMENT '쇼핑몰 원본 재고 상태',
ADD COLUMN IF NOT EXISTS last_stock_checked_at DATETIME COMMENT '마지막 재고 체크 시간';

-- 2. ace_products 컬럼 추가 (권장)
ALTER TABLE ace_products
ADD COLUMN IF NOT EXISTS last_stock_synced_at DATETIME COMMENT '마지막 재고 동기화 시간';

-- 3. 재고 변경 이력 테이블 (선택)
CREATE TABLE IF NOT EXISTS stock_change_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    ace_product_id INT NOT NULL,
    variant_id INT,
    color_value VARCHAR(100),
    size_value VARCHAR(100),
    old_status VARCHAR(20),
    new_status VARCHAR(20),
    synced_to_buyma TINYINT DEFAULT 0,
    created_at DATETIME DEFAULT NOW(),
    INDEX idx_product (ace_product_id),
    INDEX idx_synced (synced_to_buyma),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='재고 변경 이력';

-- 4. 인덱스 추가 (성능 최적화)
-- ace_products에 last_stock_synced_at 인덱스
CREATE INDEX IF NOT EXISTS idx_last_stock_synced ON ace_products(last_stock_synced_at);

-- ace_product_variants에 last_stock_checked_at 인덱스
CREATE INDEX IF NOT EXISTS idx_last_stock_checked ON ace_product_variants(last_stock_checked_at);
