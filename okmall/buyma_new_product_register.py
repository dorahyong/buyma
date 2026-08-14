# -*- coding: utf-8 -*-
"""
바이마 상품 API 부품 모음 (라이브러리 — 직접 실행하지 않는다)

이 파일은 스스로 등록을 돌리지 않는다. 등록·수정·출품정지는 전부
reconcile(okmall/reconcile_runner.py → reconcile_buyma_push.py)이 지휘하고,
여기서는 "요청서를 어떻게 만들고 어떻게 보내는가"만 제공한다.

밖에서 쓰는 것 (import buyma_new_product_register as reg):
    reg.build_request_json           상품 API 요청서 (CREATE·EDIT 공용, control=publish 상수)
    reg.build_variants_array         옵션(변이) 배열
    reg.get_product_images           이미지 목록 (뱃지 썸네일 우선)
    reg.call_buyma_api               상품 API 전송
    reg.call_buyma_variants_soldout  재고 API 전송 (전 옵션 품절 → 출품정지중)
    reg.API_BASE_URL / reg.BUYMA_MODE / reg.MAX_SHOP_URLS

  ※ 이 모듈을 import 하면 win32 stdout/stderr 을 utf-8 로 감싸는 부수효과가 있다.
    재고동기화 _merge 들이 이 부수효과에 의존하므로 옮기거나 지우지 말 것.

없앤 것 (2026-08-04):
  - 자기 실행부(main·--clean-duplicates·--clean-no-model·단건 등록 흐름) — reconcile 로 대체됨
  - 삭제 API(control=delete) 경로 전부 — 하차는 재고 API(출품정지)만 쓰기로 확정
  - ace_products 를 직접 훑어 등록 대상을 고르던 조회들 — 정체성은 buyma_listings 가 권위

작성일: 2026-02-11 (2026-08-04 부품만 남기고 정리)
"""

import os
import sys
import json
import random
from datetime import datetime, timedelta
from typing import Dict, List

import requests
import re
from dotenv import load_dotenv

import unicodedata

def truncate_buyma_name(text, max_limit=60):
    """
    Buyma 상품명 제한(반각 60자/전각 30자)에 맞춰 문자열을 자르는 함수
    - 전각(한글, 한자, 일본어 등): 2로 계산
    - 반각(영어, 숫자, 기호): 1로 계산
    """
    if not text:
        return ""
    
    current_length = 0
    result = ""
    
    for char in text:
        # 문자의 폭(width) 확인
        # 'F'(Fullwidth), 'W'(Wide), 'A'(Ambiguous)는 전각(2)으로 취급
        eaw = unicodedata.east_asian_width(char)
        if eaw in ('F', 'W', 'A'):
            char_width = 2
        else:
            char_width = 1
            
        # 제한 길이를 초과하면 중단
        if current_length + char_width > max_limit:
            break
            
        result += char
        current_length += char_width

    # 자른 자리가 공백이면 BUYMA 가 저장할 때 지운다 → 우리가 보낸 값과 BUYMA 값이 달라진다.
    #   name 은 게시 후 편집 불가라 값이 다르면 이후 수정 요청이 거부될 수 있으므로 여기서 다듬는다.
    #   (2026-08-04 실측: 끝 공백 때문에 4,217건이 어긋나 있었다)
    return result.rstrip()

def truncate_option_value(text, max_limit=26):
    """
    バイマ옵션명 제한(반각 26자/전각 13자)에 맞춰 자르는 함수
    1. + 구분자가 있으면 → 첫 번째 색상 + ' 外N色'
    2. 그래도 초과면 → '...' 포함하여 max_limit 이내로 truncate
    """
    if not text:
        return ""

    def buyma_width(s):
        w = 0
        for c in s:
            eaw = unicodedata.east_asian_width(c)
            w += 2 if eaw in ('F', 'W', 'A') else 1
        return w

    # max_limit 이내면 그대로
    if buyma_width(text) <= max_limit:
        return text

    # 1. + 구분자가 있으면 첫 번째 색상 + 外N色
    if '+' in text:
        parts = [p.strip() for p in text.split('+') if p.strip()]
        if len(parts) > 1:
            first = parts[0]
            suffix = f' 外{len(parts) - 1}色'
            combined = first + suffix
            if buyma_width(combined) <= max_limit:
                return combined

    # 2. truncate with ...
    result = ""
    current_length = 0
    dots = "..."
    dots_width = 3  # 반각 3자
    limit = max_limit - dots_width

    for char in text:
        eaw = unicodedata.east_asian_width(char)
        char_width = 2 if eaw in ('F', 'W', 'A') else 1
        if current_length + char_width > limit:
            break
        result += char
        current_length += char_width

    return result + dots


