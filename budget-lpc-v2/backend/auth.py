"""Optional single-owner sign-in for an HTTPS hosted deployment.

The local launcher remains local-only. Cloud startup fails closed without a
password hash. Session cookies contain random tokens, not financial records.
"""
import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

COOKIE = '__Host-budget-lpc-session'
TTL = 30 * 86400


def enabled():
    return os.environ.get('BUDGET_LPC_REQUIRE_AUTH', '0') == '1'


def password_hash(password):
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f'scrypt:{salt}:{value}'


def verify(password, encoded):
    try:
        algorithm, salt, expected = encoded.split(':')
        if algorithm != 'scrypt' or len(salt) != 32 or len(expected) != 128:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def initialize(database):
    if enabled():
        encoded = os.environ.get('BUDGET_LPC_PASSWORD_HASH', '')
        parts = encoded.split(':')
        if len(parts) != 3 or parts[0] != 'scrypt' or len(parts[1]) != 32 or len(parts[2]) != 128:
            raise RuntimeError('Hosted Budget LPC requires a valid BUDGET_LPC_PASSWORD_HASH. Run set_password.py to create one.')
    with database() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS owner_sessions(digest TEXT PRIMARY KEY,expires INTEGER NOT NULL,credential_version TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS login_failures(client TEXT NOT NULL,at INTEGER NOT NULL);
        ''')


def revision():
    return hashlib.sha256(os.environ.get('BUDGET_LPC_PASSWORD_HASH', '').encode()).hexdigest()


def authenticated(request, database):
    if not enabled():
        return True
    token = request.cookies.get(COOKIE, '')
    if not token:
        return False
    with database() as db:
        return bool(db.execute('SELECT 1 FROM owner_sessions WHERE digest=? AND expires>? AND credential_version=?', (hashlib.sha256(token.encode()).hexdigest(), int(time.time()), revision())).fetchone())


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=512)


def install(app, database):
    @app.middleware('http')
    async def protect(request, call_next):
        path = request.url.path
        protected = path.startswith('/api/') or path in ('/docs', '/redoc', '/openapi.json')
        public = path in ('/api/health', '/api/auth/session', '/api/auth/login')
        if protected and not public and not await run_in_threadpool(authenticated, request, database):
            return JSONResponse({'detail': 'Please sign in to Budget LPC'}, status_code=401, headers={'Cache-Control':'no-store'})
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if enabled():
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.get('/api/auth/session')
    def session(request: Request):
        return {'required': enabled(), 'authenticated': authenticated(request, database)}

    @app.post('/api/auth/login')
    def login(body: Login, request: Request):
        if not enabled():
            return {'authenticated': True}
        now = int(time.time())
        client = hashlib.sha256((request.client.host if request.client else 'unknown').encode()).hexdigest()
        with database() as db:
            db.execute('DELETE FROM login_failures WHERE at<?', (now-600,))
            attempts = db.execute('SELECT COUNT(*) FROM login_failures WHERE client=?', (client,)).fetchone()[0]
            total = db.execute('SELECT COUNT(*) FROM login_failures').fetchone()[0]
        if attempts >= 6 or total >= 30:
            raise HTTPException(429, 'Too many attempts. Please wait ten minutes and try again.')
        if not verify(body.password, os.environ.get('BUDGET_LPC_PASSWORD_HASH', '')):
            with database() as db:
                db.execute('INSERT INTO login_failures VALUES (?,?)', (client, now))
            raise HTTPException(401, 'Incorrect password')
        token = secrets.token_urlsafe(48)
        with database() as db:
            db.execute('DELETE FROM owner_sessions WHERE expires<=? OR credential_version!=?', (now,revision()))
            db.execute('DELETE FROM login_failures WHERE client=?', (client,))
            db.execute('INSERT INTO owner_sessions VALUES (?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), now+TTL, revision()))
        response = JSONResponse({'authenticated':True})
        response.set_cookie(COOKIE, token, max_age=TTL, secure=True, httponly=True, samesite='strict', path='/')
        response.headers['Cache-Control']='no-store'
        return response

    @app.post('/api/auth/logout')
    def logout(request: Request):
        token=request.cookies.get(COOKIE, '')
        with database() as db:
            db.execute('DELETE FROM owner_sessions WHERE digest=?', (hashlib.sha256(token.encode()).hexdigest(),))
        response=JSONResponse({'authenticated':False})
        response.delete_cookie(COOKIE, path='/', secure=True, httponly=True, samesite='strict')
        return response

