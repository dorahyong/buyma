# -*- coding: utf-8 -*-
"""상품명 정리 규칙 한 곳 모음 (수집처별 + 전 몰 공통)

왜 이 파일이 있나
------------------
몰마다 상품명에 자기네 홍보 문구·지점명·태그를 붙여 판다.
그대로 두면 BUYMA 상품명에 "[워드로브]" "(신세계 강남점)" "럭키찬스" 같은 게 그대로 나간다.
전에는 이 규칙이 수집기 15개에 흩어져 있어서, 규칙 하나 고치려면 파일을 여러 개 뒤져야 했다.
이 파일이 그 규칙 전부를 담는다. 수집기는 `clean_product_name(몰이름, 상품명)` 한 줄만 부른다.

규칙을 새로 넣을 때 지켜야 할 것
--------------------------------
1. **멱등이어야 한다** — 두 번 적용해도 결과가 같아야 한다.
   지우기만 하는 규칙은 자연히 멱등이다. 값을 덧붙이거나 바꾸는 규칙은 멱등이 깨지기 쉬우니 피한다.
   (변환 단계에서 한 번 더 부를 수 있어야 하므로 이 조건이 필요하다)
2. **모델번호를 지우지 않는지 확인한다** — 색상코드 `20F`·`23S` 는 시즌코드와 생김새가 같다.
   ★ 정리된 이름에서 모델번호를 뽑는 몰(loromoda·milaneez·maisonparco·unico 등)에서 특히 위험하다.
3. 브랜드명을 지우지 않는지 확인한다 — 예) '국내'로 시작하는 브랜드는 없어서 국내마커 규칙이 안전하다.

규칙 쓰는 법
------------
- 문자열  → 그 부분을 **지운다**
- (패턴, 문자열) → **바꾼다**
- (패턴, 함수) → 함수가 돌려주는 값으로 **바꾼다**
"""

import re


# =====================================================
# 전 몰 공통 규칙
# =====================================================

# 국내판매 마커: [국내...]/(국내...) 괄호토큰 + 관부가세포함 + 국냄매장판(오타).
#   국내백화점/매장판/매장/당일/판/배송/신상/매장발송 등 '국내~' 전부. 브랜드명은 국내로 시작 안 함 → 안전.
#   ★일본어 번역 변형(国内店舗版/国内正規品/韓国百貨店/国内当日 등)은 이 한글 마커를 지우면 원천 발생 안 함.
GLOBAL_DOMESTIC_PATTERN = r'[\[(]\s*(?:국내[^\]\)]*|관부가세포함|국냄매장판)\s*[\])]\s*'

# 적용 순서: 국내마커 → 시즌토큰(strip_season_tokens)
GLOBAL_PATTERNS = [GLOBAL_DOMESTIC_PATTERN]


# =====================================================
# 몰별 규칙
# =====================================================

def _brickmansion_bracket(m):
    """대괄호 정리:
       - 콜라보 [A x B] (x/X/× 포함) → 대괄호만 벗기고 콜라보 텍스트는 남긴다
       - 그 외 [브랜드/홍보] (last size, brand sale, adizero sale, 본사공식, jerusalem sandals 등) → 통째 지운다
    """
    inner = m.group(1)
    return f" {inner} " if re.search(r'[Xx×]', inner) else " "


