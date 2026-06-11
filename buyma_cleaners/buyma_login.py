# -*- coding: utf-8 -*-
"""바이마 로그인 도구 — 사람이 직접 로그인해서 쿠키를 저장한다.

manage 페이지의 「로그인 도구 다운로드」로 받은 뒤, PC에서 더블클릭하거나
  python buyma_login.py
로 실행한다. 진짜 크롬 창이 열리면 바이마에 직접 로그인(캡차도 직접) 한다.
로그인되면 이 파일과 같은 폴더에 buyma_cookies.json 이 저장된다.
그 파일을 manage 페이지에서 「쿠키 업로드」 하면 된다.

필요: PC에 Python + playwright
  pip install playwright
  playwright install chromium
"""

import os
import sys
import json
import asyncio

if sys.platform == "win32":
    import io
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)
    except Exception:
        pass

COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buyma_cookies.json")
LOGIN_URL = "https://www.buyma.com/login/"


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("playwright 가 설치돼 있지 않습니다.")
        print("  설치:  pip install playwright   그리고   playwright install chromium")
        return 1

    print("브라우저(크롬)를 엽니다. 바이마에 직접 로그인한 뒤 마이페이지로 이동하세요...")
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=False)
        except Exception as e:
            print(f"브라우저 실행 실패: {e}")
            print("  'playwright install chromium' 이 필요할 수 있습니다.")
            return 1

        ctx = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="ja-JP")
        page = await ctx.new_page()
        await page.goto(LOGIN_URL)
        print("로그인 페이지가 열렸습니다. 로그인을 완료해주세요. (최대 5분 대기)")

        try:
            await page.wait_for_url("**/my/**", timeout=300000)
            print("로그인 확인!")
        except Exception:
            print("자동 감지 실패. 로그인을 완료했으면 이 창에서 Enter 를 눌러주세요.")
            try:
                input(">>> Enter: ")
            except EOFError:
                pass

        cookies = await ctx.cookies()
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        print("")
        print(f"쿠키 저장 완료: {COOKIE_FILE}  ({len(cookies)}개)")
        print("→ 이 파일을 manage 페이지에서 「쿠키 업로드」 하세요.")
        await browser.close()
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception as e:
        print(f"오류: {e}")
        rc = 1
    sys.exit(rc)
