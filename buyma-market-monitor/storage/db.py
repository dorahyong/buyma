"""DB connection & schema.

백엔드 2개 지원 (환경변수 MONITOR_DB 로 선택):
  - mysql  (기본)  : buyma MySQL DB 의 market_* 테이블에 저장 (운영).
  - sqlite         : 기존 SQLite (테스트 = tests/conftest.py 가 sqlite 강제).

MySQL 경로는 sqlite3.Connection 호환 shim(_MySQLConn) 을 반환하므로 repo 코드(conn.execute
/.executemany/.fetchone/.fetchall, row["col"]·row[0])는 수정 없이 그대로 동작한다.
shim 이 SQLite→MySQL dialect 를 자동 변환한다:
  ?→%s / 테이블명→market_ 접두어 / INSERT OR IGNORE→INSERT IGNORE /
  ON CONFLICT(col) DO UPDATE SET→ON DUPLICATE KEY UPDATE / excluded.x→VALUES(x)
"""
import os
import re
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 12

# ---- .env (buyma DB 자격증명) 로드: buyma-market-monitor 의 상위 buyma 폴더 .env ----
_ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"
)
try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_PATH, override=False)
except Exception:
    pass


def _use_mysql() -> bool:
    return os.getenv("MONITOR_DB", "mysql").strip().lower() == "mysql"


