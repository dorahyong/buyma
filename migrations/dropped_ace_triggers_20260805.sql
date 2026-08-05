-- 2026-08-05 삭제한 ace_products 트리거 원본 (되돌릴 때 사용)

DELIMITER //
CREATE TRIGGER trg_protect_immutable_fields BEFORE UPDATE ON ace_products FOR EACH ROW
BEGIN
    -- 잠금 상태인 경우에만 체크
    IF OLD.is_buyma_locked = 1 THEN
        -- brand_id 변경 시도 시 원래 값 유지
        IF NEW.brand_id != OLD.brand_id THEN
            SET NEW.brand_id = OLD.brand_id;
        END IF;
        
        -- category_id 변경 시도 시 원래 값 유지
        IF NEW.category_id != OLD.category_id THEN
            SET NEW.category_id = OLD.category_id;
        END IF;
        
        -- reference_number 변경 시도 시 원래 값 유지
        IF NEW.reference_number != OLD.reference_number THEN
            SET NEW.reference_number = OLD.reference_number;
        END IF;
        
        -- name 변경 시도 시 원래 값 유지 (권장)
        IF NEW.name != OLD.name THEN
            SET NEW.name = OLD.name;
        END IF;
    END IF;
END//
DELIMITER ;

DELIMITER //
CREATE TRIGGER trg_ace_listing_days AFTER UPDATE ON ace_products FOR EACH ROW
BEGIN
    DECLARE v_bid INT;
    IF NEW.is_published <> OLD.is_published THEN
        IF OLD.is_published = 0 AND NEW.is_published = 1 THEN
            SET v_bid = NEW.buyma_product_id;
            IF v_bid IS NOT NULL THEN
                INSERT INTO buyma_listing_days
                    (buyma_product_id, listed_since, accumulated_seconds, first_listed_at, is_listed, last_event_at)
                VALUES (v_bid, NOW(), 0, NOW(), 1, NOW())
                ON DUPLICATE KEY UPDATE
                    listed_since    = IF(listed_since IS NULL, NOW(), listed_since),
                    is_listed       = 1,
                    first_listed_at = COALESCE(first_listed_at, NOW()),
                    last_event_at   = NOW();
                INSERT INTO buyma_listing_events
                    (buyma_product_id, event, event_at, reason, source_table, reference_number)
                VALUES (v_bid, 'up', NOW(), NEW.status, 'ace', NEW.reference_number);
            END IF;
        ELSEIF OLD.is_published = 1 AND NEW.is_published = 0 THEN
            SET v_bid = COALESCE(NEW.buyma_product_id, OLD.buyma_product_id);
            IF v_bid IS NOT NULL THEN
                UPDATE buyma_listing_days
                SET accumulated_seconds = accumulated_seconds + TIMESTAMPDIFF(SECOND, listed_since, NOW()),
                    listed_since        = NULL,
                    is_listed           = 0,
                    last_event_at       = NOW()
                WHERE buyma_product_id = v_bid AND listed_since IS NOT NULL;
                INSERT INTO buyma_listing_events
                    (buyma_product_id, event, event_at, reason, source_table, reference_number)
                VALUES (v_bid, 'down', NOW(), NEW.status, 'ace',
                        COALESCE(NEW.reference_number, OLD.reference_number));
            END IF;
        END IF;
    END IF;
END//
DELIMITER ;
