-- register/stock 그룹 선정 쿼리 가속용 인덱스.
--
-- 문제: reconcile_runner.select_groups_to_process 는 ace_products 를 source_site 로 거르는데
--   해당 인덱스가 없어 40만행 풀스캔 + 이미지 189만행 스캔이 발생, kasina 등 큰 몰에서 ~43초.
--   원격(MariaDB) 이 net_read_timeout(30s) 부근에서 연결을 끊어(WinError 10054) REGISTER 가
--   몰 전체 실패("Lost connection")하던 근본 원인.
-- 조치: (source_site, is_active) 복합 인덱스 → 구동 스캔 40.8만→2.8만행, 실측 43초 → ~1초.
--
-- 적용 이력: 2026-07-15 운영 DB 에 온라인(ALGORITHM=INPLACE, 무중단) 생성 완료.
--   이 파일은 재현/기록용(코드가 아닌 DB 변경이라 별도 기록).
--   ★ 이미 적용돼 있으므로(수동 생성) IF NOT EXISTS 로 멱등화 — 재실행해도 중복오류 없음.

CREATE INDEX IF NOT EXISTS idx_source_site_active ON ace_products (source_site, is_active);