# =====================================================================
# SQLite (테스트/레거시) — 원본 스키마 유지
# =====================================================================
_DDL = """
CREATE TABLE IF NOT EXISTS items (
  item_id             TEXT PRIMARY KEY,
  seller_id           TEXT NOT NULL,
  name                TEXT NOT NULL,
  current_price       INTEGER,
  brand               TEXT,
  category_path       TEXT,
  origin_country      TEXT,
  image_url           TEXT,
  description         TEXT,
  size_guide_text     TEXT,
  view_count          INTEGER,
  fav_count           INTEGER,
  inquiry_count       INTEGER,
  brand_model_number  TEXT,
  tags                TEXT,
  themes              TEXT,
  size_chart_json     TEXT,
  status              TEXT NOT NULL,
  first_seen_at       TEXT NOT NULL,
  last_seen_at        TEXT NOT NULL,
  sold_out_at         TEXT,
  deleted_at          TEXT,
  detail_fetched_at   TEXT,
  listed_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_seller ON items(seller_id);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);

CREATE TABLE IF NOT EXISTS price_history (
  item_id      TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  price        INTEGER NOT NULL,
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_price_history_item ON price_history(item_id);

CREATE TABLE IF NOT EXISTS orders (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  seller_id    TEXT NOT NULL,
  item_id      TEXT NOT NULL,
  item_name    TEXT,
  item_url     TEXT,
  qty          INTEGER,
  sale_date    TEXT NOT NULL,
  collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_seller    ON orders(seller_id);
CREATE INDEX IF NOT EXISTS idx_orders_sale_date ON orders(sale_date);
CREATE INDEX IF NOT EXISTS idx_orders_item      ON orders(item_id);

CREATE TABLE IF NOT EXISTS order_watermarks (
  seller_id              TEXT PRIMARY KEY,
  signature_json         TEXT NOT NULL,
  last_run_at            TEXT,
  pages_scanned_last_run INTEGER,
  orders_added_last_run  INTEGER
);

CREATE TABLE IF NOT EXISTS order_run_meta (
  id                  INTEGER PRIMARY KEY CHECK (id = 1),
  last_run_at         TEXT,
  last_run_stats_json TEXT
);

CREATE TABLE IF NOT EXISTS sellers (
  seller_id      TEXT PRIMARY KEY,
  seller_name    TEXT,
  seller_type    TEXT,
  seller_url     TEXT,
  country        TEXT,
  follower_count INTEGER,
  listing_count  INTEGER,
  order_count    INTEGER,
  first_seen_at  TEXT,
  updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS item_images (
  item_id    TEXT NOT NULL,
  position   INTEGER NOT NULL,
  image_url  TEXT NOT NULL,
  PRIMARY KEY (item_id, position)
);
CREATE INDEX IF NOT EXISTS idx_item_images_item ON item_images(item_id);

CREATE TABLE IF NOT EXISTS stats_history (
  item_id      TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  view_count    INTEGER,
  fav_count     INTEGER,
  inquiry_count INTEGER,
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_stats_history_item ON stats_history(item_id);

CREATE TABLE IF NOT EXISTS item_variants (
  item_id       TEXT NOT NULL,
  variant_sku   TEXT NOT NULL,
  color         TEXT,
  size          TEXT,
  price         INTEGER,
  availability  TEXT,
  stock_min     INTEGER,
  stock_max     INTEGER,
  PRIMARY KEY (item_id, variant_sku)
);
CREATE INDEX IF NOT EXISTS idx_item_variants_item ON item_variants(item_id);

CREATE TABLE IF NOT EXISTS revisit_state (
  item_id          TEXT PRIMARY KEY,
  tier             TEXT,
  base_tier        TEXT,
  seller_id        TEXT,
  last_observed_at TEXT,
  next_revisit_at  TEXT,
  obs_count        INTEGER,
  last_velocity    REAL
);
CREATE INDEX IF NOT EXISTS idx_revisit_next ON revisit_state(next_revisit_at);
CREATE INDEX IF NOT EXISTS idx_revisit_tier_seller ON revisit_state(tier, seller_id);

CREATE TABLE IF NOT EXISTS seller_scan_state (
  seller_id       TEXT PRIMARY KEY,
  value_tier      TEXT,
  value_score     INTEGER,
  last_scanned_at TEXT,
  next_scan_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_seller_scan_next ON seller_scan_state(next_scan_at);

CREATE TABLE IF NOT EXISTS exposure_snapshot (
  snapshot_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  model_query      TEXT NOT NULL,
  observed_at      TEXT NOT NULL,
  n_results_page1  INTEGER,
  total_results    INTEGER,
  floor_price_yen  INTEGER,
  status           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exposure_snapshot_model ON exposure_snapshot(model_query);
CREATE INDEX IF NOT EXISTS idx_exposure_snapshot_obs ON exposure_snapshot(observed_at);

CREATE TABLE IF NOT EXISTS exposure_history (
  snapshot_id  INTEGER NOT NULL,
  rank         INTEGER NOT NULL,
  model_query  TEXT NOT NULL,
  item_id      TEXT NOT NULL,
  price_yen    INTEGER,
  seller_name  TEXT,
  seller_id    TEXT,
  observed_at  TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, rank)
);
CREATE INDEX IF NOT EXISTS idx_exposure_history_item ON exposure_history(item_id);
CREATE INDEX IF NOT EXISTS idx_exposure_history_model ON exposure_history(model_query);
CREATE INDEX IF NOT EXISTS idx_exposure_history_obs ON exposure_history(observed_at);

CREATE TABLE IF NOT EXISTS exposure_state (
  model_query       TEXT PRIMARY KEY,
  last_collected_at TEXT,
  last_status       TEXT
);

CREATE TABLE IF NOT EXISTS stylehaus_history (
  item_id              TEXT NOT NULL,
  observed_at          TEXT NOT NULL,
  has_style_haus       INTEGER NOT NULL,
  stylehaus_video_count INTEGER,
  PRIMARY KEY (item_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_stylehaus_history_item ON stylehaus_history(item_id);

CREATE TABLE IF NOT EXISTS variant_history (
  item_id      TEXT NOT NULL,
  variant_sku  TEXT NOT NULL,
  observed_at  TEXT NOT NULL,
  availability INTEGER NOT NULL,
  PRIMARY KEY (item_id, variant_sku, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_variant_history_observed ON variant_history(observed_at);
"""


# =====================================================================
# MySQL shim — sqlite3.Connection 호환
# =====================================================================
import pymysql
from pymysql.cursors import DictCursor

# SQLite 테이블명 → market_ 접두어 (FROM/INTO/UPDATE/JOIN 뒤 테이블 위치에서만)
_TABLES = sorted(
    ["items", "price_history", "orders", "order_watermarks", "order_run_meta",
     "sellers", "item_images", "stats_history", "item_variants",
     "revisit_state", "seller_scan_state",
     "exposure_snapshot", "exposure_history", "exposure_state",
     "stylehaus_history", "variant_history"],
    key=len, reverse=True,
)
_TBL_RE = re.compile(r"\b(FROM|INTO|UPDATE|JOIN)(\s+)(" + "|".join(_TABLES) + r")\b", re.I)
_ONCONFLICT_RE = re.compile(r"ON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET", re.I)
_EXCLUDED_RE = re.compile(r"\bexcluded\.(\w+)", re.I)


