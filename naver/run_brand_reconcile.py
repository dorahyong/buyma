# -*- coding: utf-8 -*-
"""
네이버 '순회형' 8몰의 브랜드 카탈로그를 사이트 기준으로 자동 동기화.

왜 순회형만? 순회형(브랜드 URL을 하나씩 도는 방식)은 mall_brands에 저장된 브랜드만 수집하므로
  - 사이트에 새 브랜드가 생겨도 목록에 없으면 영영 안 긁히고,
  - 사이트에서 사라진 브랜드는 죽은 URL로 계속 들어감.
→ 그래서 '브랜드 목록'을 주기적으로 사이트와 맞춰야(reconcile) 한다.
스윕형 13몰(dmont 등)은 전체상품을 훑으며 상세 제조사로 브랜드를 자동 등록하므로 불필요.

동작: 몰별로 scan_store_brands.py --reconcile --brands-only 를 호출.
  - **기본은 '추가 전용'**: 새 브랜드 추가 + URL 갱신만. 사라진 브랜드 끄기는 안 함.
    (끄기는 스캔 불완전·옛 잔재로 오판 잦고, collector 수정 후엔 죽은 브랜드가 무해해서 불필요)
  - 스캔이 가끔 빈손(0개, 메뉴 호버 타이밍 플레이크) → 자동 재시도(--retries).
  - 수집이 쓰는 naver_cookies.json 재사용 → 별도 로그인 불필요(쿠키 만료 시 갱신은 수집과 동일).

권장 주기: 주 1회 (브랜드는 상품처럼 매일 바뀌지 않음).

사용법:
    python run_brand_reconcile.py --dry-run        # 8몰 미리보기(DB 안 건드림)
    python run_brand_reconcile.py                  # 8몰 신규 추가(+URL갱신)
    python run_brand_reconcile.py --source carpi    # 한 몰만
    python run_brand_reconcile.py --deactivate      # [opt-in] 사라진 브랜드 끄기까지(완전 스캔 때만)
"""
import os, sys, re, subprocess, argparse

# 순회형 8몰 (run_daily_unified NAVER_COLLECTOR의 _NV_BRAND 6 + _NV_BRANDSTORE 2)
LOOP_STORES = ['premiumsneakers', 'fabstyle', 'loutique', 't1global', 'vvano', 'veroshopmall',
               'carpi', 'joharistore']

HERE = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(HERE, 'scan_store_brands.py')


def run_one(store, dry, deactivate, retries, passes):
    cmd = [sys.executable, SCAN, '--store', store, '--reconcile', '--brands-only', '--passes', str(passes)]
    if dry:
        cmd.append('--dry-run')
    if deactivate:
        cmd.append('--deactivate')
    for attempt in range(1, retries + 1):
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
        out = (p.stdout or '') + (p.stderr or '')
        m = re.search(r'동기화: .*', out)
        if m:
            return True, m.group(0)
        if '추출된 데이터 없음' in out or '브랜드: 0개' in out:
            print(f"  [{store}] 스캔 빈손 — 재시도 {attempt}/{retries}")
            continue
        last = out.strip().splitlines()[-1] if out.strip() else '출력 없음'
        return False, f"실패: {last}"
    return False, f"{retries}회 모두 빈손(쿠키 만료/캡챠 의심)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', help='한 몰만 (기본: 순회형 8몰 전체)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--deactivate', action='store_true',
                    help='[opt-in] 사라진 브랜드 끄기까지(기본: 추가/URL갱신 + 삭제후보 로그)')
    ap.add_argument('--retries', type=int, default=3)
    ap.add_argument('--passes', type=int, default=1, help='스캔 union 횟수. 트리추출이라 1번이면 완전(호버 폴백 시만 늘림)')
    args = ap.parse_args()

    if args.source and args.source not in LOOP_STORES:
        print(f"'{args.source}'는 순회형 8몰이 아님: {', '.join(LOOP_STORES)}")
        sys.exit(1)
    stores = [args.source] if args.source else LOOP_STORES

    print(f"=== 브랜드 reconcile: {len(stores)}몰 (Mode: {'DRY-RUN' if args.dry_run else 'APPLY'}) ===")
    results = []
    for s in stores:
        print(f"\n>>> {s}")
        ok, msg = run_one(s, args.dry_run, args.deactivate, args.retries, args.passes)
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        results.append((s, ok, msg))

    print("\n=== 요약 ===")
    for s, ok, msg in results:
        print(f"  [{'OK  ' if ok else 'FAIL'}] {s}: {msg}")
    fails = [s for s, ok, _ in results if not ok]
    if fails:
        print(f"\n⚠️ 실패 {len(fails)}몰: {', '.join(fails)} — 쿠키 갱신 후 재실행 권장.")
        sys.exit(1)
    print("\n✅ 전 몰 동기화 완료")


if __name__ == '__main__':
    main()