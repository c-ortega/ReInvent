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
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

clientId = "7vmom55m1qstvq8i71ph127bfq"
authorizeUrl = "https://oauth.awsevents.com/oauth2/authorize"
tokenUrl = "https://oauth.awsevents.com/oauth2/token"
callbackPort = 8484
redirectUri = f"http://localhost:{callbackPort}/callback"
tokenFile = Path(__file__).resolve().parent / "data/tokens.json"
pendingAuth: dict[str, str] = {}
callbackServer: ThreadingHTTPServer | None = None
serverStarted = 0.0
authLock = threading.RLock()


def saveTokens(data: dict) -> None:
    tokenFile.parent.mkdir(exist_ok=True)
    existing = loadTokens()
    record = {
        "accessToken": data["access_token"],
        "refreshToken": data.get("refresh_token") or existing.get("refreshToken"),
        "expiresAt": time.time() + max(0, int(data.get("expires_in", 3600)) - 60),
    }
    if os.name == "posix":
        fd = os.open(tokenFile.with_suffix(".tmp"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream)
        os.replace(tokenFile.with_suffix(".tmp"), tokenFile)
    else:
        tokenFile.with_suffix(".tmp").write_text(json.dumps(record))
        os.replace(tokenFile.with_suffix(".tmp"), tokenFile)


def loadTokens() -> dict:
    try:
        stored = json.loads(tokenFile.read_text())
        # Read token files written by the previous release without forcing sign-in.
        return {
            "accessToken": stored.get("accessToken") or stored.get("access_token"),
            "refreshToken": stored.get("refreshToken") or stored.get("refresh_token"),
            "expiresAt": stored.get("expiresAt", stored.get("expires_at", 0)),
        }
    except (OSError, ValueError):
        return {}


def isSignedIn() -> bool:
    return bool(loadTokens().get("refreshToken") or loadTokens().get("accessToken"))


def signOut() -> None:
    with authLock:
        tokenFile.unlink(missing_ok=True)


def accessToken() -> str | None:
    with authLock:
        current = loadTokens()
        if current.get("accessToken") and time.time() < current.get("expiresAt", 0):
            return current["accessToken"]
        if not current.get("refreshToken"):
            return None
        response = httpx.post(tokenUrl, data={
            "grant_type": "refresh_token", "client_id": clientId,
            "refresh_token": current["refreshToken"],
        }, timeout=20)
        if response.status_code in (400, 401):
            signOut()
            raise RuntimeError("AWS sign-in expired. Sign in again.")
        response.raise_for_status()
        saveTokens(response.json())
        return loadTokens()["accessToken"]


class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # HTTP query contains an authorization code; never log it.

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        params = parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        code = params.get("code", [""])[0]
        with authLock:
            verifier = pendingAuth.pop(state, None)
        if not verifier or not code:
            self.showPage("Sign-in failed. Return to the planner and try again.", 400)
            return
        try:
            response = httpx.post(tokenUrl, data={
                "grant_type": "authorization_code", "client_id": clientId,
                "redirect_uri": redirectUri, "code": code,
                "code_verifier": verifier,
            }, timeout=20)
            response.raise_for_status()
            with authLock:
                saveTokens(response.json())
        except (httpx.HTTPError, ValueError, KeyError):
            self.showPage("AWS could not complete sign-in. Return to the planner and try again.", 502)
            return
        self.showPage("Signed in. Return to the planner tab and press Refresh catalog.")

    def showPage(self, message: str, code: int = 200):
        body = ("<!doctype html><html><meta charset='utf-8'><title>re:Invent Planner</title>"
                f"<body style='font:18px system-ui;margin:4rem'>{message}</body></html>").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def beginSignIn() -> str:
    global callbackServer, serverStarted
    with authLock:
        if callbackServer is None or time.time() - serverStarted > 900:
            if callbackServer is not None:
                callbackServer.shutdown()
                callbackServer.server_close()
            try:
                callbackServer = ThreadingHTTPServer(("127.0.0.1", callbackPort), CallbackHandler)
            except OSError as exc:
                raise RuntimeError("Port 8484 is in use. Close the other application and try again.") from exc
            threading.Thread(target=callbackServer.serve_forever, daemon=True).start()
            serverStarted = time.time()
        pendingAuth.clear()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        pendingAuth[state] = verifier
        return authorizeUrl + "?" + urlencode({
            "response_type": "code", "client_id": clientId,
            "redirect_uri": redirectUri, "scope": "openid email events/access",
            "identity_provider": "AWSBuilderID", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": state,
        })