def _buyma_width(s: str) -> int:
    """바이마 반각 환산 길이 계산 (전각=2, 반각=1)"""
    w = 0
    for c in s:
        eaw = unicodedata.east_asian_width(c)
        w += 2 if eaw in ('F', 'W', 'A') else 1
    return w


def truncate_buying_shop_name(shop_name: str, max_limit: int = 30) -> str:
    """buying_shop_name 반각 30자 제한 처리
    1단계: 원본 그대로 (brand正規販売店)
    2단계: 正規販売店 → 正規店 으로 축약
    3단계: 'BRAND 正規販売店' 고정값
    """
    if not shop_name:
        return ""
    # 1단계: 원본
    if _buyma_width(shop_name) <= max_limit:
        return shop_name
    # 2단계: 正規販売店 → 正規店
    if shop_name.endswith('正規販売店'):
        short = shop_name.replace('正規販売店', '正規店')
        if _buyma_width(short) <= max_limit:
            return short
    # 3단계: 고정값
    return 'BRAND 正規販売店'


# 표준 출력 인코딩 설정 (윈도우 환경 대응)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)


# .env 파일 로드
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

# =====================================================
# 설정값
# =====================================================

# 바이마 API 설정
BUYMA_MODE = int(os.getenv('BUYMA_MODE', 1))  # 1: 본환경, 2: 샌드박스
BUYMA_API_BASE_URL = os.getenv('BUYMA_API_BASE_URL', 'https://personal-shopper-api.buyma.com/')
BUYMA_SANDBOX_URL = os.getenv('BUYMA_SANDBOX_URL', 'https://sandbox.personal-shopper-api.buyma.com/')
BUYMA_ACCESS_TOKEN = os.getenv('BUYMA_ACCESS_TOKEN', '')

# 환경에 따른 API URL 선택
API_BASE_URL = BUYMA_API_BASE_URL if BUYMA_MODE == 1 else BUYMA_SANDBOX_URL

# 바이마 API 고정값
BUYMA_FIXED_VALUES = {
    'buying_area_id': '2002003000',       # 구매 지역 ID (한국)
    'shipping_area_id': '2002003000',     # 발송 지역 ID (한국)
    'theme_id': 98,                       # 테마 ID
    'duty': 'included',                   # 관세 포함
    'shipping_methods': [1063035],        # 배송 방법 ID
}

# shop_urls(買付先) 최대 칸수. 초과하면 BUYMA 가 422 로 거부한다:
#   {"errors":{"shop_urls":["買付先は15件以内で入力してください。"]}}  (2026-07-22 실측)
MAX_SHOP_URLS = 15

# 품번(style_numbers) 한 칸의 최대 글자수. 초과하면 BUYMA 가 거부한다:
#   {"errors":{"style_numbers":{"0":{"number":["品番は40文字以内で入力してください。"]}}}}
MODEL_NO_MAX = 40

# =====================================================
# 유틸리티 함수
# =====================================================

