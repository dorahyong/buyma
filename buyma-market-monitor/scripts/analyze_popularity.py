"""인기 상품 기준 도출용 탐색적 분석.

수집 데이터의 view/fav/inquiry/price 분포와, 실제 판매(orders) 여부에 따른
지표 차이·판매 lift를 출력한다. Project 2(적응형 관측 스케줄러)의 '인기' 정의
기준을 데이터 기반으로 잡기 위한 1회성 분석 스크립트.
"""
import sqlite3
import statistics
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "items.db"

PCTS = [50, 75, 90, 95, 99, 99.9]


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def describe(name, vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        print(f"  {name}: (no data)")
        return
    s = sorted(vals)
    parts = [f"n={len(s)}", f"mean={statistics.mean(s):.1f}"]
    for p in PCTS:
        parts.append(f"p{p}={pct(s, p):.0f}")
    parts.append(f"max={s[-1]}")
    print(f"  {name}: " + "  ".join(parts))


def main():
    c = sqlite3.connect(str(DB))

    # 팔린 적 있는 item_id 집합 (items에 존재하는 것만)
    sold = set(
        r[0] for r in c.execute(
            "SELECT DISTINCT o.item_id FROM orders o JOIN items i ON o.item_id=i.item_id"
        )
    )
    print(f"[ground truth] 판매 이력 있는 매칭 상품: {len(sold):,}\n")

    # 전체 enriched 상품 지표 로드
    rows = c.execute(
        "SELECT item_id, view_count, fav_count, inquiry_count, current_price "
        "FROM items WHERE detail_fetched_at IS NOT NULL"
    ).fetchall()
    print(f"[로드] enriched 상품: {len(rows):,}\n")

    view = [r[1] for r in rows]
    fav = [r[2] for r in rows]
    inq = [r[3] for r in rows]
    price = [r[4] for r in rows]

    print("=== 전체 분포 ===")
    describe("view   ", view)
    describe("fav    ", fav)
    describe("inquiry", inq)
    describe("price  ", price)

    # 판매 여부 그룹 분리
    sold_view, sold_fav, sold_inq = [], [], []
    un_view, un_fav, un_inq = [], [], []
    for iid, v, f, q, _p in rows:
        if iid in sold:
            sold_view.append(v); sold_fav.append(f); sold_inq.append(q)
        else:
            un_view.append(v); un_fav.append(f); un_inq.append(q)

    print("\n=== 팔린 상품 (n={:,}) ===".format(len(sold_view)))
    describe("view   ", sold_view)
    describe("fav    ", sold_fav)
    describe("inquiry", sold_inq)

    print("\n=== 안 팔린 상품 (n={:,}) ===".format(len(un_view)))
    describe("view   ", un_view)
    describe("fav    ", un_fav)
    describe("inquiry", un_inq)

    # 임계치별 판매 lift: (지표>=t 인 상품의 판매율) / (전체 판매율)
    base_rate = len(sold_view) / len(rows)
    print(f"\n=== 임계치별 판매 lift (전체 판매율 base={base_rate*100:.2f}%) ===")

    def lift_table(label, getter, thresholds):
        print(f"\n[{label}]  임계치 / 해당상품수 / 그중판매수 / 판매율 / lift")
        for t in thresholds:
            grp = [(iid in sold) for iid, v, f, q, _p in rows if getter(v, f, q) is not None and getter(v, f, q) >= t]
            if not grp:
                continue
            sold_n = sum(grp)
            rate = sold_n / len(grp)
            lift = rate / base_rate if base_rate else 0
            print(f"  >={t:>6}  n={len(grp):>9,}  sold={sold_n:>7,}  rate={rate*100:5.2f}%  lift={lift:4.1f}x")

    lift_table("fav", lambda v, f, q: f, [5, 10, 20, 50, 100, 200, 500])
    lift_table("view", lambda v, f, q: v, [100, 500, 1000, 2000, 5000, 10000])
    lift_table("inquiry", lambda v, f, q: q, [1, 2, 3, 5, 10])


if __name__ == "__main__":
    main()
