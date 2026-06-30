# -*- coding: utf-8 -*-
"""버킷② — 미연결 라이브 ace 들에 ensure_group 으로 buyma_listings 행 생성(=reconcile이 평소 하던 그룹 빌드).
워커별 독립 커넥션 병렬(그룹은 model_no 단위 분배 → 충돌 없음). ensure_group 로직 그대로, 속도만 N배.
이후 fill_listing_identity.py 재실행하면 정체성 복사됨. ensure_group은 listing을 draft(is_published=0)로 만들어 트리거 영향 없음.
사용: python bucket2_create_listings.py                       # 건수만
      python bucket2_create_listings.py --execute --workers 6 # 실제 생성(병렬)
"""
import os, sys, argparse, threading
from concurrent.futures import ThreadPoolExecutor
import pymysql
from dotenv import load_dotenv
BASE=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE,'okmall'))
load_dotenv(os.path.join(BASE,'.env'),override=True)
import reconcile_ensure_group as eg   # stdout wrap 은 이 import 가 처리

cfg=dict(host=os.getenv('DB_HOST'),port=int(os.getenv('DB_PORT',3306)),user=os.getenv('DB_USER'),password=os.getenv('DB_PASSWORD'),database=os.getenv('DB_NAME'),charset='utf8mb4',cursorclass=pymysql.cursors.DictCursor)

_prog={'done':0,'ok':0,'err':0,'lock':threading.Lock(),'total':0}

def worker(chunk):
    wc=pymysql.connect(**cfg); wc.autocommit(False)
    lok=lerr=0
    for j,g in enumerate(chunk,1):
        try:
            eg.ensure_group(wc, g['model_no'], g['brand_id'], dry_run=False); lok+=1
        except Exception:
            wc.rollback(); lerr+=1
        if j%200==0: wc.commit()
    wc.commit(); wc.close()
    with _prog['lock']:
        _prog['ok']+=lok; _prog['err']+=lerr; _prog['done']+=len(chunk)
        print(f"   워커 청크 완료 (+{lok} 생성/+{lerr} 실패) | 누적 {_prog['done']}/{_prog['total']}")
    return lok,lerr

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--execute',action='store_true'); ap.add_argument('--limit',type=int,default=None)
    ap.add_argument('--workers',type=int,default=6)
    args=ap.parse_args()
    conn=pymysql.connect(**cfg)
    with conn.cursor() as c:
        c.execute(f"""SELECT DISTINCT a.model_no, a.brand_id
                      FROM ace_products a
                      WHERE a.is_published=1 AND a.buyma_product_id IS NOT NULL
                        AND a.model_no IS NOT NULL AND a.model_no<>'' AND a.model_no NOT REGEXP '[가-힣]'
                        AND a.category_id IS NOT NULL AND a.category_id>0
                        AND NOT EXISTS (SELECT 1 FROM source_offerings so WHERE so.ace_product_id=a.id)
                      {'LIMIT '+str(int(args.limit)) if args.limit else ''}""")
        groups=c.fetchall()
    conn.close()
    print(f"[미연결 그룹] {len(groups)}개 (model_no 기준)")
    if not args.execute:
        print("(건수만 — 실제 생성은 --execute)"); return
    _prog['total']=len(groups)
    W=max(1,args.workers)
    chunks=[groups[i::W] for i in range(W)]   # 라운드로빈 분배(각 워커 서로 다른 model_no)
    print(f"[병렬 생성] 워커 {W}개")
    with ThreadPoolExecutor(max_workers=W) as ex:
        list(ex.map(worker, chunks))
    print(f"[완료] ensure_group 성공 {_prog['ok']} / 실패 {_prog['err']}")

if __name__=='__main__':
    main()