MALL_PATTERNS = {
    # ---- 네이버 스토어 ----
    'dmont': [
        r'^\s*디몬트\s+',           # 앞에 붙은 '디몬트 ' 접두사
    ],
    'tuttobene': [
        r'\[국내배송\]\s*',
        r'\[[0-9]+%중복쿠폰\]\s*',
    ],
    'maniaon':    [r'\s*매니아온\s*$', r'\[국내배송\]\s*'],       # 끝의 '매니아온' suffix + [국내배송]
    # 앞의 '실시간유럽' 머리말. 괄호 있는 것([실시간유럽])과 없는 것(실시간유럽 프라다…)이 섞여 있고
    # 2026-07-27 199건 중 42건(괄호 14/민 28) 전부 맨 앞 → 괄호 옵셔널 + ^ 앵커.
    'luvgrande':  [r'^\s*[\[(]?\s*실시간유럽\s*[\])]?\s*'],
    'pano':       [r'\[국내신상\]\s*'],
    'wardrobe':   [r'\[워드로브\]\s*'],                         # 스토어 태그 '[워드로브]' 제거.
    'milanosangin': [r'[\[(]\s*당일\s*[\])]\s*'],              # 당일배송 표시 '(당일)' 제거. (미세하자 당일)'은 STORE_EXCLUDE_KEYWORDS 의 '하자'로 걸러짐
    # 상품명 맨 앞 소괄호 토큰 전부 제거 — (국내아울렛) (수량한정) (국배내송) 등 종류가 다양해 내용을 열거하지 않고 '맨 앞 괄호'라는 위치로 지운다. 연속으로 붙은 것도 모두.
    #   ★단 '스크래치'가 든 괄호는 남긴다 — 지워버리면 아래 제외 규칙이 못 걸린다.
    'gimpooutlet': [r'^(?:\s*\((?![^)]*스크래치)(?![^)]*스크레치)[^)]{1,30}\)\s*)+'],
    'larlashoes': [r'[\[(]\s*(?:국내매장판|국내매장|국냄매장판)\s*[\])]\s*'],           # 국내매장판/매장/오타 국냄매장판 (괄호무관, 브랜드명 제외)
    'luxlimit':   [r'[\[(]\s*(?:국내백화점|국내매장판|국내매장|국내당일|관부가세포함)\s*[\])]\s*'],  # 국내백화점/매장판/매장/당일/관부가세포함 (괄호무관, 브랜드명 제외)
    'shinsegae':  [
        r'\(\s*[^()]*신세계[^()]*\)\s*',      # 판매 백화점 지점명: (신세계 강남점)/(광주신세계)/(신세계 사우스시티) 등
        r'\[\s*[^\[\]]*신세계[^\[\]]*\]\s*',   # 브랜드자리 백화점 태그: [신세계백화점] 등
    ],

    # ---- 자체몰 / cafe24 ----
    '9tems':        [(r"\s*럭키찬스'?\s*", ' ')],       # 홍보 문구 '럭키찬스' (따옴표 동반하기도 함)
    'brickmansion': [(r"\[([^\]]*)\]", _brickmansion_bracket)],   # 대괄호 — 콜라보만 내용 남김
    'loromoda':     [r'^\s*\[로로모다\]\s*'],           # 스토어 태그 접두어
    'laprima':      [(r'\s*상품명\s*', ' ')],           # HTML에서 딸려오는 '상품명' 라벨
    # 앞 [브랜드명] — 변환기가 브랜드를 따로 붙이므로 중복 방지.
    #   `[메종 키츠네] [23SS]` 처럼 연달아 붙는 경우가 있어 반복(+)으로 받는다.
    #   (하나만 지우면 두 번 돌렸을 때 결과가 달라져 멱등이 깨진다 — 2026-08-05 실측 3건)
    'labellusso':   [r'^(?:\[.*?\]\s*)+'],
    'nextzennpack': [r'^(?:\[.*?\]\s*)+'],
}


# =====================================================
# 시즌 토큰 (상품명·품번 공용)
# =====================================================
# 지운다
#   연도 + SS/FW/SU/AW/WT/SP       26SS 25FW 15SS 24SP 19FW · 2026SS 2025FW
#   SS/FW/SU/AW/WT/SP + 연도       SS25 FW24 AW23 SS26 · SS2026 FW2025
#   26/25/6/5 + F/S/W              26S 25F 26W 6F 5S 5W
#   연도(+년) + F/W · S/S 꼴        25F/W · 24 S/S · 26년F/W · S/S 25년 · 2026 S/S
#     ※ 연도 = 15~29, 앞에 20 이 붙은 네 자리도 같이 본다(2015~2029).
# 지우는 조건
#   공백으로 떨어진 단독 단어일 때만. 앞뒤 괄호는 벗기고 판정한다([26SS] (25FW)).
#   한글·일본어에 바로 붙은 것도 지운다(26SS스투시 → 스투시). 품번엔 한글이 없어 안전하다.
# 손대지 않는다
#   영문·숫자에 붙은 것        19CMSS058A · A25SS · SS25COLLECTION
#   하이픈·슬래시로 이어진 것   25FW-PMPUPP01-541 · 프라다/5S
#   연도 범위 밖              20F · 23S · 01FW · 00AW · 38AW
#   ★연도 없는 맨 S/S · L/S    반팔(Short Sleeve)·긴팔(Long Sleeve)이지 시즌이 아니다.
#     실측: 'L/S T-shirt' 336건과 짝을 이룬 'S/S Shirt' 3,559건. 그래서 연도가 붙은 것만 지운다.
#   ★20F·23S 는 품번 끝에 붙는 색상코드다(예 '1A00108 597YW 20F' — 778·999 자리와 같다).
#     연도를 15~29 로, 한 글자 형태를 26/25/6/5 로 좁게 잡아 이걸 지키고 있다.
# 연도 — 두 자리(25) 와 네 자리(2025) 둘 다. 앞에 숫자가 더 붙어 있으면(1520SS) 시즌이 아니다.
_YEAR = r'(?<![0-9])(?:20)?(?:1[5-9]|2[0-9])'
_HALF = r'(?:SS|FW|SU|AW|WT|SP)'
_SEASON_CORE = (_YEAR + _HALF
                + r'|' + _HALF + _YEAR
                + r'|(?<![0-9])(?:26|25|6|5)[FSW]')
