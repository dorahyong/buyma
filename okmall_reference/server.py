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
                if buyma_id:
                    cursor.execute("""
                        UPDATE ace_products
                        SET buyma_product_id = %s,
                            is_published = 1,
                            status = 'success',
                            is_buyma_locked = 1,
                            buyma_registered_at = COALESCE(buyma_registered_at, NOW()),
                            updated_at = NOW()
                        WHERE reference_number = %s
                    """, (buyma_id, ref_num))
                    # ace_product_id 조회 후 api_logs에 저장
                    cursor.execute("SELECT id FROM ace_products WHERE reference_number = %s", (ref_num,))
                    row = cursor.fetchone()
                    if row:
                        cursor.execute("""
                            INSERT INTO ace_product_api_logs (ace_product_id, api_response_json, last_api_call_at)
                            VALUES (%s, %s, NOW())
                            ON DUPLICATE KEY UPDATE api_response_json = VALUES(api_response_json), last_api_call_at = NOW()
                        """, (row['id'], json.dumps(data, ensure_ascii=False)))
                    print(f"[WEBHOOK] 등록 성공: {ref_num} → buyma_id={buyma_id}, is_buyma_locked=1")

            elif event == 'product/fail_to_create':
                # 등록 실패: 바이마에 상품이 없음 → is_published=0으로 재등록 대상
                errors = data.get('errors', {})
                error_str = str(errors)

                if '商品IDは不正な値です' in error_str or '削除できない商品です' in error_str:
                    cursor.execute("""
                        UPDATE ace_products
                        SET status = 'fail',
                            buyma_product_id = NULL,
                            is_published = 0,
                            is_buyma_locked = 0,
                            updated_at = NOW()
                        WHERE reference_number = %s
                    """, (ref_num,))
                else:
                    cursor.execute("""
                        UPDATE ace_products
                        SET status = 'fail',
                            updated_at = NOW()
                        WHERE reference_number = %s
                    """, (ref_num,))
                # api_logs에 저장
                cursor.execute("SELECT id FROM ace_products WHERE reference_number = %s", (ref_num,))
                row = cursor.fetchone()
                if row:
                    cursor.execute("""
                        INSERT INTO ace_product_api_logs (ace_product_id, api_response_json, last_api_call_at)
                        VALUES (%s, %s, NOW())
                        ON DUPLICATE KEY UPDATE api_response_json = VALUES(api_response_json), last_api_call_at = NOW()
                    """, (row['id'], json.dumps(data, ensure_ascii=False)))

            elif event == 'product/fail_to_update':
                # 수정 실패: 바이마에 상품이 존재함 → is_published 유지 (0으로 바꾸면 안됨)
                cursor.execute("""
                    UPDATE ace_products
                    SET status = 'fail',
                        updated_at = NOW()
                    WHERE reference_number = %s
                """, (ref_num,))
                # api_logs에 저장
                cursor.execute("SELECT id FROM ace_products WHERE reference_number = %s", (ref_num,))
                row = cursor.fetchone()
                if row:
                    cursor.execute("""
                        INSERT INTO ace_product_api_logs (ace_product_id, api_response_json, last_api_call_at)
                        VALUES (%s, %s, NOW())
                        ON DUPLICATE KEY UPDATE api_response_json = VALUES(api_response_json), last_api_call_at = NOW()
                    """, (row['id'], json.dumps(data, ensure_ascii=False)))

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