def log(message: str, level: str = "INFO") -> None:
    """로그 출력"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


# =====================================================
# 마진 계산 함수
# =====================================================


# =====================================================
# 데이터 조회 함수
# =====================================================


def get_product_images(conn, ace_product_id: int) -> List[Dict]:
    """상품 이미지 조회.
    ★ 대표이미지(position=1)에 뱃지 썸네일이 있으면(ace_product_thumbnails.is_generated=1)
      뱃지본을 '맨 앞에 추가'하고 원본 1번은 그대로 뒤에 남긴다.
      → 최종 순서: [뱃지본] → [원본1] → [원본2] → …  (뱃지본 + 원본 전부 업로드)
      없으면 원본만 그대로. position 은 build_images_array 가 1부터 다시 매기므로 여기선 순서만 중요.
      BUYMA 이미지 상한 20 → 뱃지 추가로 21이 되면 마지막 1장을 잘라 20으로 맞춘다."""
    with conn.cursor() as cursor:
        sql = """
            SELECT api.position,
                   api.cloudflare_image_url AS cloudflare_image_url,
                   CASE WHEN api.position = 1
                             AND t.thumbnail_cloudflare_url IS NOT NULL
                             AND t.thumbnail_cloudflare_url <> ''
                        THEN t.thumbnail_cloudflare_url
                        ELSE NULL
                   END AS badge_url
            FROM ace_product_images api
            LEFT JOIN ace_product_thumbnails t
                   ON t.image_id = api.id AND t.is_generated = 1
            WHERE api.ace_product_id = %s
              AND api.cloudflare_image_url IS NOT NULL
            ORDER BY api.position
            LIMIT 20
        """
        cursor.execute(sql, (ace_product_id,))
        rows = cursor.fetchall()

    out: List[Dict] = []
    for r in rows:
        if r['position'] == 1 and r.get('badge_url'):
            # 뱃지본을 맨 앞에, 이어서 원본 1번도 유지
            out.append({'position': 0, 'cloudflare_image_url': r['badge_url']})
        out.append({'position': r['position'], 'cloudflare_image_url': r['cloudflare_image_url']})
    return out[:20]  # BUYMA 이미지 상한


# =====================================================
# API 요청 데이터 구성
# =====================================================

def build_images_array(image_rows: List[Dict]) -> List[Dict]:
    """images 배열 구성 — position을 1부터 빈칸 없이 연속 재부여.
    (이미지 업로드 실패로 원본 position에 구멍이 생기면 BUYMA가 422
     '表示位置番号は歯抜けができないように…' 로 거부함. image_rows는
     position 정렬돼 있으므로 순서 유지한 채 1,2,3…으로 다시 매김)"""
    return [
        {"path": row['cloudflare_image_url'], "position": idx}
        for idx, row in enumerate(image_rows, start=1)
    ]


# 카테고리별 허용 size_details 키 캐시
_category_size_keys_cache: Dict[int, List[str]] = {}


def load_category_size_keys() -> Dict[int, List[str]]:
    """
    BUYMA 마스터 데이터 size_details.csv에서 카테고리별 허용 키 로드
    Returns: {category_id: [허용 키 리스트]}
    """
    global _category_size_keys_cache
    if _category_size_keys_cache:
        return _category_size_keys_cache

    import csv
    csv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'buyma_master_data', 'size_details.csv')
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                cat_id = row.get('category_id', '').strip()
                key_name = row.get('name', '').strip()
                if cat_id and cat_id.isdigit() and key_name:
                    cat_id = int(cat_id)
                    if cat_id not in _category_size_keys_cache:
                        _category_size_keys_cache[cat_id] = []
                    if key_name not in _category_size_keys_cache[cat_id]:
                        _category_size_keys_cache[cat_id].append(key_name)
        log(f"카테고리별 size_details 키 매핑 {len(_category_size_keys_cache)}개 카테고리 로드")
    except Exception as e:
        log(f"size_details.csv 로드 실패: {e}", "WARNING")

    return _category_size_keys_cache


def filter_details_by_category(details: List[Dict], category_id: int) -> List[Dict]:
    """category_id에 허용된 키만 남기고 나머지 제거"""
    keys_map = load_category_size_keys()
    allowed_keys = keys_map.get(category_id)
    if not allowed_keys:
        return []  # 허용 키가 없으면 details 전체 제거
    filtered = [d for d in details if d.get('key') in allowed_keys]
    if len(filtered) != len(details):
        removed = [d['key'] for d in details if d.get('key') not in allowed_keys]
        log(f"  - size_details 필터링: category_id={category_id}, 제거된 키={removed}")
    return filtered


def build_options_array(option_rows: List[Dict], valid_sizes: set = None, valid_colors: set = None, category_id: int = 0) -> List[Dict]:
    """
    options 배열 구성 (size인 경우 details 포함)

    ★ valid_sizes, valid_colors가 주어지면 해당 값만 포함 (variants와 일치)
    ★ category_id 기준으로 허용되지 않는 size_details 키 제거
    """
    options = []
    for row in option_rows:
        # ★ variants에 있는 것만 포함 (필터링)
        #   variants 쪽 값은 이미 26반각으로 잘린 상태다. 여기서 원본으로 대조하면 26반각을
        #   넘는 옵션이 "변이에 없는 값"으로 오인돼 options 에서만 빠지고, BUYMA 가
        #   "변이가 가리키는 선택지가 없다"며 요청 전체를 거부한다. → 같은 기준(자른 값)으로 대조.
        _val = truncate_option_value(row['value'])
        if valid_sizes is not None and row['option_type'] == 'size':
            if _val not in valid_sizes:
                continue
        if valid_colors is not None and row['option_type'] == 'color':
            if _val not in valid_colors:
                continue

        option = {
            "type": row['option_type'],
            "value": _val,
            "position": row['position'],
            "master_id": row['master_id'] or 0
        }

        # size 옵션이고 details_json이 있으면 details 추가
        if row['option_type'] == 'size' and row.get('details_json'):
            try:
                details = json.loads(row['details_json'])
                if details:
                    # ★ category_id 기준 허용 키 필터링
                    if category_id:
                        details = filter_details_by_category(details, category_id)
                    if details:  # 필터링 후에도 남아있으면 추가
                        option['details'] = details
            except (json.JSONDecodeError, TypeError):
                pass  # 파싱 실패 시 무시

        options.append(option)
    return options


def build_variants_array(variant_rows: List[Dict]) -> List[Dict]:
    """
    ★★★ variants 배열 구성 ★★★
    
    out_of_stock도 포함하여 options와 일치시킴
    """
    variants = []
    for row in variant_rows:
        # 오케이몰 재고 상태 확인
        is_in_stock = row['stock_type'] != 'out_of_stock' and (row['stocks'] is None or row['stocks'] > 0)
        
        variant = {
            "options": [],
            "stock_type": "purchase_for_order" if is_in_stock else "out_of_stock"
        }
        # purchase_for_order일 때는 개별 stocks를 보내면 에러가 나므로 제외합니다. (바이마 API 필수 규칙)
        
        if row['color_value']:
            variant["options"].append({"type": "color", "value": truncate_option_value(row['color_value'])})
        if row['size_value']:
            variant["options"].append({"type": "size", "value": truncate_option_value(row['size_value'])})
        variants.append(variant)
    return variants

def generate_model_no_variants(model_no: str) -> List[str]:
    """
    모델명을 여러 형태로 생성하여 리스트로 반환
    예: "WVBDK M25085 AAD" → ["WVBDK M25085 AAD", "WVBDKM25085AAD"]
    """
    if not model_no:
        return []

    model_no = re.sub(r'\s*\([^)]*\)', '', model_no).strip()
    variants = [model_no]  # 1. 원본

    # 2. 특수문자를 공백으로 바꾼 버전 (하이픈, 언더스코어 등)
    space_replaced = re.sub(r'[-_/\\.,]+', ' ', model_no)
    if space_replaced != model_no and space_replaced not in variants:
        variants.append(space_replaced)

    # 3. 모든 특수문자와 공백을 제거한 버전
    no_special = re.sub(r'[^A-Za-z0-9]', '', model_no)
    if no_special and no_special not in variants:
        variants.append(no_special)

    # 4. 40자 상한 (BUYMA: "品番は40文字以内で入力してください")
    #    넘는 값은 세트상품('쇼트팬츠 4종' 처럼 품번이 여럿)이거나 수집처가 품번칸에
    #    색상·상품명을 넣어준 것이다. 값 자체는 ace/목록에 그대로 두고 **보낼 때만** 자른다.
    #    → ace 에서 자르면 그룹 판정(canonicalize) 기준이 바뀌어 병합이 깨진다.
    #    자른 뒤 서로 같아지는 것이 생기므로 중복 제거하고 순서는 유지한다.
    out = []
    for v in variants:
        v = v[:MODEL_NO_MAX].strip()
        if v and v not in out:
            out.append(v)
    return out  # 리스트 반환


def build_request_json(product: Dict, images: List[Dict], options: List[Dict], variants: List[Dict]) -> Dict:
    """바이마 API 요청 JSON 구성 (신규 등록 전용)"""

    # 0. 전체 품절 여부 확인 (미등록 상품은 delete 불가 → 스킵)
    all_out_of_stock = all(v['stock_type'] == 'out_of_stock' for v in variants)

    if all_out_of_stock:
        log(f"  ★ 모든 재고 품절 → 신규 등록 스킵 (재고 데이터 유지)", "WARN")
        return None

    # 1. 모델명 변형 생성
    model_no_list = generate_model_no_variants(product.get('model_no', ''))

    # 1-1. Comments용 텍스트 변환 (기존 로직 유지)
    model_no_text = '\n'.join(model_no_list)

    # 1-2. style_numbers 배열 생성 (신규 추가 요청사항)
    # [{"number": "...", "memo": ""}, ...] 형식
    style_numbers = [{"number": num, "memo": ""} for num in model_no_list]

    # 2. 고정 공지사항 (comments) - 한국어 완벽 제거
    fixed_comments = """☆☆☆ ご購入前にご確認ください ☆☆☆

