# -*- coding: utf-8 -*-
"""manage_server 세션 기반 비밀번호 인증.

흐름:
  - .env: MANAGE_PASSWORD, MANAGE_SECRET_KEY
  - 미인증 상태로 보호 라우트 접근 → /login 으로 redirect (?next= 보존)
  - /login POST: 비밀번호 일치 시 session['authenticated']=True
  - 세션 만료 30분 (PERMANENT_SESSION_LIFETIME)
"""
import os
from datetime import timedelta
from functools import wraps

from flask import (Flask, redirect, render_template, request, session, url_for)


SESSION_LIFETIME_MINUTES = 30


def configure_auth(app: Flask) -> None:
    """app에 세션 설정 적용. SECRET_KEY는 .env에서 가져옴."""
    secret = os.getenv('MANAGE_SECRET_KEY', '')
    if not secret:
        raise RuntimeError("환경변수 MANAGE_SECRET_KEY 가 설정되지 않았습니다.")
    app.config['SECRET_KEY'] = secret
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=SESSION_LIFETIME_MINUTES)
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'


def require_login(f):
    """보호 라우트에 적용. 미인증 시 /login 으로 redirect."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('authenticated'):
            return redirect(url_for('login', next=request.full_path if request.query_string else request.path))
        # 매 요청마다 만료 시간 리셋
        session.permanent = True
        return f(*args, **kwargs)
    return wrapper


def register_auth_routes(app: Flask) -> None:
    """/login, /logout 라우트 등록."""

    @app.route('/manage/login', methods=['GET', 'POST'])
    def login():
        password_env = os.getenv('MANAGE_PASSWORD', '')
        if not password_env:
            return render_template('login.html',
                                   error='서버에 MANAGE_PASSWORD 가 설정되지 않았습니다. 관리자에게 문의하세요.'), 500
        if request.method == 'POST':
            pw = (request.form.get('password') or '').strip()
            if pw and pw == password_env:
                session['authenticated'] = True
                session.permanent = True
                next_url = request.args.get('next') or '/manage'
                # open redirect 방지: 같은 사이트 경로만 허용
                if not next_url.startswith('/'):
                    next_url = '/manage'
                return redirect(next_url)
            return render_template('login.html', error='비밀번호가 올바르지 않습니다.'), 401
        return render_template('login.html', error=None)

    @app.route('/manage/logout')
    def logout():
        session.pop('authenticated', None)
        return redirect(url_for('login'))
