-- market_orders: 크롤러 핫패스용 복합 인덱스.
--   1) (item_id, sale_date)     — 재방문마다 count_item_sales_since (판매 블렌드)
--   2) (seller_id, sale_date)   — watermark 경계 reconcile / 셀러·날짜 조회
--   3) (sale_date, seller_id)   — recompute_seller_values 의 최근주문 GROUP BY
-- 기존 단일 인덱스(seller / sale_date / item)는 다른 조회와 호환을 위해 유지.

ALTER TABLE market_orders
  ADD KEY idx_market_orders_item_date   (item_id, sale_date),
  ADD KEY idx_market_orders_seller_date (seller_id, sale_date),
  ADD KEY idx_market_orders_date_seller (sale_date, seller_id);