◆商品は直営店をはじめ、 デパート、 公式オンラインショップ、ショッピングモールなどの正規品を取り扱う店舗にて買い付けております。100％正規品ですのでご安心ください。

◆「あんしんプラス」へご加入の場合、「サイズがあわない」「イメージと違う」場合に「返品補償制度」をご利用頂けます。
※「返品対象商品」に限ります。詳しくは右記URLをご参照ください。https://qa.buyma.com/trouble/5206.html

◆ご注文～お届けまで
手元在庫有：【ご注文確定】 →【梱包】 → 【発送】 → 【お届け】
手元在庫無し：【ご注文確定】 →【買付】 →【検品】 →【梱包】 →【発送】→【お届け】

◆配送方法/日数
通常国際便（SAGAWA）：【商品準備2-5日 】+ 【発送～お届け5-9日】
※平常時の目安です。繁忙期/非常時はお届け日が前後する場合もございます。詳しくはお問合せください。
※当店では検品時に不良/不具合がある場合は良品に交換をしてお送りしております。当理由でお時間を頂戴する場合は都度ご報告させて頂いております。

◆「お荷物追跡番号あり」にて配送しますので、随時、配送状況をご確認いただけます。
◆土・日・祝日は発送は休務のため、休み明けに順次発送となります。

