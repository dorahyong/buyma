# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
import os
import json
import pymysql
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# [기존 유지] 로그 파일 경로
LOG_FILE = "/home/ubuntu/buyma/buyma/webhook/webhook.log"

# [기존 유지] DB 접속 정보
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def log_webhook(data, event_type=None):
    """[기존 기능] 웹훅 데이터를 파일에 로그"""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        timestamp = datetime.now().isoformat()
        f.write(f"\n{'='*60}\n")
        f.write(f"[{timestamp}] Webhook Received (Event: {event_type})\n")
        f.write(json.dumps(data, indent=2, ensure_ascii=False))
        f.write(f"\n{'='*60}\n")

def _log_api_response(cursor, ref_num, data):
    """webhook 응답을 buyma_listing_api_logs 에 기록.
    관리번호(reference_number)는 buyma_listings 만 갖는다 (2026-08-04: ace_products 쪽 폐지)."""
    payload = json.dumps(data, ensure_ascii=False)
    cursor.execute("SELECT id FROM buyma_listings WHERE reference_number = %s", (ref_num,))
    lrow = cursor.fetchone()
    if lrow:
        cursor.execute("""
            INSERT INTO buyma_listing_api_logs (buyma_listing_id, api_response_json, last_api_call_at)
            VALUES (%s, %s, NOW())
            ON DUPLICATE KEY UPDATE api_response_json = VALUES(api_response_json), last_api_call_at = NOW()
        """, (lrow['id'], payload))


def update_db_with_webhook(event, data):
    """[추가 기능] DB 업데이트 (에러가 나도 로그 기록에는 방해 안 줌)"""
    try:
        # 실제 데이터 구조에 맞게 ID 추출 (최상위 우선 확인)
        ref_num = data.get('reference_number') or (data.get('product', {}).get('reference_number'))

        if not ref_num:
            return

        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            if event in ['product/create', 'product/update']:
                buyma_id = data.get('id') or (data.get('product', {}).get('id'))
                status = data.get('status', '')

                # delete 성공: status=buyer_deleted → is_published=0
                if status == 'buyer_deleted':
                    cursor.execute("""
                        UPDATE buyma_listings
                        SET is_published = 0, status = 'deleted', updated_at = NOW()
                        WHERE reference_number = %s
                    """, (ref_num,))
                    print(f"[WEBHOOK] 삭제 성공: {ref_num} → is_published=0")
                # 품절 처리(재고 API)로 '출품정지중' → is_published=0 (★삭제 아님, buyma_id 유지)
                elif status == 'buyer_suspended':
                    cursor.execute("""
                        UPDATE buyma_listings
                        SET is_published = 0, status = 'soldout', updated_at = NOW()
                        WHERE reference_number = %s
                    """, (ref_num,))
                    print(f"[WEBHOOK] 품절(출품정지중): {ref_num} → is_published=0")
                elif buyma_id:
                    cursor.execute("""
                        UPDATE buyma_listings
                        SET buyma_product_id = %s,
                            is_published = 1,
                            status = 'success',
                            is_buyma_locked = 1,
                            buyma_registered_at = COALESCE(buyma_registered_at, NOW()),
                            updated_at = NOW()
                        WHERE reference_number = %s
                    """, (buyma_id, ref_num))
                    # api_logs 기록 (ace 또는 merge listing)
                    _log_api_response(cursor, ref_num, data)
                    print(f"[WEBHOOK] 등록 성공: {ref_num} → buyma_id={buyma_id}, is_buyma_locked=1")

            elif event == 'product/fail_to_create':
                # 등록 실패: 바이마에 상품이 없음 → is_published=0으로 재등록 대상
                errors = data.get('errors', {})
                error_str = str(errors)

                if '商品IDは不正な値です' in error_str or '削除できない商品です' in error_str:
                    cursor.execute("""
                        UPDATE buyma_listings
                        SET status = 'fail',
                            buyma_product_id = NULL,
                            is_published = 0,
                            is_buyma_locked = 0,
                            updated_at = NOW()
                        WHERE reference_number = %s
                    """, (ref_num,))
                # 이미 출품정지중인 상품에 재고 API(출품정지)를 또 보낸 것.
                #   관리번호로만 상품을 지목하는데 정지된 상품은 못 찾아 거부된다.
                #   우리 DB만 게시중이라 매 배치마다 반복 → 여기서 상태를 맞춰 대상에서 뺀다.
                #   ★ 상품은 BUYMA에 살아 있다 → buyma_product_id·is_buyma_locked 는 건드리지 않는다.
                #     지우면 신규등록 차단이 풀려 같은 상품이 또 올라간다. (2026-08-13 실측 314건)
                elif '商品管理番号は不正な値です' in error_str:
                    cursor.execute("""
                        UPDATE buyma_listings
                        SET is_published = 0, status = 'soldout', updated_at = NOW()
                        WHERE reference_number = %s
                    """, (ref_num,))
                    print(f"[WEBHOOK] 이미 출품정지중: {ref_num} → is_published=0")
                else:
                    cursor.execute("""
                        UPDATE buyma_listings
                        SET status = 'fail',
                            updated_at = NOW()
                        WHERE reference_number = %s
                    """, (ref_num,))
                # api_logs 기록 (ace 또는 merge listing)
                _log_api_response(cursor, ref_num, data)

            elif event == 'product/fail_to_update':
                # 수정 실패: 바이마에 상품이 존재함 → is_published 유지 (0으로 바꾸면 안됨)
                cursor.execute("""
                    UPDATE buyma_listings
                    SET status = 'fail',
                        updated_at = NOW()
                    WHERE reference_number = %s
                """, (ref_num,))
                # api_logs 기록 (ace 또는 merge listing)
                _log_api_response(cursor, ref_num, data)

            conn.commit()
        conn.close()
    except Exception as e:
        # DB 업데이트 실패해도 콘솔에만 찍고 넘어감 (로그 파일 기록은 이미 완료된 상태)
        print(f"[DB ERROR] {e}")

@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Buyma Webhook Server is running"}), 200

@app.route('/webhook/buyma', methods=['POST'])
def buyma_webhook():
    event_type = request.headers.get('X-Buyma-Event')
    try:
        data = request.get_json()

        # 1. 파일 로그 저장 (기존 기능 - 무조건 실행)
        log_webhook(data, event_type)

        # 2. DB 업데이트 (추가 기능 - 실패해도 무관)
        update_db_with_webhook(event_type, data)

        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    # 기존과 동일하게 설정
    app.run(host='127.0.0.1', port=8000, debug=True)