# 슬래시 꼴(F/W · S/S)은 토큰 경계가 아니라 연도 인접으로 가른다 — 연도가 없으면 반팔/긴팔이다.
#   '년' 이 끼는 표기(26년F/W · S/S 25년신상)도 함께 받는다. 실측 신세계·롯데 1,593건.
_SEASON_SLASH_RE = re.compile(
    _YEAR + r'년?\s?[FS]/[SW]'
    r'|[FS]/[SW]\s?' + _YEAR + r'년?(?![0-9])', re.I)
# '붙어 있다'로 볼 이웃 글자 — 영문·숫자와 하이픈·언더바·슬래시·마침표.
#   이 글자들에 닿아 있으면 품번의 일부로 보고 손대지 않는다(25FW-PMPUPP01 · A25SS · 프라다/5S).
#   한글·일본어는 여기 없으므로 '26SS스투시' 는 떨어진 것으로 보아 지운다(품번엔 한글이 없다).
_NEIGHBOR = r'[A-Za-z0-9\-_/.]'
# ① 괄호가 시즌만 감싼 경우 — 괄호째 지운다. "[26SS] 프라다" → "프라다"
_SEASON_BRACKETED_RE = re.compile(
    r'[\[({]\s*(?:' + _SEASON_CORE + r')\s*[\])}]\s*', re.I)
# ② 그 외 떨어져 있는 시즌.
#    앞이 공백·여는괄호·문자열 시작이면 뒤 공백까지 먹는다 — "[26SS 신규입고]" → "[신규입고]".
#    앞이 한글이면 뒤 공백은 남긴다 — "프라다26SS 백" → "프라다 백" (안 그러면 '프라다백' 이 된다).
_SEASON_STANDALONE_RE = re.compile(
    r'(?:^|(?<=[\s\[({]))(?:' + _SEASON_CORE + r')(?!' + _NEIGHBOR + r')\s*'
    r'|(?<!' + _NEIGHBOR + r')(?:' + _SEASON_CORE + r')(?!' + _NEIGHBOR + r')', re.I)


# ★맨 끝에 오는 시즌 모양 토큰은 시즌이 아니라 색상코드다 — 손대지 않는다.
#   색상코드와 시즌은 생김새가 같아(둘 다 숫자2+글자2) 모양으로는 못 가른다. 자리로 가른다.
#   실측(raw 전수, 맨 끝이 시즌 모양인 품번 27건):
#     · 색상코드 확실 14건 — 같은 자리에 색상값이 함께 온다
#         이자벨마랑  'PM0001FA A1X19M 23SU' ← 02FK · 02GY · 30BU · 86LC 와 같은 자리
#         이자벨마랑  'SH0021FB B1J15E 23SU' ← 01BK(블랙) · 20WH(화이트) 와 같은 자리
#         오트리      'AULW SU15'            ← BB52 · CB08 · DG01 · DW02 와 같은 자리 (형제 62종)
#         unico      '15S TRLD WT16'        ← SR01 · SR02 · WT04 와 같은 자리
#     · 시즌으로 보임 13건 — 형제가 없어 증거 부족. 대부분 통합 실행기 밖의 몰.
#   → 지워서 품번을 깨뜨리는 쪽(색상 구분 소실·과병합)이 남겨두는 쪽보다 손해가 크다.
#   → 그룹 판정은 canonicalize 가 따로 시즌을 떼고 하므로, 남겨둬도 묶음은 흔들리지 않는다.
#   ★상품명에도 같은 예외를 쓴다. 상품명은 끝에 품번이 붙는 일이 많고(실측 27%),
#     상품명이 시즌 모양으로 끝나는 581건 중 580건이 품번의 꼬리였다(진짜 시즌은 1건).
#         labellusso '…옌키 로고 토트백PM0001FA A1X19M 23SU'  품번='PM0001FA A1X19M 23SU'
#         lotte      '…앙티브 캔버스 토트백 6Y2B0D18 NDZ RFK 25S' 품번='6Y2B0D18 NDZ RFK 25S'
#     변환기가 조립하는 이름('… 상품명 + 품번')은 항상 품번으로 끝나므로 더욱 그렇다.
_SEASON_LAST_TOKEN_RE = re.compile(r'^(?:' + _SEASON_CORE + r')$', re.I)


