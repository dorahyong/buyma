# -*- coding: utf-8 -*-
"""[임시] 롯데 raw → ace 변환을 '브랜드별'로 순차 실행 + 브랜드 안에서 N개 단위 진행표시.
   이유: 롯데 37만 통째 fetch가 무거워 시작이 느림 → 브랜드로 쪼개면 fetch가 작아 바로 변환 시작.
   --brand 는 이미 변환된 건 자동 skip(중복 안 생김) → 중간에 죽어도 다시 돌리면 이어감.
   사용:
        python lotte_convert_loop.py                # 전체(진행 100개 단위)
        python lotte_convert_loop.py --every 50     # 진행 50개 단위
        python lotte_convert_loop.py --start 300    # 300번째 브랜드부터 재개
"""
import os, sys, subprocess, argparse, re
sys.stdout.reconfigure(encoding='utf-8')
import pymysql
from dotenv import load_dotenv

BASE = r"C:\Users\hyong\OneDrive\원블록스\buyma"
load_dotenv(os.path.join(BASE, '.env'))
CONV = os.path.join(BASE, 'kasina', 'raw_to_converter_kasina.py')

ap = argparse.ArgumentParser()
ap.add_argument('--start', type=int, default=0, help='이 번째 브랜드부터 재개(0-based)')
ap.add_argument('--every', type=int, default=100, help='브랜드 안 진행표시 단위(기본 100)')
args = ap.parse_args()
EVERY = max(1, args.every)

cfg = dict(host=os.getenv('DB_HOST'), port=int(os.getenv('DB_PORT', 3306)), user=os.getenv('DB_USER'),
           password=os.getenv('DB_PASSWORD'), database=os.getenv('DB_NAME'), charset='utf8mb4', read_timeout=30)
conn = pymysql.connect(**cfg); c = conn.cursor()
c.execute("""SELECT DISTINCT raw_brand_name FROM mall_brands
             WHERE mall_name='lotte' AND is_active=1 AND buyma_brand_id IS NOT NULL
               AND raw_brand_name IS NOT NULL AND raw_brand_name<>''""")
brands = sorted(set((r[0] or '').strip().upper() for r in c.fetchall() if (r[0] or '').strip()))
conn.close()

total = len(brands)
print(f"롯데 매핑 브랜드 {total}개 순차 변환 (start={args.start}, 진행표시 {EVERY}개 단위)", flush=True)
env = dict(os.environ, PYTHONIOENCODING='utf-8')
sum_ins = sum_skip = sum_fail = 0

RE_PROG = re.compile(r'\[(\d+)/(\d+)\]\s*변환 중')
RE_INS  = re.compile(r'신규 INSERT:\s*(\d+)')
RE_SKIP = re.compile(r'스킵:\s*(\d+)')
RE_FAIL = re.compile(r'실패:\s*(\d+)')

for i, b in enumerate(brands):
    if i < args.start:
        continue
    print(f"\n===== [{i+1}/{total}] brand={b} =====", flush=True)
    ins = skip = fail = 0
    last = 0
    try:
        proc = subprocess.Popen([sys.executable, CONV, '--source-site', 'lotte', '--brand', b, '--skip-translation'],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=BASE,
                                text=True, encoding='utf-8', errors='replace', env=env, bufsize=1)
    except Exception as e:
        print(f"  ⚠️ 실행오류: {e}", flush=True); continue
    for line in proc.stdout:
        m = RE_PROG.search(line)
        if m:
            n, tot = int(m.group(1)), int(m.group(2))
            if n - last >= EVERY or n == tot:
                print(f"     ...{n}/{tot}", flush=True); last = n
            continue
        m = RE_INS.search(line);  ins  = int(m.group(1)) if m else ins
        m = RE_SKIP.search(line); skip = int(m.group(1)) if m else skip
        m = RE_FAIL.search(line); fail = int(m.group(1)) if m else fail
    proc.wait()
    sum_ins += ins; sum_skip += skip; sum_fail += fail
    flag = f" ⚠️rc={proc.returncode}" if proc.returncode != 0 else ""
    print(f"  → INSERT {ins} / skip {skip} / fail {fail}{flag}   [누적 INSERT {sum_ins:,}]", flush=True)

print(f"\n===== 전체 완료: 누적 INSERT {sum_ins:,} / skip {sum_skip:,} / fail {sum_fail:,} =====", flush=True)
