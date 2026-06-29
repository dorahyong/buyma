# -*- coding: utf-8 -*-
"""
mall_brands의 미매핑(buyma_brand_id IS NULL) 브랜드를 buyma brands.csv에 매칭.
스토어 단위로 scope + dry-run 미리보기 지원 (kasina/match_brands.py의 안전 버전).

매칭 레벨: 1=영문 완전일치 / 2=특수문자 제거 후 일치 / 3=특수문자 제거 후 포함관계 / 0=실패
매핑은 buyma_brand_id·buyma_brand_name·mapping_level·is_mapped만 갱신 — **is_active는 안 건드림**
(활성화는 검수 페이지에서 사람이 판단).

사용법:
    python map_new_brands.py --source carpi            # dry-run 미리보기
    python map_new_brands.py --source carpi --apply     # 실제 매핑
"""
import os, re, csv, argparse
import pymysql
from dotenv import load_dotenv
load_dotenv()

BRANDS_CSV = os.path.join(os.path.dirname(__file__), "buyma_master_data", "brands.csv")


def clean(s):
    return re.sub(r"[^a-zA-Z0-9]", "", s or "").upper()


def english_name(brand_name):
    m = re.match(r"^(.+?)(\(.*\))?$", brand_name or "")
    return (m.group(1).strip() if m else (brand_name or "").strip())


def load_buyma_brands():
    brands = []
    with open(BRANDS_CSV, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            en = english_name(row["brand_name"])
            brands.append({"id": int(row["id"]), "name_full": row["brand_name"],
                           "upper": en.upper(), "clean": clean(en)})
    return brands


def match_brand(mall_en, buyma_brands):
    mu, mc = (mall_en or "").strip().upper(), clean(mall_en)
    for b in buyma_brands:           # L1 완전일치
        if mu == b["upper"]:
            return b["id"], b["name_full"], 1
    for b in buyma_brands:           # L2 특수문자 제거 일치
        if mc and mc == b["clean"]:
            return b["id"], b["name_full"], 2
    cands = [b for b in buyma_brands if mc and b["clean"] and (mc in b["clean"] or b["clean"] in mc)]
    if cands:                        # L3 포함관계 (길이 가장 비슷한 것)
        cands.sort(key=lambda b: abs(len(b["clean"]) - len(mc)))
        return cands[0]["id"], cands[0]["name_full"], 3
    return None, None, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="스토어 (예: carpi)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-l3", action="store_true",
                    help="L3(포함관계)도 매핑. 기본은 제외(오매칭 잦음 — HOWLIN→OWL 등) → 검수대기 유지")
    args = ap.parse_args()

    buyma_brands = load_buyma_brands()
    print(f"buyma brands.csv: {len(buyma_brands)}개")

    conn = pymysql.connect(host=os.getenv("DB_HOST"), port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()
    cur.execute("SELECT mall_brand_name_en, mall_brand_url, is_active FROM mall_brands "
                "WHERE mall_name=%s AND buyma_brand_id IS NULL", (args.source,))
    rows = cur.fetchall()
    print(f"미매핑 ({args.source}): {len(rows)}개\n")

    stats = {0: 0, 1: 0, 2: 0, 3: 0}
    updates = []
    for r in rows:
        en = r["mall_brand_name_en"]
        bid, bname, level = match_brand(en, buyma_brands)
        stats[level] += 1
        skip_l3 = (level == 3 and not args.include_l3)
        if level > 0 and not skip_l3:
            print(f"  [L{level}] {en:22} → {bname} (id={bid})")
            updates.append((bid, bname, level, args.source, r["mall_brand_url"], en))
        elif level == 3:
            print(f"  [L3] {en:22} → {bname} (id={bid})  ⚠️ 포함관계라 제외(검수대기 유지)")
        else:
            print(f"  [L0] {en:22} → ❌ 매칭 없음 (검수대기 유지)")

    print(f"\n===== 매칭: L1={stats[1]} L2={stats[2]} L3={stats[3]} / 실패 L0={stats[0]} =====")
    print(f"     적용 대상(L1+L2{'+L3' if args.include_l3 else ''}): {len(updates)}개")

    if not args.apply:
        print(f"\n[DRY-RUN] 실제 매핑: python {os.path.basename(__file__)} --source {args.source} --apply")
        conn.close(); return

    for bid, bname, level, src, url, en in updates:
        if url:
            cur.execute("UPDATE mall_brands SET buyma_brand_id=%s, buyma_brand_name=%s, mapping_level=%s, is_mapped=1 "
                        "WHERE mall_name=%s AND mall_brand_url=%s AND buyma_brand_id IS NULL", (bid, bname, level, src, url))
        else:
            cur.execute("UPDATE mall_brands SET buyma_brand_id=%s, buyma_brand_name=%s, mapping_level=%s, is_mapped=1 "
                        "WHERE mall_name=%s AND mall_brand_name_en=%s AND buyma_brand_id IS NULL", (bid, bname, level, src, en))
    conn.commit()
    print(f">>> {len(updates)}개 매핑 완료. (is_active는 그대로 — 검수 페이지에서 확인/활성화)")
    conn.close()


if __name__ == "__main__":
    main()
