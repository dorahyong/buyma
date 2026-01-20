# -*- coding: utf-8 -*-
import pymysql
import json
import sys

# 결과를 파일로 저장
output_file = open('db_check_result.txt', 'w', encoding='utf-8')

def print_out(msg):
    output_file.write(str(msg) + '\n')

conn = pymysql.connect(
    host='54.180.248.182',
    port=3306,
    user='block',
    password='1234',
    database='buyma',
    charset='utf8mb4'
)
cur = conn.cursor(pymysql.cursors.DictCursor)

# ace_products 데이터 조회
print_out("=== ace_products ===")
cur.execute('SELECT id, raw_data_id, reference_number, control, name, brand_id, category_id, price, available_until, buying_area_id, shipping_area_id, model_no, is_published FROM ace_products LIMIT 3')
products = cur.fetchall()
for p in products:
    print_out(json.dumps(p, ensure_ascii=False, default=str, indent=2))
    print_out("---")

# ace_product_images 데이터 조회
print_out("\n=== ace_product_images ===")
cur.execute('SELECT * FROM ace_product_images LIMIT 5')
images = cur.fetchall()
for img in images:
    print_out(json.dumps(img, ensure_ascii=False, default=str, indent=2))
    print_out("---")

# ace_product_options 데이터 조회
print_out("\n=== ace_product_options ===")
cur.execute('SELECT * FROM ace_product_options LIMIT 5')
options = cur.fetchall()
for opt in options:
    print_out(json.dumps(opt, ensure_ascii=False, default=str, indent=2))
    print_out("---")

# ace_product_variants 데이터 조회
print_out("\n=== ace_product_variants ===")
cur.execute('SELECT * FROM ace_product_variants LIMIT 5')
variants = cur.fetchall()
for var in variants:
    print_out(json.dumps(var, ensure_ascii=False, default=str, indent=2))
    print_out("---")

# buyma_tokens 데이터 조회
print_out("\n=== buyma_tokens ===")
cur.execute('SELECT * FROM buyma_tokens LIMIT 1')
tokens = cur.fetchall()
for t in tokens:
    # 토큰은 일부만 표시
    t_copy = t.copy()
    if t_copy.get('access_token'):
        t_copy['access_token'] = t_copy['access_token'][:50] + '...'
    if t_copy.get('refresh_token'):
        t_copy['refresh_token'] = t_copy['refresh_token'][:50] + '...' if t_copy['refresh_token'] else None
    print_out(json.dumps(t_copy, ensure_ascii=False, default=str, indent=2))

# ace_product_shipping 데이터 조회
print_out("\n=== ace_product_shipping ===")
cur.execute('SELECT * FROM ace_product_shipping LIMIT 5')
shipping = cur.fetchall()
for s in shipping:
    print_out(json.dumps(s, ensure_ascii=False, default=str, indent=2))
    print_out("---")

# 총 데이터 수 조회
print_out("\n=== 데이터 수 ===")
cur.execute('SELECT COUNT(*) as cnt FROM ace_products')
print_out(f"ace_products: {cur.fetchone()['cnt']}")
cur.execute('SELECT COUNT(*) as cnt FROM ace_product_images')
print_out(f"ace_product_images: {cur.fetchone()['cnt']}")
cur.execute('SELECT COUNT(*) as cnt FROM ace_product_options')
print_out(f"ace_product_options: {cur.fetchone()['cnt']}")
cur.execute('SELECT COUNT(*) as cnt FROM ace_product_variants')
print_out(f"ace_product_variants: {cur.fetchone()['cnt']}")

conn.close()
output_file.close()
print("DB check completed. Results saved to db_check_result.txt")
