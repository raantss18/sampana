#!/usr/bin/env python3
"""Sampana — authentification unique par mot de passe maitre.

Caddy interroge /auth/verify avant chaque requete (forward_auth). Si la session
est absente ou expiree, on renvoie une 302 vers /auth/login, que Caddy relaie
telle quelle au navigateur.

Pourquoi un cookie plutot que Basic Auth : un cookie est porte par le NOM DE
DOMAINE, sans tenir compte du port. Une seule connexion couvre donc a la fois
https://hote/ et https://hote:10443/, alors que Basic Auth redemanderait le mot
de passe sur chaque port (origines differentes).

Stdlib pure. Ecoute sur 127.0.0.1 uniquement.

Usage : auth.py <auth.json> <port> <host>
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
import urllib.parse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COOKIE = "sampana_session"
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


def make_token(key: bytes, ttl: int) -> str:
    payload = str(int(time.time()) + ttl).encode()
    sig = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{base64.urlsafe_b64encode(payload).decode()}.{base64.urlsafe_b64encode(sig).decode()}"


def check_token(key: bytes, token: str) -> bool:
    try:
        raw, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(raw)
        expected = hmac.new(key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, base64.urlsafe_b64decode(sig)):
            return False
        return int(payload) > time.time()
    except Exception:  # noqa: BLE001 - un jeton illisible est simplement invalide
        return False


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sampana</title><link rel="icon" href="/logo.svg" type="image/svg+xml">
<style>
:root{{--accent:#38bdf8;--bg:#0d1117;--surface:#161b22;--line:#2a3441;--txt:#e6edf3;--dim:#8b98a9;--err:#f85149}}
@media(prefers-color-scheme:light){{:root{{--bg:#f6f8fa;--surface:#fff;--line:#d8dee4;--txt:#1f2328;--dim:#5b6673;--err:#cf222e}}}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--txt);
font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;padding:20px}}
form{{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:32px;width:100%;max-width:360px;
box-shadow:0 8px 30px rgba(0,0,0,.14)}}
.brand{{display:flex;align-items:center;gap:11px;margin-bottom:6px}}
.brand b{{font-size:20px;letter-spacing:-.3px}}
p.sub{{color:var(--dim);font-size:13px;margin:0 0 22px}}
label{{display:block;font-size:13px;font-weight:600;margin-bottom:6px}}
input{{width:100%;padding:10px 12px;background:var(--bg);color:var(--txt);border:1px solid var(--line);
border-radius:8px;font:inherit}}
input:focus{{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}}
button{{width:100%;margin-top:16px;padding:10px;background:var(--accent);color:#04212e;border:0;border-radius:8px;
font:inherit;font-weight:650;cursor:pointer}}
button:hover{{filter:brightness(1.07)}}
.err{{color:var(--err);font-size:13px;margin-top:14px}}
</style></head><body>
<form method="post" action="/auth/login">
  <div class="brand">
    <svg width="26" height="26" viewBox="0 0 32 32" fill="none" style="color:var(--accent)">
      <path d="M16 30V17" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>
      <path d="M16 17L7 8M16 17V6M16 17l9-9" stroke="currentColor" stroke-width="2.4"
            stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="7" cy="7" r="3" fill="currentColor"/><circle cx="16" cy="5" r="3" fill="currentColor"/>
      <circle cx="25" cy="7" r="3" fill="currentColor"/></svg>
    <b>Sampana</b>
  </div>
  <p class="sub">Une seule connexion pour tous les services.</p>
  <input type="hidden" name="next" value="{next}">
  <label for="p">Mot de passe maître</label>
  <input id="p" name="password" type="password" autofocus autocomplete="current-password" required>
  <button type="submit">Se connecter</button>
  {error}
</form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    conf: dict = {}
    host: str = ""
    protocol_version = "HTTP/1.1"

    # ── utilitaires ────────────────────────────────────────────────

    def _send(self, code: int, body: bytes = b"", headers: dict | None = None) -> None:
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authed(self) -> bool:
        raw = self.headers.get("Cookie")
        if not raw:
            return False
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw)
        except cookies.CookieError:
            return False
        morsel = jar.get(COOKIE)
        key = base64.b64decode(self.conf["key"])
        return bool(morsel and check_token(key, morsel.value))

    def _login_redirect(self) -> None:
        # Caddy relaie telle quelle toute reponse non-2xx de forward_auth.
        # L'URL d'origine est reconstruite depuis les en-tetes poses par Caddy.
        proto = self.headers.get("X-Forwarded-Proto", "https")
        host = self.headers.get("X-Forwarded-Host", self.host)
        uri = self.headers.get("X-Forwarded-Uri", "/")
        nxt = urllib.parse.quote(f"{proto}://{host}{uri}", safe="")
        self._send(302, b"", {"Location": f"https://{self.host}/auth/login?next={nxt}"})

    # ── routes ─────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802 - impose par BaseHTTPRequestHandler
        path = urllib.parse.urlparse(self.path).path

        if path == "/auth/verify":
            self._send(204) if self._authed() else self._login_redirect()

        elif path == "/auth/login":
            if self._authed():
                self._send(302, b"", {"Location": f"https://{self.host}/"})
                return
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            nxt = (q.get("next") or [f"https://{self.host}/"])[0]
            page = LOGIN_PAGE.format(next=nxt.replace('"', "&quot;"), error="")
            self._send(200, page.encode(), {"Content-Type": "text/html; charset=utf-8"})

        elif path == "/auth/logout":
            self._send(302, b"", {
                "Location": f"https://{self.host}/auth/login",
                "Set-Cookie": f"{COOKIE}=; Domain={self.host}; Path=/; Max-Age=0; "
                              "Secure; HttpOnly; SameSite=Lax",
            })
        else:
            self._send(404)

    def do_POST(self) -> None:  # noqa: N802
        if urllib.parse.urlparse(self.path).path != "/auth/login":
            self._send(404)
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        password = (form.get("password") or [""])[0]
        nxt = (form.get("next") or [f"https://{self.host}/"])[0]

        salt = base64.b64decode(self.conf["salt"])
        expected = base64.b64decode(self.conf["hash"])
        if not hmac.compare_digest(hash_password(password, salt), expected):
            # Cout constant : ralentit un peu les tentatives repetees.
            time.sleep(1.0)
            page = LOGIN_PAGE.format(
                next=nxt.replace('"', "&quot;"),
                error='<div class="err">Mot de passe incorrect.</div>',
            )
            self._send(401, page.encode(), {"Content-Type": "text/html; charset=utf-8"})
            return

        key = base64.b64decode(self.conf["key"])
        ttl = int(self.conf.get("ttl", 30 * 24 * 3600))
        token = make_token(key, ttl)
        # Domain sans port : le cookie vaut donc aussi pour hote:10443, etc.
        self._send(302, b"", {
            "Location": nxt,
            "Set-Cookie": f"{COOKIE}={token}; Domain={self.host}; Path=/; "
                          f"Max-Age={ttl}; Secure; HttpOnly; SameSite=Lax",
        })

    def log_message(self, *args) -> None:
        """Silence : forward_auth genere une requete par requete utilisateur."""


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2

    conf_path, port, host = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    Handler.conf = json.loads(open(conf_path).read())
    Handler.host = host

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


def init_config(password: str) -> dict:
    """Genere la configuration : sel, empreinte scrypt et cle de signature."""
    salt = secrets.token_bytes(16)
    return {
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(hash_password(password, salt)).decode(),
        "key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "ttl": 30 * 24 * 3600,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--init":
        json.dump(init_config(sys.argv[2]), sys.stdout)
    else:
        sys.exit(main())
