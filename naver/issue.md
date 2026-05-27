# 트렌드메카 convert 옵션 unique 충돌 이슈 — 정리

## 1. 발생 에러 로그 (사용자 제공)

```
[2026-05-19 15:47:52] [INFO] [787/7302] 변환 중: raw_id=1272406, brand=비비안웨스트우드...
[2026-05-19 15:47:52] [INFO]   → 자체 이미지 2장 저장 (source: kasina)
[2026-05-19 15:47:52] [ERROR]   → 변환 실패: (pymysql.err.IntegrityError) (1062, "Duplicate entry '185569-FREE-FREE' for key 'uk_ace_product_variant'")
[SQL: INSERT INTO ace_product_variants (ace_product_id, color_value, size_value, options_json, stock_type, stocks, source_option_code, source_stock_status) VALUES (...)]
[parameters: {'ace_product_id': 185569, 'color_value': 'FREE', 'size_value': 'FREE', 'options_json': '[{"type": "color", "value": "FREE"}, {"type": "size", "value": "FREE"}]', 'stock_type': 'purchase_for_order', 'stocks': 1, 'source_option_code': '57071829748', 'source_stock_status': 'in_stock'}]
```

- 같은 패턴의 IntegrityError가 [787]~[808] 구간에 연속 발생 (raw_id=1272406, 1272407, 1272408, 1272409, 1272458, 1272459, …)
- 마지막에 Traceback 발생 (수집 도중 중단)
- 로그 텍스트 "(source: kasina)" 는 컨버터 파일명(`raw_to_converter_kasina.py`) 기인 표기일 뿐, 실제 source_site는 **trendmecca**

## 2. raw_scraped_data id=1272406 실제 데이터 (사용자 제공)

```json
{
  "channel_no": "500017499",
  "brand_name": "비비안웨스트우드",
  "brand_id": 12253,
  "model_name": "1803002U Y001V A402",
  "origin": "",
  "material": "",
  "manufacturer": "비비안웨스트우드",
  "options": [
    {"color": "", "tag_size": "FREE", "option_code": "57071829747", "status": "in_stock"},
    {"color": "", "tag_size": "FREE", "option_code": "57071829748", "status": "in_stock"},
    {"color": "", "tag_size": "FREE", "option_code": "57071829749", "status": "in_stock"}
  ],
  "images": ["https://shop-phinf.pstatic.net/.../76064468202676674_702298201.jpg", "..."],
  "category": "패션의류 > 여성의류 > 니트 > 카디건",
  "scraped_at": "2026-05-18T16:33:38"
}
```

→ 옵션 3개가 모두 `color=''`, `tag_size='FREE'` 로 동일. converter가 normalize 시 (FREE, FREE)로 떨어져 같은 `(ace_product_id, color, size)` UNIQUE 키 위반.

## 3. 원본 네이버(brand.naver.com) JSON 응답 일부 (사용자 제공)

```json
"optionUsable": true,
"options": [
  {
    "id": 11503769094,
    "optionType": "COMBINATION",
    "groupName": "모델명"
  }
],
"optionCombinations": [
  { "id": 57071829747, "optionName1": "(XS)", "stockQuantity": 7,  "price": 0, "regOrder": 0, "registerDate": "2026-03-23T00:15:33.885+00:00" },
  { "id": 57071829748, "optionName1": "(S)",  "stockQuantity": 12, "price": 0, "regOrder": 1, "registerDate": "2026-03-23T00:15:33.885+00:00" },
  { "id": 57071829749, "optionName1": "(M)",  "stockQuantity": 10, "price": 0, "regOrder": 2, "registerDate": "2026-03-23T00:15:33.886+00:00" }
]
```

→ 옵션 그룹이 `groupName="모델명"` 단일이고, `optionCombinations`의 `optionName1` 이 실제로는 사이즈값 (`(XS)`, `(S)`, `(M)`).
→ 판매자가 "사이즈" 그룹을 "모델명"으로 잘못 등록한 케이스로 보임.

## 4. collector 옵션 파싱 로직 (원인 위치)

파일: `naver/premiumsneakers/premiumsneakers_collector.py` (라인 487~538)

