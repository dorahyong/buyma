# -*- coding: utf-8 -*-
"""
통합 일일 자동화 — 33몰 (okmall 1 + 멀티소스 10: kasina·nextzennpack·labellusso·9tems·
brickmansion·loromoda·milaneez·maisonparco·musinsa·laprima + naver 22). naver 만 캡챠로 사이트접속 직렬.

pipeline_engine.PipelineEngine 위에 "무엇을 어떻게 돌릴지"만 주입하는 설정층.
  - 비즈니스 로직 0 — 마진·가격·중복판정·필드매핑은 기존 완성 스크립트를 그대로 호출.
  - 몰 단위 유닛(브랜드 단위 아님): "어느 몰이 어디까지 됐나"를 mall 단위로 추적(요구 #2).
  - NEW(collector~register) 와 STOCK(재고/최저가) 를 동시 트랙으로(요구 #4).
  - 유닛 파이프라인 병렬 + DONE 스킵 resume(요구 #2·#3).

★ 사이트 동시접속 잠금: 9몰은 각자 독립 사이트라 불필요(전부 병렬).
   캡챠 때문에 직렬이 필요한 건 naver 뿐(브라우저 2개↑ 뜨면 캡챠) → naver 를 이 통합에
   추가할 때만 site_resource='naver' + SITE_ACCESS_STAGES={'COLLECT','STOCK_REFRESH'} 로 켠다.

각 단계 명령은 기존 run_daily.py(okmall orchestrator)·run_daily_multisource_merge.py 에서
실제로 쓰는 명령을 그대로 복제(2026-06-23 재검증).

사용법:
    python run_daily_unified.py --plan                # 실행 명령만 출력(DB·실행 없음)
    python run_daily_unified.py --dry-run             # 엔진 가동(상태기록), 명령은 no-op 로그
    python run_daily_unified.py                       # 실제 실행
    python run_daily_unified.py --only kasina         # 특정 몰만
    python run_daily_unified.py --track new           # NEW 트랙만 (new|stock|all, 기본 all)
"""

import os
import sys
import argparse

import pipeline_engine as pe

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def _p(*parts):
    return os.path.join(BASE, *parts)


# =====================================================
# 몰 목록
# =====================================================
OKMALL = 'okmall'
MULTISOURCE = ['kasina', 'nextzennpack', 'labellusso', '9tems',
               'brickmansion', 'loromoda', 'milaneez', 'maisonparco', 'musinsa', 'laprima']
# naver 21몰 (run_daily_naver.py SOURCES 와 동일). 캡챠로 사이트접속 직렬 → site_resource='naver'.
NAVER = ['premiumsneakers', 'fabstyle', 'loutique', 't1global', 'vvano', 'veroshopmall',
         'dmont', 'tuttobene', 'thefactor2',
         'carpi', 'joharistore',
         'maniaon', 'bblue', 'euroline', 'unico', 'kometa',
         'larlashoes', 'thegrande', 'upset', 'luxlimit', 'pano', 'trendmecca']
MALLS = [OKMALL] + MULTISOURCE + NAVER

# only-naver 3분할 (PC별, IP 분산 목적). 큰 몰(bblue·unico·larlashoes·upset·carpi)을
#   서로 다른 PC로 흩어 대략 균형. 각 PC는 자기 몫만(disjoint) → 같은 배치 공유해도 안 부딪힘.
#   naver 직렬잠금(Semaphore)은 프로세스별이라 3 PC = 3 IP 병렬.
NAVER_SPLIT = {
    1: ['bblue', 'premiumsneakers', 'fabstyle', 'loutique', 't1global', 'vvano', 'veroshopmall'],
    2: ['unico', 'upset', 'dmont', 'tuttobene', 'thefactor2', 'joharistore', 'maniaon'],
    3: ['larlashoes', 'carpi', 'luxlimit', 'thegrande', 'euroline', 'kometa', 'pano', 'trendmecca'],
}

