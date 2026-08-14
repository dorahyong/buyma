# -*- coding: utf-8 -*-
"""draft scoring 공통: DB 연결 · mn_norm 토큰화."""
from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence

import pymysql
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE, ".env"), override=True)

MN_BLACKLIST = frozenset(
    {
        "FREE",
        "001",
        "ONESIZE",
        "ONESIZEFREE",
        "FREESIZE",
        "SIZE",
        "NULL",
        "NONE",
        "NOLABEL",
        # 색
        "BLACK",
        "WHITE",
        "NERO",
        "BIANCO",
        "MARINE",
        "BLK",
        "BLUE",
        "RED",
        "GREEN",
        "PINK",
        "BROWN",
        "BEIGE",
        "GREY",
        "GRAY",
        "NAVY",
        "IVORY",
        "CREAM",
        "YELLOW",
        "ORANGE",
        "PURPLE",
        "SILVER",
        "GOLD",
        "MULTI",
        "CLEAR",
        "CHARCOAL",
        "INDIGO",
        "NATURAL",
        "HEATHER",
        "NOUGAT",
        "LICHEN",
        # 옷종류·스타일 단어
        "TSHIRT",
        "TSHIRTS",
        "SHIRT",
        "SHIRTS",
        "SWEATER",
        "SWEATSHIRT",
        "HOODIE",
        "JACKET",
        "CREWNECK",
        "PULLOVER",
        "CARDIGAN",
        "BLOUSE",
        "BOTTOM",
        "BOTTOMS",
        "ONEPIECE",
        "WIDELEG",
        "CROPPED",
        "COLLAR",
        "BEANIE",
        "BIKINI",
        "WALLET",
        "BUCKET",
        # 기타 일반 영단어(L1 노이즈)
        "NOSTALGIA",
        "VINTAGE",
        "ARCHIVE",
        "ESSENTIAL",
        "DIAMOND",
        "SCRIPT",
        "PIGMENT",
        "HICKORY",
        "DONKEY",
        "DEXTER",
        "AURELIE",
        "POLOOFF",
        "CONNECTOR",
    }
)
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
# 25SS / SS25 / 26FW 등
_SEASON_RE = re.compile(r"^(?:20)?\d{2}(?:SS|FW|AW|SP)$|^(?:SS|FW|AW)\d{2}$")
# L1에서 "강한 품번" — 짧은 색·옵션 코드(N401 등)와 구분
STRONG_MN_MIN_LEN = 6


def connect(**extra):
    cfg = dict(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=30,
        read_timeout=int(extra.pop("read_timeout", 600)),
        write_timeout=int(extra.pop("write_timeout", 600)),
        autocommit=extra.pop("autocommit", True),
    )
    cfg.update(extra)
    return pymysql.connect(**cfg)


def normalize_mn_token(raw: str) -> Optional[str]:
    if not raw:
        return None
    t = _NON_ALNUM.sub("", raw.strip().upper())
    if len(t) < 4 or t in MN_BLACKLIST:
        return None
    if _SEASON_RE.match(t):
        return None
    if t.isdigit() and len(t) <= 4:
        return None
    return t[:64]


def is_strong_mn(tok: Optional[str]) -> bool:
    """L1 우선 토큰. 길이≥6만 강함(짧은 옵션/색코드 배제)."""
    return bool(tok) and len(tok) >= STRONG_MN_MIN_LEN


def tokenize_model_no(model_no: Optional[str]) -> List[str]:
    """TRIM → 공백 분리 → 토큰별 UPPER+비영숫자제거 → 길이≥4. 중복 제거(순서 유지)."""
    if not model_no:
        return []
    seen = set()
    out: List[str] = []
    for part in model_no.strip().split():
        tok = normalize_mn_token(part)
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def representative_mn(tokens: Sequence[str]) -> Optional[str]:
    """캐시용 대표: 강한 토큰 우선, 없으면 첫 토큰."""
    if not tokens:
        return None
    for t in tokens:
        if is_strong_mn(t):
            return t
    return tokens[0]


# --- 2·3번 셀 키 / 축소식 ---
KAPPA = 0.3663
C_MARGIN = 0.6107
K_SHRINK = 50
L6_DENSITY = 0.0565
WINDOW_DAYS = 90


def brand_key_from_listing(brand_name: Optional[str]) -> str:
    if not brand_name:
        return ""
    return brand_name.split("(", 1)[0].strip().upper()


def brand_key_from_market(brand: Optional[str]) -> str:
    if not brand:
        return ""
    return brand.strip().upper()


def price_band(price_jpy) -> str:
    try:
        p = float(price_jpy or 0)
    except (TypeError, ValueError):
        p = 0.0
    if p < 10000:
        return "<10k"
    if p < 20000:
        return "10-20k"
    if p < 30000:
        return "20-30k"
    if p < 50000:
        return "30-50k"
    if p < 80000:
        return "50-80k"
    if p < 120000:
        return "80-120k"
    return "120k+"


def market_cat_key(category_path: Optional[str]) -> str:
    """첫·끝 세그 제거 후 '/' 결합."""
    if not category_path:
        return ""
    segs = [s.strip() for s in category_path.split(">") if s.strip()]
    if len(segs) <= 2:
        return ""
    return "/".join(segs[1:-1])


def listing_cat_key(buyma_paths: Optional[str], buyma_name: Optional[str]) -> str:
    paths = (buyma_paths or "").strip().strip("/")
    name = (buyma_name or "").strip()
    if not paths and not name:
        return ""
    if not paths:
        return name
    if not name:
        return paths
    return f"{paths}/{name}"


def cell_key_l3(brand: str, cat: str, band: str) -> str:
    return f"{brand}|{cat}|{band}"


def cell_key_l4(cat: str, band: str) -> str:
    return f"{cat}|{band}"


def cell_key_l6() -> str:
    return "GLOBAL"


def clip_adj(x: float, lo: float = 0.5, hi: float = 3.0) -> float:
    return max(lo, min(hi, x))


def shrink_density(ord90: int, n_act: int, parent_d: float, k: int = K_SHRINK) -> float:
    return (ord90 + k * parent_d) / (n_act + k)
