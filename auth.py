from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

import httpx

CLIENT_ID = "7vmom55m1qstvq8i71ph127bfq"
AUTHORIZE_URL = "https://oauth.awsevents.com/oauth2/authorize"
TOKEN_URL = "https://oauth.awsevents.com/oauth2/token"
CALLBACK_PORT = 8484
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
TOKEN_FILE = Path(__file__).resolve().parent / "data/token.json"
_pending: dict[str, str] = {}
_server: ThreadingHTTPServer | None = None
_serverStarted = 0.0
_lock = threading.RLock()

def saveTokens(data: dict) -> None:
    TOKEN_FILE.parent.mkdir(exist_ok=True)
    existing = loadTokens()
    record = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token") or existing.get("refresh_token"), 
        "expires_at": time.time() + max(0, int(data.get("expires_in", 3600))- 60),
    }
    if os.name == "posix":
        fd = os.open(TOKEN_FILE.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream)
        os.replace(TOKEN_FILE.with_suffix(".tmp"), TOKEN_FILE)
    else:
        TOKEN_FILE.with_suffix(".tmp").write_text(json.dumps(record))
        os.replace(TOKEN_FILE.with_suffix(".tmp"), TOKEN_FILE)

def loadTokens() -> dict:
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (OSError, ValueError):
        return {}

def isSignedIn() -> bool:
    return bool(loadTokens().get("access_token") or loadTokens().get("refresh_token"))

def signOut() -> None:
    with _lock:
        TOKEN_FILE.unlink(missing_ok=True)

def accessToken() -> str | None:
    with _lock:
        current = loadTokens()
        if current.get("access_token") and time.time() < current.get("expires_at", 0):
            return current["access_token"]
        if not current.get("refresh_token"):
            return None
        response = httpx.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "client_id": CLIENT_ID,
            "refresh_token": current["refresh_token"]
        }, timeout=20)
        if response.status_code in (400, 401):
            signOut()
            raise RuntimeError("AWS sign in expired. Sign in again.")
        response.raise_for_status()
        saveTokens(response.json())
        return loadTokens().get("access_token")
    
class Callback(BaseHTTPRequestHandler):
    def logMessage(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        params = parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]
        with _lock:
            verifier = _pending.pop(state, None)
        if not verifier or not code:
            self._show("Sign-in failed. Return to the planner and try again.", 400)
            return
        try:
            response = httpx.post(TOKEN_URL, data={
                "grant_type": "authorization_code", "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI, "code": code,
                "code_verifier": verifier,
            }, timeout=20)
            response.raise_for_status()
            with _lock:
                saveTokens(response.json())
        except (httpx.HTTPError, ValueError, KeyError):
            self._show("AWS could not complete sign-in. Return to the planner and try again.", 502)
            return
        self._show("Signed in. Return to the planner tab and press Refresh catalog.")

    def _show(self, message: str, code: int = 200):
        body = ("<!doctype html><html><meta charset='utf-8'><title>re:Invent Planner</title>"
                f"<body style='font:18px system-ui;margin:4rem'>{message}</body></html>").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def beginSignIn() -> str:
    global _server, _server_started
    with _lock:
        if _server is None or time.time() - _server_started > 900:
            if _server is not None:
                _server.shutdown()
                _server.server_close()
            try:
                _server = ThreadingHTTPServer(("127.0.0.1", CALLBACK_PORT), Callback)
            except OSError as exc:
                raise RuntimeError("Port 8484 is in use. Close the other application and try again.") from exc
            threading.Thread(target=_server.serve_forever, daemon=True).start()
            _server_started = time.time()
        _pending.clear()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        _pending[state] = verifier
        return AUTHORIZE_URL + "?" + urlencode({
            "response_type": "code", "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI, "scope": "openid email events/access",
            "identity_provider": "AWSBuilderID", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": state,
        })
