-- =====================================================
-- 실제 게시일수 트래킹 — 트리거 (2단계)
--
-- 작성일: 2026-06-29
-- 전제: 1단계 스키마(2026_listing_days_tracking.sql) 적용 완료
-- 원리: is_published 0↔1 전이를 잡아 buyma_product_id 기준 카운터/이력 갱신.
--       writer-무관(어느 스크립트가 바꾸든 자동 포착) → 트리거 방식.
--
-- 대상 2개:
--   - ace_products  : 옛 직접등록분(라이브 54,961)의 전이
--   - buyma_listings: 신규 병합분(라이브 2,717)+앞으로의 전이
--   ref 가 한쪽에만 있어 같은 상품은 한쪽 트리거만 발화 → 중복 없음.
--
-- 발화 조건은 반드시 OLD.is_published <> NEW.is_published 로 한정
--   (stock/price 의 대량 UPDATE 는 is_published 불변이라 맨 위 IF 에서 즉시 통과).
--
-- ※ 실행 전 사용자 확인. 트리거는 데이터가 아니라 재적용 안전(DROP IF EXISTS).
-- ※ 트리거 설치 직후 3단계 백필을 돌려 기존 게시중 상품을 시드할 것.
-- =====================================================

USE buyma;

-- ---------- ace_products 트리거 ----------
DROP TRIGGER IF EXISTS `trg_ace_listing_days`;

CREATE TRIGGER `trg_ace_listing_days`
AFTER UPDATE ON `ace_products`
FOR EACH ROW
BEGIN
    DECLARE v_bid INT;
    IF NEW.is_published <> OLD.is_published THEN
        -- 올라감 (0 → 1)
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
        -- 내려감 (1 → 0)
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
END;

-- ---------- buyma_listings 트리거 ----------
DROP TRIGGER IF EXISTS `trg_listings_listing_days`;

CREATE TRIGGER `trg_listings_listing_days`
AFTER UPDATE ON `buyma_listings`
FOR EACH ROW
BEGIN
    DECLARE v_bid INT;
    IF NEW.is_published <> OLD.is_published THEN
        -- 올라감 (0 → 1)
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
                VALUES (v_bid, 'up', NOW(), NEW.status, 'listings', NEW.reference_number);
            END IF;
        -- 내려감 (1 → 0)
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
                VALUES (v_bid, 'down', NOW(), NEW.status, 'listings',
                        COALESCE(NEW.reference_number, OLD.reference_number));
            END IF;
        END IF;
    END IF;
END;