def _translate(sql: str) -> str:
    sql = re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT IGNORE", sql, flags=re.I)
    sql = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "REPLACE INTO", sql, flags=re.I)
    sql = _ONCONFLICT_RE.sub("ON DUPLICATE KEY UPDATE", sql)
    sql = _EXCLUDED_RE.sub(r"VALUES(\1)", sql)
    sql = _TBL_RE.sub(lambda m: m.group(1) + m.group(2) + "market_" + m.group(3), sql)
    # named placeholder :name → %(name)s  (dict params). 문자로 시작해 시간literal(:30) 오탐 방지.
    sql = re.sub(r":([A-Za-z_]\w*)", r"%(\1)s", sql)
    sql = sql.replace("?", "%s")            # positional placeholder → %s
    return sql


class _Row(dict):
    """sqlite3.Row 호환: row["col"] 와 row[정수인덱스] 둘 다 지원."""
    def __init__(self, d: dict):
        super().__init__(d)
        self._vals = list(d.values())

    def __getitem__(self, k):
        if isinstance(k, int):
            return self._vals[k]
        return dict.__getitem__(self, k)

    def __iter__(self):
        # sqlite3.Row 처럼 순회·언패킹((x,)=row) 시 "값"을 냄 (dict 기본은 키라 buggy).
        return iter(self._vals)


class _HybridCursor(DictCursor):
    def fetchone(self):
        r = super().fetchone()
        return _Row(r) if r is not None else None

    def fetchall(self):
        return [_Row(r) for r in super().fetchall()]

    def fetchmany(self, size=None):
        return [_Row(r) for r in super().fetchmany(size)]

    def __iter__(self):
        while True:
            r = self.fetchone()
            if r is None:
                break
            yield r


# 연결 끊김(idle drop 등) 에러코드 → 재연결 후 재시도
_LOST_CODES = (2013, 2006, 2055, 0)


class _MySQLConn:
    """sqlite3.Connection 호환 shim. buffered cursor + db_lock 직렬화 전제(운영은 워커가 락 사용).
    ★ 장시간 데몬 대비: 연결이 idle 로 끊기면(원격 MySQL) 자동 재연결 후 재시도."""
    def __init__(self, params: dict):
        self._params = params
        self._raw = pymysql.connect(**params)

    def _reconnect(self):
        try:
            self._raw.close()
        except Exception:
            pass
        self._raw = pymysql.connect(**self._params)

    def _run(self, method: str, sql: str, arg):
        q = _translate(sql)
        try:
            cur = self._raw.cursor(_HybridCursor)
            getattr(cur, method)(q, arg)
            return cur
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            code = e.args[0] if e.args else None
            msg = str(e)
            if code in _LOST_CODES or "Lost connection" in msg or "gone away" in msg or "Broken pipe" in msg:
                self._reconnect()
                cur = self._raw.cursor(_HybridCursor)
                getattr(cur, method)(q, arg)
                return cur
            raise

    def execute(self, sql, params=()):
        return self._run("execute", sql, params or ())

    def executemany(self, sql, seq):
        return self._run("executemany", sql, list(seq))

    def commit(self):
        try:
            self._raw.commit()
        except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
            self._reconnect()  # autocommit=True 라 커밋 유실 없음

    def close(self):
        try:
            self._raw.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        return False


def _mysql_connect() -> "_MySQLConn":
    params = dict(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4",
        autocommit=True,       # sqlite isolation_level=None 동등
        connect_timeout=10,    # ★ 재연결이 무한 대기(hung) 방지
        # 집계쿼리(recompute hot_warm ~35s)가 부하 spike때 타임아웃으로 죽지 않게 넉넉히.
        # 대형셀러 20분멈춤은 묶음저장으로 근본해결됨 → 짧은 read_timeout 불필요.
        read_timeout=300,      # 쿼리 응답 대기 상한(진짜 hang은 여기서 끊고 재연결)
        write_timeout=300,
    )
    return _MySQLConn(params)


_MYSQL_DDL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "mysql_schema.sql"
)


# =====================================================================
# public
# =====================================================================
def connect(db_path: Path | str | None = None):
    if _use_mysql():
        return _mysql_connect()
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn) -> None:
    if isinstance(conn, _MySQLConn):
        ddl = open(_MYSQL_DDL_PATH, encoding="utf-8").read()
        body = "\n".join(l for l in ddl.splitlines() if not l.strip().startswith("--"))
        for stmt in [s.strip() for s in body.split(";") if s.strip()]:
            conn.execute(stmt)
        return
    conn.executescript(_DDL)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