```python
# 옵션 - groupName으로 optionName1/2가 색상인지 사이즈인지 판별
# product.options: [{optionType, groupName: '색상'|'사이즈'|...}, ...]
# product.optionCombinations: [{id, optionName1, optionName2?, stockQuantity, ...}, ...]
opt_groups = product.get('options') or []
group_types = []  # ['color', 'size', 'skip'] — skip은 모델명 등 실사용 안 하는 옵션
for g in opt_groups:
    gname = (g.get('groupName') or '').strip()
    gname_up = gname.upper()
    if '색상' in gname or '컬러' in gname or 'COLOR' in gname_up:
        group_types.append('color')
    elif '모델' in gname or 'MODEL' in gname_up or '품번' in gname or '스타일' in gname:
        group_types.append('skip')
    else:
        # '사이즈', '신발사이즈' 등은 모두 size로 처리
        group_types.append('size')

def _normalize_size(s: str) -> str:
    s = (s or '').strip()
    if s.upper() in {'ONE SIZE', 'ONESIZE', '단일사이즈', '단일 사이즈', '단일', '원사이즈', '원 사이즈', 'UNI', 'FREE'}:
        return 'FREE'
    return s

combos = product.get('optionCombinations') or []
options = []
for i, c in enumerate(combos):
    n1 = (c.get('optionName1') or '').strip()
    n2 = (c.get('optionName2') or '').strip()
    names = [n for n in [n1, n2] if n]

    color_val = ''
    size_val = ''
    for idx, name in enumerate(names):
        gtype = group_types[idx] if idx < len(group_types) else 'size'
        if gtype == 'color':
            color_val = name
        elif gtype == 'skip':
            continue
        else:
            size_val = _normalize_size(name)

    if not size_val and not color_val:
        size_val = 'FREE'
    if not size_val:
        size_val = 'FREE'

    stock = int(c.get('stockQuantity') or 0)
    options.append({
        'color': color_val or '',
        'tag_size': size_val,
        'option_code': str(c.get('id', i)),
        'status': 'in_stock' if stock > 0 else 'out_of_stock',
    })
```

**실행 흐름** (이번 케이스):
1. `opt_groups = [{groupName: "모델명", ...}]`
2. "모델" in "모델명" → `group_types = ['skip']`
3. combos 3개 순회 → optionName1="(XS)"/"(S)"/"(M)" 모두 `skip` 처리되어 size/color 어디에도 안 들어감
4. 라인 527-528: `size_val=''`, `color_val=''` → `size_val='FREE'`
5. 옵션 3개 모두 `(color='', size='FREE')` 동일 → converter에서 unique key 충돌

## 5. git 이력

```
acee5f4 (2026-04-24) naver 수집기 추가: premiumsneakers collector + 10개 스마트스토어 ...
7563366 naver: smartstore Referer 우회 + skip-existing 의미 통일 + 신규 mall 10개 추가
```

→ `acee5f4` 최초 commit부터 "모델명 → skip" 로직이 있었음. 별도 수정 이력 없음.

## 6. 현재 로직 판정 방식 (정리)

- 그룹마다 **독립적으로** 판정 (다른 그룹 존재 여부 보지 않음)
- "모델명" 단어가 단독 그룹이어도 무조건 skip
- 즉 옵션 그룹이 `["모델명"]` 단독으로만 있는 trendmecca 같은 케이스 → 모든 옵션값이 버려지고 FREE로 떨어짐

## 7. "모델명 → skip" 의도 추정

**의도가 합리적인 케이스** (정상 데이터):
- 옵션 그룹이 `["색상", "사이즈", "모델명"]` 처럼 여러 개 있을 때
- "모델명"은 사이즈/색상과 별개의 SKU 식별자/디자이너 코드 마케팅 라벨 → buyma 색·사이즈와 무관
- skip 안 하면 같은 (색, 사이즈) 조합에 모델명 차이로 variant 폭증

**문제 케이스** (이번 trendmecca):
- 옵션 그룹이 `["모델명"]` 단독
- 실제 값은 사이즈
- 판매자가 그룹 라벨을 잘못 등록

→ skip 자체는 유지하되 "다른 그룹과 함께 있을 때만 skip" 으로 조건 강화하면 의도 보존 + 이번 케이스 해결.

## 8. 해결 방향 (사용자 결정 대기)

| 안 | 처리 위치 | 동작 | 비고 |
|---|---|---|---|
| A | collector | "모델명"이 다른 그룹과 함께 있을 때만 skip. 단독 그룹이면 size로 인식 | 원래 의도 보존, 이번 케이스 해결 |
| A' | collector | "모델명" 단독이고 옵션 값이 사이즈 패턴(XS/S/M, 숫자, ONE SIZE 등)이면 size로 처리 | 더 보수적이지만 휴리스틱 오판 가능 |
| B | collector | "모델명" 단독 그룹이면 optionName1을 그대로 size 값에 저장 ("(XS)" 형태) | 안전하지만 형식 어색 |
| C | converter | (color, size) 중복 옵션은 1개만 INSERT, 나머지 skip | 옵션 정보 손실, 변환 자체는 성공 |

## 9. 미진행 항목

- 다른 mall raw 데이터에서 동일 패턴 (옵션 그룹이 "모델명"/"품번"/"스타일" 만 단독으로 있는 케이스) 빈도 조사 → 사용자 결정 후
- 어느 안으로 수정할지 사용자 결정 대기
- 이번 에러로 변환 실패한 raw 건 수 / 영향 ace_products 정리는 안 가는 별개 작업

## 10. 관련 사실/메모

- trendmecca는 brand.naver.com 으로 전환 진행 중인 mall (네이버 brand store)
- collector 진입점: `naver/premiumsneakers/premiumsneakers_collector.py` (브랜드 스토어용 `fetch_detail_brand_store` 경로)
- 컨버터: `kasina/raw_to_converter_kasina.py` 공용 (`--source-site` 플래그)
- DB unique key: `ace_product_variants(ace_product_id, color_value, size_value)` = `uk_ace_product_variant`
- raw `tag_size='FREE'` 는 컨버터/normalize에서 size_value='FREE'로 그대로 들어감