◆海外製品は「MADE IN JAPAN」の製品に比べて、若干見劣りする場合もございます。
返品・交換にあたる不具合の条件に関しては「お取引について」をご確認ください。

◆当店では、日本完売品、日本未入荷アイテム、限定品、
メンズ、レディース、キッズの シューズ（スニーカー等）や衣類をメインに取り扱っております。
(カップル,ファミリー、ペアルック、親子リンク)
韓国の最新トレンドや新作アイテムを順次出品しており、

◆交換・返品・キャンセル
返品と交換に関する規定は、バイマ規定によりお客様の理由による返品はお受けいたしかねますので、ご購入には慎重にお願いいたします。
不良品・誤配送は交換、または返品が可能です。
モニター環境による色違い、サイズ測定方法による1~3cm程度の誤差、糸くず、糸の始末などは欠陥でみなされません。
製品の大きさは測定方法によって1~3cm程度の誤差が生じることがありますが、欠陥ではございません。

◆不良品について
検品は行っておりますが、海外製品は日本商品よりも検品基準が低いです。
下記の理由は返品や交換の原因にはなりません。
- 縫製の粗さ
- 縫い終わり部分の糸が切れていないで残っている
- 生地の色ムラ
- ミリ段位の傷
- 若干の汚れ、シミ
- 製造過程での接着剤の付着など"""

    # 2. 색상/사이즈 보충 정보 푸터 섹션별 분리 (뒤 섹션부터 제거)
    colorsize_footer_sections = [
        """

★最安値に挑戦中！★
本商品は、私たちKONNECT（コネクト）が
お客様に少しでもお安く提供できるよう、
最安値での出品に努めた商品です。
出品時の市場価格調査はもちろん、
定期的にも価格チェックを行っております。
（※ただし、価格はリアルタイムで変動するため、
タイミングによっては最安値ではなくなる場合もございます。
あらかじめご了承ください。）""",
        """

★追加料金は一切なし！★
BUYMAでの決済金額以外、追加費用は一切かかりませんのでご安心ください。
関税・消費税・送料はすべて商品価格に含まれております。お客様が追加で支払う必要はございません。""",
        """