# naver collector 3종 (run_daily_naver.py COLLECTOR_MAP 와 동일). 모두 공용 → --source 필요.
_NV_BRAND = _p('naver', 'premiumsneakers', 'premiumsneakers_collector.py')
_NV_CATEGORY = _p('naver', 'premiumsneakers', 'premiumsneakers_category_collector.py')
_NV_BRANDSTORE = _p('naver', 'premiumsneakers', 'brand_store_collector.py')
NAVER_COLLECTOR = {
    'premiumsneakers': _NV_BRAND, 'fabstyle': _NV_BRAND, 'loutique': _NV_BRAND,
    't1global': _NV_BRAND, 'vvano': _NV_BRAND, 'veroshopmall': _NV_BRAND,
    'carpi': _NV_BRANDSTORE, 'joharistore': _NV_BRANDSTORE,
    'dmont': _NV_CATEGORY, 'tuttobene': _NV_CATEGORY, 'thefactor2': _NV_CATEGORY,
    'maniaon': _NV_CATEGORY, 'bblue': _NV_CATEGORY, 'euroline': _NV_CATEGORY,
    'unico': _NV_CATEGORY, 'kometa': _NV_CATEGORY, 'larlashoes': _NV_CATEGORY,
    'thegrande': _NV_CATEGORY, 'upset': _NV_CATEGORY, 'luxlimit': _NV_CATEGORY, 'pano': _NV_CATEGORY,
    'trendmecca': _NV_CATEGORY,
}
# naver stock = 21몰 공용 1개 스크립트(_merge) → --source 로 1몰씩.
NAVER_STOCK_MERGE = _p('naver', 'stock_price_synchronizer_naver_merge.py')

# 멀티소스 몰별 collector 경로 (run_daily_multisource_merge.py 와 동일)
COLLECTOR = {
    'kasina': _p('kasina', 'kasina_collector.py'),
    'nextzennpack': _p('nextzennpack', 'nextzennpack_collector.py'),
    'labellusso': _p('labellusso', 'labellusso_collector.py'),
    '9tems': _p('9tems', '9tems_collector.py'),
    'brickmansion': _p('brickmansion', 'brickmansion_collector.py'),
    'loromoda': _p('loromoda', 'loromoda_collector.py'),
    'milaneez': _p('milaneez', 'milaneez_collector.py'),
    'maisonparco': _p('maisonparco', 'maisonparco_collector.py'),
    'musinsa': _p('musinsa_boutique', 'musinsa_collector.py'),
    'laprima': _p('laprima', 'laprima_collector.py'),
}
# 수집 직후 카테고리 채우기가 필요한 몰만(같은 collector를 플래그로 1회 더). 멀티소스와 동일.
CATEGORY_FILL = {
    'brickmansion': ['--categories'],
    'loromoda': ['--categories'],
    'maisonparco': ['--categories'],
    'milaneez': ['--map-categories'],
}
# STOCK(2차 merge) 스크립트 — 몰별
STOCK_MERGE = {
    'okmall': _p('okmall', 'stock_price_synchronizer_merge.py'),
    'kasina': _p('kasina', 'stock_price_synchronizer_kasina_merge.py'),
    'nextzennpack': _p('nextzennpack', 'stock_price_synchronizer_nextzennpack_merge.py'),
    'labellusso': _p('labellusso', 'stock_price_synchronizer_labellusso_merge.py'),
    '9tems': _p('9tems', 'stock_price_synchronizer_9tems_merge.py'),
    'brickmansion': _p('brickmansion', 'stock_price_synchronizer_brickmansion_merge.py'),
    'loromoda': _p('loromoda', 'stock_price_synchronizer_loromoda_merge.py'),
    'milaneez': _p('milaneez', 'stock_price_synchronizer_milaneez_merge.py'),
    'maisonparco': _p('maisonparco', 'stock_price_synchronizer_maisonparco_merge.py'),
    'musinsa': _p('musinsa_boutique', 'stock_price_synchronizer_musinsa_merge.py'),
    'laprima': _p('laprima', 'stock_price_synchronizer_laprima_merge.py'),
}

# 공용 스크립트
CONV_OKMALL = _p('okmall', 'raw_to_ace_converter.py')        # okmall 용 (--source)
CONV_MULTI = _p('kasina', 'raw_to_converter_kasina.py')      # 멀티소스 용 (--source-site)
PRICE = _p('okmall', 'buyma_lowest_price_collector.py')
TRANSLATE = _p('okmall', 'convert_to_japanese_gemini.py')
IMG_COLLECT = _p('okmall', 'image_collector_parallel.py')
IMG_UPLOAD = _p('okmall', 'r2_image_uploader.py')
THUMBNAIL = _p('thumbnail', 'thumbnail_generator.py')        # 대표이미지에 뱃지 합성
RECONCILE = _p('okmall', 'reconcile_runner.py')

