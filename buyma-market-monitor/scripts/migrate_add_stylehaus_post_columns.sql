-- STYLE HAUS 関連記事(포스트) 유무·개수 컬럼 추가.
-- 기존 has_style_haus / stylehaus_video_count 는 동영상 전용으로 유지.
-- 적용: python scripts/_run_sql_file.py scripts/migrate_add_stylehaus_post_columns.sql

ALTER TABLE market_stylehaus_history
  ADD COLUMN has_style_haus_post TINYINT NOT NULL DEFAULT 0
    COMMENT '関連記事(포스트) 유무' AFTER stylehaus_video_count,
  ADD COLUMN stylehaus_post_count INT NULL
    COMMENT '関連記事 개수' AFTER has_style_haus_post;