★安心の追跡付き発送★
KONNECT（コネクト）では、すべて追跡可能な配送方法でお届けいたします。
商品発送後、1〜2日ほどでBUYMA上にて追跡番号をご確認いただけます""",
        """

★ご購入前の在庫確認のお願い★
在庫状況はリアルタイムではなく、人気の商品は注文時す
でに《欠品》となっている可能性もございます。
確実でスピーディーなお取引と、注文確定後のキャンセル
によるお客様のご負担をなくすため、ご注文手続きの前に
【在庫確認】のご協力をお願いしております。
ご検討されている方も、お気軽にお問い合わせ欄からお声
掛け下さいませ。""",
        """

※ 上記参考価格は現地参考価格を10KRW ＝ 1.1円で換算したものです
※仕入れはデパートや公式オンラインショップなど、100％正規品のみ扱っております"""
    ]

    # available_until: 항상 현재시각 + 90일
    available_until_str = (datetime.now() + timedelta(days=90)).strftime('%Y/%m/%d')

    # 배송 방법 배열 구성 (객체 배열 형식으로 복구)
    shipping_methods = [
        {"shipping_method_id": sm_id} for sm_id in BUYMA_FIXED_VALUES['shipping_methods']
    ]

    # ★ 신규 등록: 현재 값 사용 (locked_* 체크 불필요)
    api_name = product['name']
    api_brand_id = product['brand_id']
    api_category_id = product['category_id']
    api_reference_number = product['reference_number']

    # ★ variants에서 유효한 size/color 추출 (options 필터링용)
    valid_sizes = set()
    valid_colors = set()
    for v in variants:
        for opt in v.get('options', []):
            if opt['type'] == 'size':
                valid_sizes.add(opt['value'])
            elif opt['type'] == 'color':
                valid_colors.add(opt['value'])

    # ★ options 필터링: variants에 있는 size/color만 포함 + category_id별 size_details 키 필터링
    filtered_options = build_options_array(options, valid_sizes, valid_colors, category_id=int(api_category_id))

    request_data = {
        # 필수 필드
        "control": "publish",
        "name": truncate_buyma_name(api_name),
        "comments": f"{api_name}\n{model_no_text}\n\n{fixed_comments}" if model_no_text else f"{api_name}\n\n{fixed_comments}",
        "brand_id": int(api_brand_id) if api_brand_id else 0,
        "category_id": int(api_category_id),
        "price": int(product['price']),
        "available_until": available_until_str,
        "buying_area_id": BUYMA_FIXED_VALUES['buying_area_id'],
        "shipping_area_id": BUYMA_FIXED_VALUES['shipping_area_id'],
        "shipping_methods": shipping_methods,
        "images": build_images_array(images),
        "options": filtered_options,  # ★ 필터링된 options
        "variants": variants,
        "order_quantity": random.randint(90, 100), # purchase_for_order 사용 시 필수 항목
        # 선택 필드
        "reference_number": api_reference_number,
        "theme_id": BUYMA_FIXED_VALUES['theme_id'],
        "duty": BUYMA_FIXED_VALUES['duty'],
    }

    # brand_id=0인 경우 (바이마 미등록 브랜드) brand_name 추가, style_numbers 제외
    if not api_brand_id or api_brand_id == 0:
        if product.get('brand_name'):
            request_data['brand_name'] = product['brand_name']
            log(f"  - 미등록 브랜드: brand_id=0, brand_name='{product['brand_name']}'")
    else:
        request_data['style_numbers'] = style_numbers

    # 선택 필드 추가 (값이 있는 경우만)
    if product.get('buying_shop_name'):
        request_data['buying_shop_name'] = truncate_buying_shop_name(product['buying_shop_name'])

    if product.get('original_price_jpy'):
        ref_price = int(product['original_price_jpy'])
        if ref_price > request_data.get('price', 0):
            request_data['reference_price'] = ref_price

    if product.get('buyma_model_id'):
        request_data['model_id'] = product['buyma_model_id']

    # shop_urls: 호출부가 소싱처 목록(product['shop_urls'])을 넘기면 그대로 쓴다.
    #   병합 상품은 매입처가 여럿이라 winner 1개만 보내면 나머지가 사라진다.
    #   목록이 없으면 종전대로 winner 주소 1칸(기존 동작 유지).
    if product.get('shop_urls'):
        request_data['shop_urls'] = product['shop_urls'][:MAX_SHOP_URLS]
    elif product.get('source_product_url'):
        request_data['shop_urls'] = [{
            "url": product['source_product_url'],
            "label": product.get('source_site', ''),
            "description": ""
        }]

    # colorsize_comments 글자수 제한 처리 (1000자)
    COLORSIZE_LIMIT = 1000
    base_colorsize = product.get('colorsize_comments_jp') or ""

    # 앞에서부터 섹션 누적 길이 계산하여 끝 인덱스 결정 (뒤 섹션부터 제거)
    remaining = COLORSIZE_LIMIT - len(base_colorsize)
    end_idx = 0  # 기본값: 아무것도 안 붙임

    cumulative_len = 0
    for i in range(len(colorsize_footer_sections)):
        section_len = len(colorsize_footer_sections[i])
        if cumulative_len + section_len <= remaining:
            cumulative_len += section_len
            end_idx = i + 1
        else:
            break

    # 선택된 섹션들만 합쳐서 footer 생성
    colorsize_footer = ''.join(colorsize_footer_sections[:end_idx])
    request_data['colorsize_comments'] = base_colorsize + colorsize_footer

    # 최상위를 'product' 키로 감싸서 반환 (바이마 API 필수 규격)
    return {"product": request_data}


# =====================================================
# 바이마 API 호출
# =====================================================

def call_buyma_api(request_data: Dict) -> Dict:
    """
    바이마 상품 등록 API 호출
    """
    url = f"{API_BASE_URL}api/v1/products"

    headers = {
        "Content-Type": "application/json",
        "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=request_data,
            timeout=30
        )

        log(f"API 응답 코드: {response.status_code}")

        if response.status_code in [200, 201, 202]:
            return {
                "success": True,
                "status_code": response.status_code,
                "response": response.json() if response.text else {},
                "headers": dict(response.headers)
            }
        else:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text,
                "headers": dict(response.headers)
            }

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timeout"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


def call_buyma_variants_soldout(reference_number: str, option_rows: list) -> Dict:
    """재고 API(variants.json)로 전 옵션 품절(out_of_stock) → '출품정지중'(삭제 아님).
    삭제(control=delete) 대신 사용 → buyma_product_id 유지, 재입고 시 같은 상품으로 출품중 복구.
    option_rows: [{'color_value':..., 'size_value':...}, ...] (listing_options 옵션들).
    ★ order_quantity=0 필수: 전 변이가 out_of_stock 되면 買付可 변이가 0이라
       기존 order_quantity 가 충돌(거부)하므로 0 으로 클리어해야 통과.
    실제 is_published=0 반영은 buyer_suspended webhook 이 담당(server.py).
    """
    url = f"{API_BASE_URL}api/v1/products/variants.json"
    headers = {
        "Content-Type": "application/json",
        "X-Buyma-Personal-Shopper-Api-Access-Token": BUYMA_ACCESS_TOKEN
    }
    variants = []
    for o in option_rows:
        opts = []
        if o.get('color_value'):
            opts.append({"type": "color", "value": o['color_value']})
        if o.get('size_value'):
            opts.append({"type": "size", "value": o['size_value']})
        variants.append({"options": opts, "stock_type": "out_of_stock"})
    request_data = {
        "product": {
            "reference_number": reference_number,
            "variants": variants,
            "order_quantity": 0,
        }
    }
    try:
        response = requests.post(url, headers=headers, json=request_data, timeout=30)
        log(f"  품절(재고API) 응답 코드: {response.status_code}")
        if response.status_code in [200, 201, 202]:
            return {
                "success": True,
                "status_code": response.status_code,
                "response": response.json() if response.text else {},
            }
        else:
            return {
                "success": False,
                "status_code": response.status_code,
                "error": response.text,
            }
    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timeout"}
    except requests.exceptions.RequestException as e:
        return {"success": False, "error": str(e)}


# =====================================================
# 중복 model_no 및 model_no 없는 상품 조회/삭제
# =====================================================


# =====================================================
# DB 업데이트
# =====================================================


# =====================================================
# 메인 로직
# =====================================================