# =====================================================
# 트랙 / 단계 구성
# =====================================================
# THUMBNAIL 은 IMAGE(=R2 업로드) 뒤, REGISTER 앞이어야 한다:
#   업로드된 cloudflare_image_url 이 있어야 합성 가능하고, register/reconcile 의
#   get_product_images·_images 가 ace_product_thumbnails 를 읽어 대표이미지를 뱃지본으로 보낸다.
#   → 신규 상품이 '첫 등록부터' 뱃지를 달고 올라간다.
NEW_STAGES = ['COLLECT', 'CONVERT', 'PRICE', 'TRANSLATE', 'IMAGE', 'THUMBNAIL', 'REGISTER']
STOCK_STAGES = ['STOCK_REFRESH']     # _merge 스크립트가 내부에서 refresh + reconcile(scope published) 수행
STAGE_PLAN = {'NEW': NEW_STAGES, 'STOCK': STOCK_STAGES}

# 단계별 동시실행 상한(세마포어). 썸네일은 이미지 합성(CPU)+R2 업로드라 31몰이 한꺼번에
#   붙으면 CPU·업로드가 몰린다 → 제한. 나머지 단계는 무제한(기존 동작 그대로).
STAGE_CONCURRENCY = {'THUMBNAIL': 4}

# 사이트 동시접속 잠금 대상 단계. naver(캡챠)만 collector·stock 1개씩 → site_resource='naver' 인 유닛만 잠김.
#   9몰은 site_resource=None 이라 이 집합을 켜도 안 잠김(전부 병렬). naver 추가됐으니 ON.
SITE_ACCESS_STAGES = {'COLLECT', 'STOCK_REFRESH'}

# 캡챠로 사이트 접속을 직렬화해야 하는 몰(=공유 site_resource 'naver'). 그 외는 None(잠금 없음).
NAVER_MALLS = set(NAVER)


# =====================================================
# 워커 명령 배선 (몰 × 단계 → subprocess 명령들). 기존 스크립트 그대로.
# =====================================================
def worker_resolver(unit, stage):
    mall = unit['mall']

    # ---- STOCK 트랙 ----
    if stage == 'STOCK_REFRESH':
        if mall in NAVER_MALLS:
            # naver stock 은 21몰 공용 1개 스크립트 → --source 로 1몰
            return [[PY, NAVER_STOCK_MERGE, '--source', mall]]
        return [[PY, STOCK_MERGE[mall]]]

    # ---- NEW 트랙 ----
    if stage == 'COLLECT':
        if mall == OKMALL:
            # --brand 없으면 okmall 전체 활성 브랜드 수집
            return [[PY, _p('okmall', 'okmall_all_brands_collector.py'), '--skip-existing']]
        if mall in NAVER_MALLS:
            # naver collector 3종 공용 → --source 필요 (카테고리 채우기 단계 없음)
            return [[PY, NAVER_COLLECTOR[mall], '--source', mall, '--skip-existing']]
        cmds = [[PY, COLLECTOR[mall], '--skip-existing']]
        if mall in CATEGORY_FILL:
            cmds.append([PY, COLLECTOR[mall], *CATEGORY_FILL[mall]])
        return cmds

    if stage == 'CONVERT':
        if mall == OKMALL:
            return [[PY, CONV_OKMALL, '--source', mall, '--skip-translation']]
        return [[PY, CONV_MULTI, '--source-site', mall, '--skip-translation']]

    if stage == 'PRICE':
        return [[PY, PRICE, '--source', mall, '--new-only']]

    if stage == 'TRANSLATE':
        return [[PY, TRANSLATE, '--source', mall, '--price-checked-only']]

    if stage == 'IMAGE':
        if mall == OKMALL:
            # okmall: 이미지 수집 후 업로드 (멀티소스는 collector가 이미지URL 확보 → 업로드만)
            return [
                [PY, IMG_COLLECT, '--source', mall, '--price-checked-only'],
                [PY, IMG_UPLOAD, '--source', mall],
            ]
        return [[PY, IMG_UPLOAD, '--source', mall]]

    if stage == 'THUMBNAIL':
        # 대표이미지(position=1)에 뱃지 합성 → R2 업로드 → ace_product_thumbnails 기록.
        #   이미 생성된 건 스킵(멱등)이라 매일 돌아도 신규분만 처리.
        #   등록/수정 시 get_product_images·_images 가 이 썸네일을 대표이미지로 대신 보낸다.
        return [[PY, THUMBNAIL, '--source', mall]]

    if stage == 'REGISTER':
        # 신규등록 = reconcile auto --scope new (미등록 그룹만 → ensure_group → CREATE)
        #   ★ --limit 크게 필수: reconcile_runner 기본 limit=3 이라 안 주면 3건만 등록됨.
        return [[PY, RECONCILE, '--mode', 'auto', '--scope', 'new',
                 '--source', mall, '--limit', '100000', '--execute', '--confirm-live']]

    return []


