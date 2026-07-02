# -*- coding: utf-8 -*-
"""
썸네일 테이블 스키마 — 뱃지(送料・関税込 / 追跡付) 얹은 썸네일 산출물 관리.

설계 배경:
  - ace_product_images 는 "원본→R2 업로드본(cloudflare_image_url)"까지만 담당.
  - 그 위에 뱃지·그림자를 합성한 "썸네일"은 별도 산출물이므로 얇은 전용 테이블로 분리.
  - 재실행 안전(멱등): 이미 만든 이미지는 건너뛰고, source 가 바뀌면 다시 만들 수 있게
    source_cf_url 을 함께 저장해 둔다.

컬럼(운영에 필요한 최소):
  id                       썸네일 테이블 자체 PK
  image_id                 ace_product_images.id (어느 이미지로 만든 썸네일인지)
  ace_product_id           ace_products.id
  thumbnail_cloudflare_url 뱃지 얹은 썸네일의 R2 퍼블릭 URL (확보 결과물)
  source_cf_url            입력으로 쓴 원본 cloudflare_image_url (변경 감지용)
  is_generated             생성 완료 여부(0/1)
  generate_error           실패 시 오류 메시지
  created_at / updated_at

사용:
  python thumbnail_table.py            # 미리보기(적용 안 함)
  python thumbnail_table.py --execute  # 실제 적용
"""
import os, sys, io, argparse
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, '.env'), override=True)
DB = os.getenv('DB_NAME')
cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=DB, charset='utf8mb4',
           cursorclass=pymysql.cursors.DictCursor)

TABLE = 'ace_product_thumbnails'
DDL = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        id                       INT(11)    NOT NULL AUTO_INCREMENT,
        image_id                 INT(11)    NOT NULL              COMMENT 'ace_product_images.id',
        ace_product_id           INT(11)    NOT NULL              COMMENT 'ace_products.id',
        thumbnail_cloudflare_url TEXT       NULL DEFAULT NULL     COMMENT '뱃지 얹은 썸네일 R2 URL',
        source_cf_url            TEXT       NULL DEFAULT NULL      COMMENT '입력으로 쓴 cloudflare_image_url(변경 감지)',
        is_generated             TINYINT(1) NOT NULL DEFAULT 0    COMMENT '생성 완료 여부',
        generate_error           TEXT       NULL DEFAULT NULL     COMMENT '실패 오류 메시지',
        created_at               TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at               TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_image_id (image_id),
        INDEX idx_ace_product_id (ace_product_id),
        INDEX idx_is_generated (is_generated)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
      COMMENT='뱃지 얹은 BUYMA 썸네일 산출물'
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true')
    args = ap.parse_args()

    print("=== 적용 계획 (신규 테이블만 — 기존 테이블 락 무관) ===")
    print(f"  + CREATE TABLE IF NOT EXISTS {TABLE}")
    if not args.execute:
        print("\n(미리보기 — 실제 적용은 --execute)")
        return

    conn = pymysql.connect(**cfg)
    cur = conn.cursor()
    cur.execute(DDL)
    conn.commit()
    print(f"  ✅ {TABLE} 생성/확인 완료")
    cur.execute(f"SHOW COLUMNS FROM {TABLE}")
    print("\n=== 컬럼 ===")
    for r in cur.fetchall():
        print(f"  {r['Field']:<26} {r['Type']}")
    conn.close()
    print("\n[스키마 적용 완료]")


if __name__ == '__main__':
    main()