def _strip_season(text: str) -> str:
    cleaned = _SEASON_SLASH_RE.sub(' ', text)
    cleaned = _SEASON_BRACKETED_RE.sub('', cleaned)
    cleaned = _SEASON_STANDALONE_RE.sub('', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()


def strip_season_tokens(text: str, model_no: bool = True) -> str:
    """상품명·품번에서 시즌 토큰만 뺀다. 남는 조각의 원문 표기는 유지한다.

    맨 끝 토큰이 시즌 모양이면 손대지 않는다 — 거기 오는 건 색상코드이거나 품번의 꼬리다
    (위 _SEASON_LAST_TOKEN_RE 설명 참고). model_no=False 로 그 보호를 끌 수 있다.

    예) "26SS 프라다 백"            → "프라다 백"
        "[26SS] 프라다 백"          → "프라다 백"          (괄호가 짝이면 통째로)
        "[26SS 신규입고] 프라다"     → "[신규입고] 프라다"    (짝이 아니면 괄호는 남긴다)
        "26SS 1A00108 597YW 20F"  → "1A00108 597YW 20F"   (색상코드 20F 보존)
        "25FW-PMPUPP01-541"       → 그대로                (하이픈은 붙은 것으로 본다)
        "AULW SU15" · "SH0021FB B1J15E 23SU" (model_no=True) → 그대로  (품번 맨 끝은 색상코드)
    같은 값을 두 번 넣어도 결과가 같다(멱등).
    """
    if not text:
        return text
    if model_no:
        parts = text.split()
        # 맨 끝 토큰 보호는 '앞에 진짜 품번이 있을 때'만 뜻이 있다. 품번이 시즌 한 덩어리뿐이면
        #   ('26SS' · '2024SS') 그건 색상코드가 아니라 그냥 품번이 없는 것이므로 지운다.
        if len(parts) >= 2 and _SEASON_LAST_TOKEN_RE.match(parts[-1]):
            head = _strip_season(' '.join(parts[:-1]))
            return (head + ' ' + parts[-1]).strip()
    return _strip_season(text)


# =====================================================
# 품번 유효성
# =====================================================
# 색상명 — 품번 후보에서 색상만 남는 것을 걸러내려고 쓴다.
#   (okmall·naver 수집기가 각자 갖고 있던 목록을 합쳐 한 곳으로 모았다)
_COLOR_WORDS = {
    'BLACK', 'WHITE', 'NAVY', 'GREY', 'GRAY', 'RED', 'BLUE', 'GREEN', 'BROWN',
    'BEIGE', 'PINK', 'CREAM', 'KHAKI', 'ORANGE', 'YELLOW', 'IVORY', 'CAMEL',
    'CHARCOAL', 'SILVER', 'GOLD', 'BURGUNDY', 'OLIVE', 'TAN', 'SAND', 'NATURAL',
    'DARK', 'LIGHT', 'MOSS', 'INK', 'FOG',
}


def is_valid_model_no(model_no: str) -> bool:
    """품번으로 쓸 수 있는 값인지. 수집기들이 쓰던 판정과 같은 규칙이다.

    3자 이하 / 한글 포함 / 색상명만 남는 값은 품번이 아니다.
    이런 값으로 시세를 검색하면 엉뚱한 상품이 잔뜩 잡혀 최저가가 틀어진다.
    """
    s = (model_no or '').strip()
    if len(s) <= 3:
        return False
    if re.search(r'[가-힣ㄱ-ㅎㅏ-ㅣ]', s):
        return False
    parts = [p for p in re.split(r'[\s/\-]+', s.upper()) if p]
    if parts and all(p in _COLOR_WORDS for p in parts):
        return False
    return len(''.join(p for p in parts if p not in _COLOR_WORDS)) > 3


# =====================================================
# 정리 실행
# =====================================================

def clean_product_name(mall: str, name: str) -> str:
    """몰별 규칙 + 전 몰 공통 규칙을 적용해 상품명을 정리한다.

    같은 값을 두 번 넣어도 결과가 같다(멱등) — 수집 때와 변환 때 둘 다 불러도 안전하다.
    """
    if not name:
        return name
    cleaned = name
    for pat in MALL_PATTERNS.get(mall, []):
        if isinstance(pat, tuple):
            cleaned = re.sub(pat[0], pat[1], cleaned)
        else:
            cleaned = re.sub(pat, '', cleaned)
    for pat in GLOBAL_PATTERNS:
        cleaned = re.sub(pat, '', cleaned)
    # 시즌 토큰 — 전 몰 공통. 단독 단어만 지우므로 '정리된 이름에서 품번을 뽑는 몰'도 안전하다
    #   (예 loromoda '… / IGELONG-1C00006-20F' 은 하이픈으로 붙어 있어 안 걸린다).
    cleaned = strip_season_tokens(cleaned)   # 맨 끝 토큰(품번 꼬리·색상코드)은 보호된다
    # 중복 공백 정리
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


# =====================================================
# 품번(model_id) 정리
# =====================================================

# ★ 아래 drop_season_model_tokens 는 위 strip_season_tokens 와 목적이 다르다. 합치지 말 것.
#     strip_season_tokens      = 저장·전송되는 값을 고친다 → 보수적(하이픈으로 붙은 건 안 건드림)
#     drop_season_model_tokens = 그룹 판정용 비교 키를 만든다(dedup_corrector_merge.canonicalize)
#                                → 공격적(하이픈 접두 25FW- 도 뗀다). 같은 상품을 한 묶음으로 모으려면
#                                  비교할 때는 시즌을 무시하는 게 맞다. 이 값은 어디에도 저장되지 않는다.
#
# 시즌코드만 (25SS / SS25 / 26FW / 19SS).
# 연도 15-29 만 본다. 01FW·00AW·38AW·AW63 은 색상/사이즈 코드라 빼면 안 된다.
_NON_ALNUM = re.compile(r'[^A-Z0-9]+')
_SEASON_YEAR = r'(?:20)?(?:1[5-9]|2[0-9])(?:SS|FW|AW|SP)|(?:SS|FW|AW)(?:1[5-9]|2[0-9])'
_SEASON_RE = re.compile(r'^(?:' + _SEASON_YEAR + r')$')
_SEASON_HYPHEN_RE = re.compile(r'^(?:20)?(?:1[5-9]|2[0-9])(?:SS|FW|AW|SP)-', re.I)


def has_season_model_token(model_id: str) -> bool:
    """품번에 시즌 토큰(공백 분리 또는 하이픈 접두)이 있으면 True."""
    if not model_id:
        return False
    s = model_id.strip()
    if _SEASON_HYPHEN_RE.match(s):
        return True
    for p in s.split():
        t = _NON_ALNUM.sub('', p.strip().upper())
        if t and _SEASON_RE.match(t):
            return True
    return False


def drop_season_model_tokens(model_id: str) -> str:
    """품번에서 시즌 토큰만 뺀다. 남은 조각의 원문 표기는 유지한다.

    예) "25FW AA06F234X 08AD" → "AA06F234X 08AD"
        "25FW-PMPUPP01-541"   → "PMPUPP01-541"
    색상코드 "364665189C 01FW" 는 그대로 둔다.
    같은 값을 두 번 넣어도 결과가 같다(멱등).
    """
    if not model_id:
        return model_id
    s = _SEASON_HYPHEN_RE.sub('', model_id.strip())
    kept = []
    for p in s.split():
        if not p:
            continue
        t = _NON_ALNUM.sub('', p.strip().upper())
        if t and _SEASON_RE.match(t):
            continue
        kept.append(p)
    return ' '.join(kept)


def dedupe_model_id(model_id: str) -> str:
    """품번을 ' / ' 로 병기했는데 **같은 값을 두 번 쓴 것**이면 하나만 남긴다.

    오케이몰이 띄어쓰기만 다른 같은 품번을 병기한다(2026-08-13 실측 115건, 전부 okmall).
        "RVW00700920 D1P B999 / RVW00700920D1P B999"  → "RVW00700920D1P B999"
        "V1790VE410 / V1790VE 410"                    → "V1790VE 410"

    ★ 값이 실제로 다르면 손대지 않는다 — 슬래시 병기 1,419건 중 1,304건(92%)은
      "품번 / 몰 내부코드" 처럼 서로 다른 값이라, 뒤엣것만 남기면 진짜 품번이 사라진다.
        "44J290 / 1456690662"  → 그대로 둔다
      (그룹 판정용 canonicalize() 는 항상 뒤엣것을 쓰지만, 그건 비교용 키일 뿐
       저장·전송되는 값이 아니다. 여기서 같은 규칙을 쓰면 안 된다.)

    남길 조각은 **canonicalize 와 같게 마지막 것**으로 맞춘다. 실측상 다른 몰
    (lotte·labellusso)이 수집한 값과도 마지막 조각이 일치한다.

    같은 값을 두 번 넣어도 결과가 같다(멱등).
    """
    if not model_id or ' / ' not in model_id:
        return model_id
    parts = [p.strip() for p in model_id.split(' / ') if p.strip()]
    if len(parts) < 2:
        return model_id
    # 특수문자·공백을 뗀 형태가 전부 같을 때만 '중복 병기'로 본다
    if len({re.sub(r'[^A-Za-z0-9]', '', p).upper() for p in parts}) == 1:
        return parts[-1]
    return model_id


# =====================================================
# 스토어별 수집 제외 키워드
# =====================================================
# "이 스토어의 이런 상품은 아예 수집하지 않는다" — 향수·식품처럼 우리가 안 파는 분류,
# 흠집·중고처럼 정상품이 아닌 것. category_path 또는 상품명에 걸리면 수집 단계에서 건너뛴다.
# (2026-08-05: 수집기에 있던 것을 이 파일로 옮김. 규칙을 한 곳에 모으는 목적)
STORE_EXCLUDE_KEYWORDS = {
    'luxlimit': {'category': ['향수'], 'name': ['향수']},
    'shinsegae': {'category': ['식품'], 'name': []},
    # 흠집상품(하자품) 제외. 상품명 접두사 '[흠집상품]' + 전용 카테고리 'REFURB' 양쪽으로 차단.
    'stellastore': {'category': ['REFURB'], 'name': ['흠집']},
    # 스크래치(하자) 상품 제외.
    # 상품명이 '스크래치'로 시작하며 품번 앞에 'S'가 붙는다.
    'adonis':     {'category': [], 'name': ['스크래치', '스크레치']},
    # 중고/하자 상품 제외. 상품명 맨 앞 대괄호 태그로만 구분된다(카테고리는 일반 카테고리를 그대로 씀).
    #   실제 표기: [중고] [오염] [구성품불량] [오염,구성품불량] [미세하자] [미세하자2]
    #   → 부분일치라 아래 4개로 전부 걸린다. '불량'은 '구성품불량' 외 다른 조합도 흡수.
    'wardrobe':   {'category': [], 'name': ['중고', '오염', '하자', '불량']},
    # 미세하자 상품 제외 — '(미세하자)' / '(미세하자 당일)' 두 형태. 부분일치라 '하자' 하나면 충분.
    'milanosangin': {'category': [], 'name': ['하자']},
    'luxduck':    {'category': [], 'name': ['하자']},   # 미세하자 등
    'thesogno':   {'category': [], 'name': ['스크래치', '스크레치']},
    'gimpooutlet': {'category': [], 'name': ['스크래치', '스크레치']},
    # 중고 상품 제외 — 상품명 맨 앞 '[중고]' 태그로만 구분된다(카테고리는 일반 카테고리 그대로).
    #   대괄호까지 넣어 정확히 그 태그만 잡는다. 맨 앞 괄호를 지우는 정리 규칙을 luxboy 에
    #   두면 이 태그가 사라져 여기서 못 걸리니 주의(gimpooutlet 의 '스크래치' 사례와 같다).
    #   ★ '리퍼'는 쓰면 안 된다 — '슬리퍼'에 걸려 멀쩡한 상품이 통째로 빠진다(실측 2건).
    #   '스크래치'는 실측 6건 전부 '[중고]'와 같이 붙어 있어 이 태그 하나로 걸린다.
    'luxboy':     {'category': [], 'name': ['[중고]']},
}