# =====================================================
# 유닛 빌드
# =====================================================
def build_units(malls, track):
    units = []
    for mall in malls:
        # naver 몰만 공유 자원 'naver'(캡챠 직렬). 그 외는 None(잠금 없음, 전부 병렬).
        site = 'naver' if mall in NAVER_MALLS else None
        if track in ('new', 'all'):
            units.append({'mall': mall, 'unit_key': '_ALL_', 'track': 'NEW', 'site_resource': site})
        if track in ('stock', 'all'):
            units.append({'mall': mall, 'unit_key': '_ALL_', 'track': 'STOCK', 'site_resource': site})
    return units


def main():
    ap = argparse.ArgumentParser(description='통합 일일 자동화 (33몰: okmall + 멀티소스 10 + naver 22)')
    ap.add_argument('--plan', action='store_true', help='실행 명령만 출력(DB·실행 없음)')
    ap.add_argument('--dry-run', action='store_true', help='엔진 가동(상태기록)하되 명령은 no-op 로그')
    ap.add_argument('--only', type=str, help=f'특정 몰만 (지원: {", ".join(MALLS)})')
    ap.add_argument('--no-naver', action='store_true',
                    help='naver 21몰 제외 (okmall+멀티소스만). naver 는 캡챠로 별도 standalone 권장')
    ap.add_argument('--only-naver', action='store_true', help='naver 21몰만 실행')
    ap.add_argument('--pc', type=int, choices=[1, 2, 3], default=None,
                    help='only-naver 21몰을 3분할해 이 PC 몫만 (1/2/3). --only-naver 와 함께.')
    ap.add_argument('--track', type=str, default='all', choices=['new', 'stock', 'all'],
                    help='실행 트랙 (기본 all)')
    ap.add_argument('--max-workers', type=int, default=None, help='동시 유닛 수')
    args = ap.parse_args()

    if args.only_naver and args.no_naver:
        print("⛔ --only-naver 와 --no-naver 는 동시 사용 불가")
        return

    if args.pc and not args.only_naver:
        print("⛔ --pc 는 --only-naver 와 함께만 사용 (naver 3분할)")
        return

    malls = MALLS
    if args.only:
        if args.only not in MALLS:
            print(f"⛔ 지원하지 않는 몰: {args.only} (지원: {', '.join(MALLS)})")
            return
        malls = [args.only]
    elif args.only_naver:
        malls = NAVER_SPLIT[args.pc] if args.pc else list(NAVER)
    elif args.no_naver:
        malls = [m for m in MALLS if m not in NAVER_MALLS]

    units = build_units(malls, args.track)

    # ---- --plan: DB/실행 없이 명령만 출력 ----
    if args.plan:
        print(f"=== 통합 자동화 PLAN — 몰 {len(malls)} / 유닛 {len(units)} (track={args.track}) ===\n")
        for u in units:
            print(f"[{u['mall']}] track={u['track']}")
            for stage in STAGE_PLAN[u['track']]:
                cmds = worker_resolver(u, stage)
                for cmd in cmds:
                    disp = ['python'] + [os.path.relpath(c, BASE) if c.startswith(BASE) else c for c in cmd[1:]]
                    print(f"    {stage}: {' '.join(disp)}")
            print()
        return

    eng = pe.PipelineEngine(
        # naver 만 돌릴 땐 별도 run_mode → 별도 배치 → --no-naver(UNIFIED)와 동시 실행해도 배치 충돌 없음
        run_mode=('UNIFIED_NAVER' if args.only_naver else 'UNIFIED'),
        units=units,
        stage_plan=STAGE_PLAN,
        worker_resolver=worker_resolver,
        stage_concurrency=STAGE_CONCURRENCY,
        site_access_stages=SITE_ACCESS_STAGES,
        max_workers=args.max_workers,
        dry_run=args.dry_run,
    )
    eng.run()


if __name__ == '__main__':
    main()
