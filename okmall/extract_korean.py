import pymysql
import os
import re
import sys
import io
from dotenv import load_dotenv

# 출력 인코딩 설정
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv('../.env')

DB_CONFIG = {
    'host': os.getenv('DB_HOST', '54.180.248.182'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'block'),
    'password': os.getenv('DB_PASSWORD', '1234'),
    'database': os.getenv('DB_NAME', 'buyma'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def extract_korean():
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            # colorsize_comments_jp 컬럼 조회 (데이터가 있는 것만)
            sql = "SELECT colorsize_comments_jp FROM ace_products WHERE colorsize_comments_jp IS NOT NULL AND colorsize_comments_jp != ''"
            cursor.execute(sql)
            rows = cursor.fetchall()
            
            all_korean_words = set()  # 중복 제거를 위한 set
            
            for row in rows:
                content = row['colorsize_comments_jp']
                # 한글 정규표현식으로 단어 추출
                korean_parts = re.findall(r'[가-힣]+', content)
                for word in korean_parts:
                    all_korean_words.add(word)
            
            print(f"--- 총 {len(rows)}개의 행에서 {len(all_korean_words)}개의 고유한 한국어 단어 추출 완료 ---\n")
            
            # 사전순 정렬 후 출력
            sorted_words = sorted(list(all_korean_words))
            for word in sorted_words:
                print(word)
                    
    except Exception as e:
        print(f"오류 발생: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    extract_korean()
