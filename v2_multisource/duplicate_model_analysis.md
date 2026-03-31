# 멀티소스 중복 model_id 단일화 (dedup) 파이프라인

> 최종 분석 기준일: 2026-03-27
> 대상 수집처: okmall, kasina, nextzennpack, labellusso

---

## 1. 수집처별 기본 현황

| 수집처 | RAW 건수 | 고유 model_id | 바이마 등록 모델 |
|--------|--------:|------------:|--------------:|
| okmall | 46,547 | 45,242 | 16,011 |
| kasina | 16,991 | 16,252 | 9,415 |
| labellusso | 9,321 | 8,929 | 0 |
| nextzennpack | 2,433 | 2,423 | 255 |

> "바이마 등록 모델" = `ace_products`에서 `buyma_product_id IS NOT NULL` 기준
> "고유 model_id" = `raw_scraped_data`에서 `model_id IS NOT NULL AND model_id != ''` DISTINCT 기준

---

## 2. 4개 수집처 전체 교차 비교

### 2-1. 쌍별 정확 일치 + 유사 일치

| 조합 | 정확 일치 | 유사 일치 (추가) | 합계 |
|------|--------:|---------------:|-----:|
| **okmall vs labellusso** | **922** | **186** | **1,108** |
| **okmall vs nextzennpack** | **522** | **34** | **556** |
| **nextzennpack vs labellusso** | **162** | **38** | **200** |
| okmall vs kasina | 63 | 65 | 128 |
| kasina vs labellusso | 2 | 6 | 8 |
| kasina vs nextzennpack | 0 | 1 | 1 |

> kasina는 스포츠/스트릿웨어 중심 → 럭셔리 중심인 다른 3곳과 거의 안 겹침
> okmall-labellusso가 가장 중복이 크고, nextzennpack-labellusso도 162개로 상당

### 2-2. 3개 이상 수집처에 동시 존재하는 model_id

| 조합 | 건수 |
|------|-----:|
| okmall + nextzennpack + labellusso 3곳 동시 | **79개** |
| 4곳 전부 | 0개 |

> 3곳 동시 중복 79개는 주로 PRADA, MONCLER, GUCCI, BOTTEGA VENETA 등 대형 럭셔리 브랜드

---

## 3. okmall vs nextzennpack 상세 (522 + 35)

> 분석 기준일: 2026-03-26

### 3-1. 요약

| 구분 | 건수 |
|------|-----:|
| 정확 일치 중복 모델 | 522 |
| 유사 일치 중복 모델 (추가분) | 35 |
| 실질 총 중복 | **557** |
| 정확 일치 중 okmall 바이마 등록 | 170 |
| 정확 일치 중 nextzennpack 바이마 등록 | 0 |

### 3-2. 정확 일치 중복 브랜드별 (522개)

model_id가 문자열 완전 일치하는 경우.

| okmall 브랜드 | nextzennpack 브랜드 | 중복 수 | okmall 등록 | model_id 예시 |
|--------------|-------------------|-------:|----------:|--------------|
| STONE ISLAND | stone island | 89 | 58 | `78152NS86 V0098` |
| Thom Browne | thom browne | 65 | 17 | `FAW035A 00198 001` |
| MONCLER | moncler | 59 | 14 | `1A00003 597YF 999` |
| PRADA | prada | 44 | 1 | `1BA426 2CYR F0002` |
| BURBERRY | burberry | 43 | 5 | `8024685` |
| AMI | ami | 34 | 15 | `FKC127 005 055` |
| BOTTEGA VENETA | Bottega Veneta | 25 | 6 | `666688 VMAY1 9009` |
| GUCCI | gucci | 22 | 9 | `400593 AP00T 1000` |
| MSGM | MSGM | 22 | 18 | `2000MDA510 200002 01` |
| AUTRY | AUTRY | 18 | 13 | `AULM LL05` |
| SAINT LAURENT PARIS | saint laurent(ysl) | 17 | 0 | `364021 BOW0J 1000` |
| Miu Miu | MIUMIU | 14 | 1 | `5BA281 2CRW F0046` |
| Golden Goose | golden goose | 11 | 5 | `GMF00197 F000537 10283` |
| COMME DES GARCONS | comme des garcons | 10 | 1 | `AX N007 051 2` |
| A.P.C | A.P.C. | 9 | 2 | `COEZD F27561 IAJ` |
| CHLOE | chloe | 9 | 0 | `CHC22AS397I26 90U` |
| MAISON MARGIELA | MAISON MARGIELA | 7 | 0 | `S55UI0203 P4745 T8013` |
| FENDI | fendi | 6 | 0 | `8BH394 ABVL F0PWZ` |
| MOOSE KNUCKLES | Moose Knuckles | 5 | 4 | `M32LB002SX 305` |
| BALENCIAGA | BALENCIAGA | 4 | 1 | `587280 W2DBQ 1015` |
| MARNI | MARNI | 3 | 0 | `SBMP0193U0 P6948 00N99` |
| THE ROW | THE ROW | 3 | 0 | `W1314 L129 BLK` |
| COMMON PROJECTS | COMMON PROJECTS | 1 | 0 | `3701 0506` |
| MULBERRY | mulberry | 1 | 0 | `HH4966 205 A100` |
| TOM FORD | TOM FORD | 1 | 0 | `YM233 LCL081G 1N001` |
| **합계** | | **522** | **170** | |

