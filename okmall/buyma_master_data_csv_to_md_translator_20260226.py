#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
BUYMA 마스터 데이터 CSV → 한글 번역 마크다운 변환 스크립트
- models.csv 제외, 11개 파일 처리
- 일본어 컬럼 옆에 _ko 번역 컬럼 추가
- Gemini 2.0 Flash API 사용
- 번역 캐시 지원 (중단 후 재개 가능)
"""

import csv
import json
import time
import os
import sys
import requests
from pathlib import Path
from dotenv import load_dotenv

# .env 로드
load_dotenv(Path(__file__).resolve().parent.parent / '.env', override=True)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "buyma_master_data_20260226"
OUTPUT_DIR = INPUT_DIR / "md"
CACHE_FILE = INPUT_DIR / "translation_cache.json"

BATCH_SIZE = 200
REQUEST_INTERVAL = 4  # seconds (free tier: 15 RPM)

# 파일별 번역 대상 컬럼 (작은 파일 → 큰 파일 순)
FILE_CONFIG = [
    ("colors.csv",             ["name"]),
    ("units.csv",              ["name", "category_name", "category_paths"]),
    ("themes.csv",             ["name"]),
    ("shipping_services.csv",  ["name"]),
    ("seasons.csv",            ["name"]),
    ("areas.csv",              ["continent", "country", "city"]),
    ("categories.csv",         ["paths", "name"]),
    ("size_details.csv",       ["name", "group_name", "category_name", "category_paths"]),
    ("sizes.csv",              ["name", "category_name", "category_paths"]),
    ("tags.csv",               ["tag", "group", "category_name", "category_paths"]),
    ("brands.csv",             ["brand_name"]),
]


def has_japanese(text):
    """텍스트에 일본어 문자(히라가나/가타카나/한자) 포함 여부"""
    if not text:
        return False
    return any(
        '\u3040' <= ch <= '\u309F' or   # 히라가나
        '\u30A0' <= ch <= '\u30FF' or   # 가타카나
        '\u4E00' <= ch <= '\u9FFF' or   # CJK 통합 한자
        '\u3400' <= ch <= '\u4DBF'      # CJK 확장 A
        for ch in text
    )


def translate_batch(texts):
    """Gemini API로 일본어 → 한국어 배치 번역, 인덱스 키 JSON 반환"""
    indexed = {str(i): t for i, t in enumerate(texts)}

    prompt = (
        "다음 일본어 텍스트를 한국어로 번역해주세요.\n"
        "규칙:\n"
        "1. 영어 부분은 그대로 유지\n"
        "2. 카타카나 음역 → 한국어 음역 (コーチ→코치, ニュクス→뉴크스)\n"
        "3. 한자어 → 한국어 의미 (春夏→봄여름, 秋冬→가을겨울)\n"
        "4. 경로 구분자(/)는 유지\n"
        "5. 숫자/영어만 있으면 그대로 반환\n"
        "6. 지명은 한국어 표기로 (北海道→홋카이도, 東京都→도쿄도)\n\n"
        "JSON 객체로 응답 (키=인덱스번호, 값=한국어 번역):\n\n"
        + json.dumps(indexed, ensure_ascii=False)
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "maxOutputTokens": 8192,
        }
    }

    for attempt in range(3):
        try:
            resp = requests.post(
                f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            raw = resp.json()['candidates'][0]['content']['parts'][0]['text']
            translated = json.loads(raw)

            return {
                indexed[k]: translated.get(k, indexed[k])
                for k in indexed
            }
        except Exception as e:
            print(f"    오류({attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(3)

    # 실패 시 원문 반환
    return {t: t for t in texts}


def escape_md(text):
    """마크다운 테이블 셀 이스케이프"""
    return str(text).replace('|', '\\|').replace('\n', ' ')


def main():
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY 없음")
        sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    # ──────────────────────────────────────────
    # Phase 1: CSV 로드 & 번역 대상 수집
    # ──────────────────────────────────────────
    print("=" * 60)
    print("Phase 1: CSV 로드 & 번역 대상 수집")
    print("=" * 60)

    file_data = {}
    all_jp = set()

    for fname, cols in FILE_CONFIG:
        with open(INPUT_DIR / fname, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            headers = list(reader.fieldnames)
            rows = list(reader)

        file_data[fname] = {'headers': headers, 'rows': rows, 'cols': cols}

        cnt = 0
        for row in rows:
            for c in cols:
                v = row.get(c, '')
                if has_japanese(v):
                    all_jp.add(v)
                    cnt += 1

        print(f"  {fname:<30s} {len(rows):>6,}행  일본어셀 {cnt:>6,}개")

    # 캐시 로드
    cache = {}
    if CACHE_FILE.exists():
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            cache = json.load(f)
        print(f"\n캐시 로드: {len(cache):,}개")

    to_translate = sorted(t for t in all_jp if t not in cache)
    total_batches = (len(to_translate) + BATCH_SIZE - 1) // BATCH_SIZE if to_translate else 0

    print(f"\n고유 일본어: {len(all_jp):,}개")
    print(f"캐시 히트:   {len(all_jp) - len(to_translate):,}개")
    print(f"번역 필요:   {len(to_translate):,}개 ({total_batches}배치)")

    if to_translate:
        est_sec = total_batches * (REQUEST_INTERVAL + 2)
        print(f"예상 소요:   약 {est_sec // 60}분 {est_sec % 60}초")

    # ──────────────────────────────────────────
    # Phase 2: Gemini API 번역
    # ──────────────────────────────────────────
    if to_translate:
        print("\n" + "=" * 60)
        print("Phase 2: Gemini API 번역")
        print("=" * 60)

        for i in range(0, len(to_translate), BATCH_SIZE):
            batch = to_translate[i:i + BATCH_SIZE]
            bnum = i // BATCH_SIZE + 1
            print(f"  배치 {bnum:>3}/{total_batches} ({len(batch):>3}개)...", end=' ', flush=True)

            result = translate_batch(batch)
            cache.update(result)
            print("완료")

            # 중간 캐시 저장 (중단 대비)
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False)

            if i + BATCH_SIZE < len(to_translate):
                time.sleep(REQUEST_INTERVAL)

        print(f"\n번역 완료: 캐시 총 {len(cache):,}개")

    # ──────────────────────────────────────────
    # Phase 3: 마크다운 생성
    # ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Phase 3: 마크다운 생성")
    print("=" * 60)

    for fname, data in file_data.items():
        headers = data['headers']
        rows = data['rows']
        cols = data['cols']

        # 헤더: 원본 컬럼 뒤에 _ko 컬럼 삽입
        md_headers = []
        for h in headers:
            md_headers.append(h)
            if h in cols:
                md_headers.append(f"{h}_ko")

        md_path = OUTPUT_DIR / fname.replace('.csv', '.md')

        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(f"# {fname.replace('.csv', '')}\n\n")
            f.write(f"> 원본: `{fname}` | {len(rows):,}행 | 생성: 2026-02-26\n\n")
            f.write('| ' + ' | '.join(md_headers) + ' |\n')
            f.write('| ' + ' | '.join(['---'] * len(md_headers)) + ' |\n')

            for row in rows:
                cells = []
                for h in headers:
                    v = row.get(h, '')
                    cells.append(escape_md(v))
                    if h in cols:
                        ko = cache.get(v, v) if has_japanese(v) else v
                        cells.append(escape_md(ko))
                f.write('| ' + ' | '.join(cells) + ' |\n')

        print(f"  {md_path.name:<30s} {len(rows):>6,}행")

    print(f"\n{'=' * 60}")
    print(f"완료! {len(file_data)}개 파일 생성")
    print(f"출력: {OUTPUT_DIR}")


if __name__ == '__main__':
    main()
