import asyncio
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

CONFIG_PATH = Path(os.environ.get("SW_AI_CONFIG", str(Path(__file__).with_name(".ai_settings.json"))))
COOKIE = "swai_session"
SESSION_SECONDS = 8 * 3600


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()


def write_config(path, value):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".swai-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class Security:
    def __init__(self, path=CONFIG_PATH):
        self.path = Path(path)
        if not self.path.is_file():
            raise RuntimeError("Сначала выполните setup_ai.py в каталоге backend")
        self.config = json.loads(self.path.read_text(encoding="utf-8"))
        if not self.config.get("password_hash") or not self.config.get("password_salt"):
            raise RuntimeError("Не настроен пароль приложения. Выполните setup_ai.py")
        os.chmod(self.path, 0o600)
        self.sessions = {}
        self.failures = {}

    def session(self, request):
        token = request.cookies.get(COOKIE, "")
        key = hashlib.sha256(token.encode()).hexdigest()
        item = self.sessions.get(key)
        if not item or item["expires"] <= time.time():
            self.sessions.pop(key, None)
            return None
        return item

    def register(self, app):
        @app.middleware("http")
        async def authentication(request: Request, call_next):
            path = request.url.path
            if path.startswith("/api/ai/") and path != "/api/ai/auth/login":
                session = self.session(request)
                if not session:
                    return JSONResponse({"detail": "Требуется вход"}, status_code=401)
                request.state.ai_session = session
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    csrf = request.headers.get("X-CSRF-Token", "")
                    if not hmac.compare_digest(csrf, session["csrf"]):
                        return JSONResponse({"detail": "Недействительный CSRF token"}, status_code=403)
            if path.startswith("/api/ai/") and request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if origin and urlsplit(origin).netloc != request.headers.get("host"):
                    return JSONResponse({"detail": "Недопустимый источник запроса"}, status_code=403)
            response = await call_next(request)
            if path.startswith(("/api/ai/", "/ai/")):
                response.headers["Cache-Control"] = "no-store"
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["Referrer-Policy"] = "no-referrer"
                response.headers["X-Frame-Options"] = "DENY"
            if path == "/ai/":
                response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            return response

        @app.post("/api/ai/auth/login")
        async def login(body: Login, request: Request):
            now = time.time()
            identity = request.client.host if request.client else "local"
            recent = [t for t in self.failures.get(identity, []) if now - t < 60]
            if len(recent) >= 5:
                raise HTTPException(429, "Слишком много попыток. Подождите минуту.")
            computed = await asyncio.to_thread(password_hash, body.password, self.config["password_salt"])
            if not (hmac.compare_digest(computed, self.config["password_hash"]) and hmac.compare_digest(body.username.encode(), self.config["username"].encode())):
                self.failures[identity] = recent + [now]
                raise HTTPException(401, "Неверный логин или пароль")
            self.failures.pop(identity, None)
            self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
            if len(self.sessions) >= 100:
                self.sessions.pop(next(iter(self.sessions)))
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = {"csrf": csrf, "expires": now + SESSION_SECONDS, "id": secrets.token_hex(16)}
            response = JSONResponse({"username": self.config["username"], "csrf_token": csrf})
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=os.environ.get("SW_AI_COOKIE_SECURE") == "1", max_age=SESSION_SECONDS, path="/")
            return response

        @app.get("/api/ai/auth/session")
        async def session(request: Request):
            return {"username": self.config["username"], "csrf_token": request.state.ai_session["csrf"]}

        @app.post("/api/ai/auth/logout")
        async def logout(request: Request):
            token = request.cookies.get(COOKIE, "")
            self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
            response = JSONResponse({"status": "ok"})
            response.delete_cookie(COOKIE, path="/")
            return response