"okmall 등록" = 중복 모델 중 okmall 측에서 이미 바이마에 등록 완료된 건수 (`buyma_product_id IS NOT NULL`).

### 3-3. 유사 일치 중복 (추가 35개)

공백(` `), 하이픈(`-`), 슬래시(`/`), 백틱(`` ` ``)을 제거한 뒤 대문자 통일 후, 한쪽이 다른 쪽을 **포함**하는 경우.
정확 일치에는 포함되지 않지만 실질적으로 같은 상품인 모델.

| okmall 브랜드 | nextzennpack 브랜드 | 유사 중복 수 | 불일치 원인 | 예시 |
|--------------|-------------------|----------:|-----------|------|
| AMI | ami | 14 | okmall에 `BF` 접두사 추가 | `BFUPL001 760 001` ↔ `UPL001 760 001` |
| COMME DES GARCONS | comme des garcons | 8 | okmall에 `(P1코드 / AZ코드)` 병기 | `AX N008 051 3 (P1N008 / AZ N008 051` ↔ `AX N008 051 3` |
| repetto | repetto | 6 | okmall에 슬래시 구분 2가지 표기 병기 | `V1790VE410 / V1790VE 410` ↔ `V1790VE 410` |
| KENZO | kenzo | 3 | okmall에 시즌 접두사 (`FD6`, `FD5`) | `FD6 5PU429 3BB 99J` ↔ `5PU429 3BB 99J` |
| J.LINDEBERG | J LINDEBERG | 2 | okmall에 슬래시 구분 구모델/신모델 병기 | `GWSD06345/GWSD10005-0000` ↔ `GWSD10005 0000` |
| MONCLER | moncler | 1 | nextzennpack에 `K2 09B` 접두사 | `4G00200 M4522 999` ↔ `K2 09B 4G00200 M4522 999` |
| MOOSE KNUCKLES | Moose Knuckles | 1 | okmall에 슬래시 구분 구모델/신모델 병기 | `M32LJ129S/M32LJ129SX 305` ↔ `M32LJ129SX 305` |
| **합계** | | **35** | | |

#### 유사 불일치 패턴 정리

| 패턴 | 발생 브랜드 | 건수 | 설명 |
|------|-----------|-----:|------|
| okmall 접두사 추가 | AMI (`BF`), KENZO (`FD5`/`FD6`) | 17 | okmall이 시즌/라인 코드를 model_id 앞에 붙임 |
| okmall 병기 (`A / B`) | CDG, repetto, J.LINDEBERG, MOOSE KNUCKLES | 17 | okmall이 구모델/신모델 또는 표기변형을 슬래시로 병기 |
| nextzennpack 접두사 추가 | MONCLER (`K2 09B`) | 1 | nextzennpack이 카테고리 코드를 앞에 붙임 |

### 3-4. 중복 없는 브랜드 (양쪽 존재, 중복 0)

model_id 형식 자체가 완전히 달라서 정확/유사 모두 매칭 안 되는 브랜드.

| okmall 브랜드 | nextzennpack 브랜드 | okmall model_id 예시 | nextzennpack model_id 예시 | 비고 |
|--------------|-------------------|---------------------|--------------------------|------|
| Salvatore Ferragamo | ferragamo | `01H585 768694` | `020983 0758350` | 완전히 다른 코드 체계 |
| G/FORE | G FORE | `GMF000058-SNOW/TWILIGHT` | `G4MS23K001 KOP` | 코드 체계 다름 |
| ETRO | etro | `1P050 8502 0800` | `16365 8784 200` | 코드 체계 다름 |
| Barbour | BARBOUR | `MCA0931 GN51` | `MTS1132 BK31` | 겹치는 상품이 없거나 코드 다름 |
| DSQUARED2 | dsquared2 | `S71AN0492 S76498 900` | `S74LB0993` | 겹치는 상품 없음 |
| VALENTINO | valentino | `3W2S0K55AEQ 0B4` | `3Y2T0Q90 ECU 0NO` | 겹치는 상품 없음 |
| PARAJUMPERS | Parajumpers | `25FW-PMJKMA01-541` | `PWPUSL33 562` | 코드 체계 다름 |
| Mark&Lona | MARK&LONA | `MCM-4B-AT65-0019` | `MLM 2A AP03 BLACK` | 코드 체계 다름 |
| JIL SANDER | JIL SANDER | `J07WD0023 P4840 001` | `JSMU840091 MUS00008N 210` | 겹치는 상품 없음 |
| TEKLA | TEKLA | `SWT BEG` | `SWT SB` | 색상코드만 다름 (상품 다름) |
| Palm Angels | palm angels | `PMCH013F24FLE002 1003` | `PMBB098R21FLE004 0418` | 겹치는 상품 없음 |
| ALEXANDER MCQUEEN | alexander mcqueen | `553680 WIAIG 9061` | `551156 1JM11 1000` | 겹치는 상품 없음 |
| AXEL ARIGATO | AXEL ARIGATO | `A2900001 NAVY` | `33054` | 코드 체계 완전히 다름 |
| TEN C | TEN C | `17CTCUC03075 003780 661` | `22CTCUH02098 A06021 888` | 겹치는 상품 없음 |
| CELINE | CELINE | `118703GGT 38NO` | `189593AH4 28LB` | 겹치는 상품 없음 |
| ISABEL MARANT | ISABEL MARANT | `CA0163FA D1L16E ANBK` | `22PTS0427 22P049H 30FN` | 겹치는 상품 없음 |
| SAINT JAMES | saint james | `1326-01` | `3737 CU` | 겹치는 상품 없음 |

---

## 4. okmall vs labellusso 상세 (922 + 186)

> 분석 기준일: 2026-03-27

### 4-1. 요약

| 구분 | 건수 |
|------|-----:|
| 정확 일치 중복 모델 | 922 |
| 유사 일치 중복 모델 (추가분) | 186 |
| 실질 총 중복 | **~1,108** |
| 정확 일치 중 okmall 바이마 등록 | 349 |
| 정확 일치 중 labellusso 바이마 등록 | 0 |

> labellusso는 아직 바이마 미등록 → 현시점 중복 충돌 없음
> 본격 가동 시 922~1,108개 중복 등록 위험

### 4-2. 정확 일치 중복 브랜드별 (922개)

model_id가 문자열 완전 일치하는 경우.

| okmall 브랜드 | labellusso 브랜드 | okmall RAW | label RAW | 중복 수 | okmall 등록 | model_id 예시 |
|--------------|-----------------|----------:|---------:|-------:|-----------:|--------------|
| MONCLER | MONCLER | 931 | 240 | 89 | 21 | `1A00001 597FA 999` |
| Thom Browne | THOM BROWNE | 784 | 173 | 69 | 9 | `FAW035A 00198 001` |
| GANNI | GANNI | 395 | 121 | 66 | 39 | `A1050029 151` |
| ISABEL MARANT | ISABEL MARANT | 742 | 114 | 60 | 27 | `BK0014FA A1E21S 01BK` |
| PRADA | PRADA | 755 | 209 | 59 | 5 | `1CC545 053 F0002` |
| DIESEL | DIESEL | 630 | 115 | 52 | 26 | `A03943 068FN 01` |
| A.P.C | A.P.C | 851 | 98 | 47 | 27 | `COBQX M26497 AAB` |
| CELINE | CELINE | 392 | 145 | 46 | 9 | `10F993FQD 38SI` |
| CARHARTT WIP | CARHARTT | 407 | 132 | 42 | 18 | `I013507 ZMXX` |
| GUCCI | GUCCI | 650 | 187 | 36 | 8 | `200035 KQWBG 1060` |
| Vivienne Westwood | VIVIENNE WESTWOOD | 716 | 285 | 34 | 12 | `1803002Q Y001A N402` |
| LOEWE | LOEWE | 217 | 129 | 31 | 7 | `A039N23X01 1100` |
| FENDI | FENDI | 374 | 108 | 25 | 6 | `7C0541 ASIW F1S9U` |
| BOTTEGA VENETA | BOTTEGA VENETA | 355 | 213 | 22 | 3 | `239988 V3UN1 1275` |
| JACQUEMUS | JACQUEMUS | 270 | 62 | 22 | 15 | `213AC002 5012 560` |
| Miu Miu | MIU MIU | 284 | 106 | 22 | 4 | `5CC630 2DNT F0002` |
| nanamica | HERON PRESTON | 199 | 36 | 19 | 12 | `S25FA044E K` ※크로스브랜드 |
| AMI | AMI | 584 | 69 | 16 | 6 | `BFUKS406 018 009` |
| HUGO BOSS | HUGO BOSS | 555 | 127 | 13 | 11 | `50490775 001` |
| OUR LEGACY | OUR LEGACY | 381 | 24 | 13 | 4 | `A2208BBLA` |
| Dior | DIOR HOMME | 543 | 45 | 12 | 10 | `113J698A0531 989` |
| Brunello Cucinelli | BRUNELLO CUCINELLI | 240 | 58 | 11 | 6 | `M0B138440 CE139` |
| MIHARA YASUHIRO | MAISON MIHARA YASUHIRO | 248 | 11 | 10 | 3 | `A01FW702 BLACK` |
| STONE ISLAND | STONE ISLAND | 1,326 | 192 | 9 | 8 | `7915527A6 V0020` |
| Barbour | BARBOUR | 78 | 107 | 8 | 2 | `LRF0043 NY71` |
| BALENCIAGA | BALENCIAGA | 273 | 51 | 7 | 5 | `688757 W3RQ2 2298` |
| MAISON MARGIELA | MAISON MARGIELA | 184 | 70 | 7 | 1 | `S36UI0416 P4455 T8013` |
| Paul Smith | PAUL SMITH | 267 | 12 | 7 | 7 | `M2R 151LE P21511 02A` |
| CANADA GOOSE | CANADA GOOSE | 70 | 15 | 6 | 3 | `2050M 9061` |
| HUMAN MADE | HUMAN MADE | 143 | 26 | 6 | 3 | `HM28CS006 WHITE` |
| BAO BAO ISSEY MIYAKE | BAO BAO | 39 | 27 | 5 | 2 | `BB58AG053 91` |
| Gallery Dept. | GALLERY DEPT | 99 | 31 | 5 | 5 | `AC-90138 NAVY WHITE` |
| MULBERRY | MULBERRY | 27 | 88 | 5 | 5 | `HH9062 000 A100` |
| SAINT LAURENT PARIS | SAINT LAURENT PARIS | 241 | 41 | 5 | 2 | `614443 Y04PD 4597` |
| BIRKENSTOCK | BIRKENSTOCK | 79 | 9 | 4 | 3 | `1010504` |
| MARNI | MARNI | 118 | 48 | 4 | 0 | `HUMU0223HP USCX51 SLW01` |
| TOD\`S | TOD'S | 227 | 65 | 4 | 1 | `XAMDBS57200RLX S410` |
| ETRO | ETRO | 70 | 179 | 3 | 3 | `1P050 8502 0001` |
| SUNNEI | SUNNEI | 54 | 15 | 3 | 3 | `MRTWXJER069 JER012 7478` |
| AUTRY | AUTRY | 205 | 13 | 2 | 2 | `ADLW NW02` |
| CHLOE | CHLOE | 44 | 39 | 2 | 0 | `CHC22U188Z3 001` |
| Golden Goose | GOLDEN GOOSE | 326 | 8 | 2 | 1 | `GMF00717 F006013 10283` |
| Loro Piana | LORO PIANA | 30 | 42 | 2 | 2 | `FAI5580 W000` |
| Courreges | COURREGES | 94 | 19 | 1 | 1 | `223CJU001VY0014 9020` |
| GIVENCHY | GIVENCHY | 171 | 19 | 1 | 0 | `BB50R9B1DR 255` |
| HERNO | HERNO | 53 | 24 | 1 | 0 | `PI0177DIC 12017Z 9300` |
| HOMME PLISSE ISSEY MIYAKE | HOMME PLISSE | 144 | 17 | 1 | 0 | `HP57JF553 15` |
| PYRENEX | PYRENEX | 8 | 16 | 1 | 0 | `HUY021 0009` |
| Ten c | TEN C | 141 | 15 | 1 | 0 | `23CTCUC04111 003780 999` |
| VERSACE | VERSACE | 400 | 44 | 1 | 1 | `1007220 1A05134 1B00V` |
| VISVIM | VISVIM | 149 | 7 | 1 | 1 | `0124105010030 WHITE` |
| **합계** | | | | **922** | **349** | |

"okmall 등록" = 중복 모델 중 okmall 측에서 이미 바이마에 등록 완료된 건수 (`buyma_product_id IS NOT NULL`).

#### 크로스 브랜드 매칭 (3건)

model_id는 일치하지만 수집처 간 브랜드가 다른 경우 — 한쪽의 브랜드 매핑 오류 가능성.

| okmall | labellusso | 중복 수 | model_id 예시 |
|--------|-----------|-------:|--------------|
| nanamica | HERON PRESTON | 19 | `S25FA044E K` |
| CELINE | SALVATORE FERRAGAMO | 1 | `45AX62AH4 04LU` |
| CHLOE | MAISON KITSUNE | 1 | `CHC23SS595I31 25M` |

### 4-3. 유사 일치 중복 — 같은 브랜드 내 (186개)

정규화(공백/하이픈/슬래시/백틱/어포스트로피 제거, 대문자 통일) 후 한쪽이 다른 쪽을 포함하는 경우. 정확 일치 제외, 최소 6자 이상.

| okmall 브랜드 | labellusso 브랜드 | 유사 중복 수 | 불일치 원인 | 예시 |
|--------------|-----------------|----------:|-----------|------|
| BURBERRY | BURBERRY | 65 | labellusso에 상품명 접미 | `8050509` ↔ `8050509 LF TNR NEW REGIS L CHK A7028` |
| PRADA | PRADA | 24 | labellusso에 `(OOO)` 등 접미 | `1BD394 RDLN F0002` ↔ `1BD394 RDLN F0002 (NOO)` |
| HELEN KAMINSKI | HELEN KAMINSKI | 23 | labellusso에 색상명 접미 | `BAG51753` ↔ `BAG51753 NOUGAT PARCHMEN T` |
| PARAJUMPERS | PARAJUMPERS | 16 | okmall에 `25FW-` 접두사 | `25FW-PMPUPP01-541` ↔ `PMPUPP01 541` |
| UGG | UGG | 10 | labellusso에 색상명 접미 | `1016224` ↔ `1016224 CHESTNUT` |
| Y-3 | Y-3 | 9 | labellusso에 색상명 접미 | `IN2391` ↔ `IN2391 BLACK` |
| Miu Miu | MIU MIU | 7 | labellusso에 `(MON)` 등 접미 | `5BB142 ACR3 F0002` ↔ `5BB142 ACR3 F0002 (OON)` |
| VALENTINO | VALENTINO | 6 | okmall 구/신모델 병기 | `4Y0HDA10 YFB 0NA / 4Y2HDA10...` ↔ `4Y2HDA10 YFB 0NA` |
| MAISON KITSUNE | MAISON KITSUNE | 3 | okmall에 하이픈+색상코드 접미 | `IU00355KM0001-BK/P199` ↔ `IU00355KM0001 BK` |
| OUR LEGACY | OUR LEGACY | 2 | labellusso에 색상코드 접미 | `M2205TB` ↔ `M2205TB BG` |
| HUGO BOSS | HUGO BOSS | 2 | okmall에 하이픈, labellusso에 색상명 | `50495742-001` ↔ `50495742 001 BLACK WHITE` |
| A.P.C | A.P.C | 2 | labellusso에 색상코드 접미 | `PXAWV F61404` ↔ `PXAWV F61404 CAD` |
| TORY BURCH | TORY BURCH | 2 | okmall 구/신모델 병기 | `11165504-720 / 17843-720` ↔ `17843 720` |
| COACH | COACH | 1 | labellusso에 색상코드 접미 | `C0689 B4` ↔ `C0689 B4/BK` |
| KENZO | KENZO | 1 | okmall에 시즌 접두사 (`FF5`) | `FF5 5TS533 4SG 99J` ↔ `FF55TS5334SG 99` |
| repetto | REPETTO | 1 | okmall에 슬래시 구분 병기 | `V4200VED410 / V4200VED 410` ↔ `V4200VED 410` |
| COMME DES GARCONS | COMME DES GARCONS | 1 | labellusso에 색상명 접미 | `AX N049 051 1` ↔ `AX N049 051 1 BLACK` |
| ROGER VIVIER | ROGER VIVIER | 1 | 공백 차이 | `RVW50624180 KOT B999` ↔ `RVW50624180 KOT B999` |
| AMI | AMI | 1 | okmall에 `BF` 접두사 | `BFUTS003 724 100` ↔ `UTS003 724 100` |
| DIESEL | DIESEL | 2 | labellusso 끝자리 차이 | `A17880 0CLBR 9XXA` ↔ `A17880 0CLBR 9XX` |
| CARHARTT WIP | CARHARTT | 2 | labellusso 끝자리 차이 | `I023807 00F` ↔ `I023807 00FXX` |
| Sporty&Rich | SPORTY & RICH | 1 | 정규화 후 일치 | `TS885` ↔ `TS885CR` |
| BURBERRY | STONE ISLAND | 2 | 크로스브랜드 오탐 가능 | `8015237` ↔ `801523757 A0001` |
| Ermenegildo Zegna | EMENEGILDO ZEGNA | 1 | 공백 위치 차이 | `RE7358 A5B746 K09` ↔ `E7358A5 B746 K09` |
| FEAR OF GOD ESSENTIALS | FEAR OF GOD | 6 | okmall에 색상코드 접미 | `125SP254477F MOSS` ↔ `125SP254477F` |
| **합계** | | **~186** | | |

#### 유사 불일치 패턴 정리

| 패턴 | 발생 브랜드 | 건수 | 설명 |
|------|-----------|-----:|------|
| labellusso 색상/상품명 접미 | BURBERRY, HELEN KAMINSKI, UGG, Y-3 등 | ~120 | labellusso가 model_id 뒤에 색상명/상품명을 붙임 |
| labellusso 괄호 접미 `(OOO)` | PRADA, Miu Miu | ~31 | labellusso가 원산지/속성 코드를 괄호로 접미 |
| okmall 시즌 접두사 | PARAJUMPERS (`25FW-`), KENZO (`FF5`) | ~17 | okmall이 시즌 코드를 앞에 붙임 |
| okmall 구/신모델 병기 | VALENTINO, TORY BURCH, MAISON KITSUNE | ~11 | okmall이 슬래시로 복수 모델번호 병기 |
| 끝자리 변형 | DIESEL, CARHARTT | ~4 | 한쪽이 접미사 추가/제거 |

---

## 5. 중복 처리 설계

### 5-1. 현재 상태

- **okmall**: 16,011개 바이마 등록 완료 — 가장 많은 데이터 보유
- **kasina**: 9,415개 등록 — 스포츠/스트릿 특화, 다른 소스와 거의 안 겹침
- **nextzennpack**: 255개 등록 (EMPORIO ARMANI만) — okmall과 522개 중복 위험
- **labellusso**: 미등록 — okmall과 922개, nextzennpack과 162개 중복 위험
- 3곳 동시 중복 79개 (okmall+nextzennpack+labellusso) 별도 처리 필요

### 5-2. 중복 판정 방법 (제안)

```
정규화 함수: 공백, -, /, `, ' 제거 후 대문자 변환
비교 방식: 정규화된 문자열의 포함(contains) 관계 체크
최소 길이: 6자 이상 (짧은 코드 오탐 방지)
```

**정확 일치**: `normalize(A) == normalize(B)`
**유사 일치**: `normalize(A) in normalize(B)` 또는 `normalize(B) in normalize(A)`

### 5-3. 중복 시 우선순위 결정 기준 (안)

| 우선순위 | 조건 | 이유 |
|---------|------|------|
| 1 | 이미 바이마에 등록된 쪽 | 등록 후 카테고리 변경 불가, 기존 판매이력 유지 |
| 2 | 가격이 더 낮은 쪽 | 최저가 경쟁력 |
| 3 | 이미지가 있는 쪽 (has_own_images) | nextzennpack, labellusso는 자체 이미지 보유 |
| 4 | okmall 우선 (기본) | 데이터가 더 풍부하고 안정적 |

### 5-4. 구현 위치

- `raw_to_converter_v2.py` 또는 `stock_price_synchronizer_v2.py`에서 중복 체크
- 또는 별도 `duplicate_checker.py` 모듈로 분리
- CONVERT 단계 진입 전에 중복 model_id를 감지하여 skip 처리
