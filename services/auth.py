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
import html
import json
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import defaultdict
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COOKIE = "sampana_session"
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}

# ── limitation du debit ────────────────────────────────────────────
# Le formulaire peut etre joignable depuis Internet (Funnel). Le `sleep(1)`
# d'origine ne freinait rien : ThreadingHTTPServer traite les tentatives en
# parallele, donc mille essais simultanes coutaient une seconde au total.
#
# On tient un historique d'echecs par IP et un total global, avec un backoff
# exponentiel PLAFONNE et un verrou temporaire. Le plafond est deliberé : un
# verrouillage dur et illimite offrirait a un tiers un moyen simple de te
# fermer la porte en saturant le compteur.
# On ne verrouille JAMAIS sur un mot de passe correct. La tentation etait de
# repondre 429 avant meme de verifier, mais derriere Funnel toutes les requetes
# publiques arrivent de 127.0.0.1 et partagent donc un unique compteur : un tiers
# n'aurait eu qu'a echouer en boucle pour te fermer la porte a toi aussi.
# Le ralentissement est donc la seule sanction, et il precede la verification.
FAIL_WINDOW = 900  # un echec est oublie apres 15 min
FAIL_MAX_DELAY = 20.0  # backoff plafonne (secondes)

# scrypt coute ~16 Mo et ~100 ms. Sans borne, mille requetes paralleles
# suffiraient a saturer le CPU et la RAM de la machine — l'anti-brute-force
# deviendrait lui-meme le vecteur de deni de service. On seriealise.
SCRYPT_SLOTS = threading.Semaphore(4)

_fails: dict[str, list[float]] = defaultdict(list)
_fail_lock = threading.Lock()

# ── expiration par inactivite ──────────────────────────────────────
# Le jeton porte une expiration absolue (30 jours) : un poste laisse ouvert
# restait donc accessible tres longtemps. On y ajoute une fenetre glissante.
#
# Elle est tenue EN MEMOIRE et non dans le jeton : forward_auth ne permet pas
# de renvoyer un cookie rafraichi au navigateur sur une reponse 2xx, il faudrait
# donc reemettre le jeton par un detour a chaque requete. Un simple registre
# d'activite fait le meme travail sans toucher au cookie.
#
# Ce registre ne gouverne QUE l'acces au tableau de bord. Rien ne s'arrete
# quand il expire : le mode invite, les conteneurs et les seances en cours
# continuent — seule une nouvelle saisie du mot de passe est exigee.
_seen: dict[str, float] = {}
_seen_lock = threading.Lock()


REVOKED = 0.0  # sentinelle : jeton condamne par inactivite


def _token_expiry(token: str) -> float:
    """Expiration absolue inscrite dans le jeton, 0 si illisible."""
    try:
        return float(base64.urlsafe_b64decode(token.split(".", 1)[0]))
    except Exception:  # noqa: BLE001
        return 0.0


def touch(token: str, idle_limit: int) -> bool:
    """Enregistre une activite. False si la fenetre d'inactivite est depassee.

    Un jeton inconnu (redemarrage du service) demarre son compteur maintenant
    plutot que d'etre rejete : l'expiration absolue continue de s'appliquer, et
    redemander le mot de passe a chaque mise a jour serait gratuitement penible.

    Un jeton expire est MARQUE, pas efface. L'effacer le rendait a nouveau
    inconnu, donc valide a la requete suivante : le verrouillage ne tenait
    qu'un seul appel. Le menage se fie a l'expiration absolue du jeton, jamais
    a son inactivite, pour la meme raison.
    """
    now = time.time()
    with _seen_lock:
        last = _seen.get(token)
        if last == REVOKED:
            return False
        if last is not None and now - last > idle_limit:
            _seen[token] = REVOKED
            return False
        _seen[token] = now
        if len(_seen) > 500:
            for k in [k for k in _seen if _token_expiry(k) < now]:
                del _seen[k]
    return True


def forget(token: str) -> None:
    with _seen_lock:
        _seen.pop(token, None)


def _prune(now: float) -> None:
    for ip in list(_fails):
        kept = [t for t in _fails[ip] if now - t < FAIL_WINDOW]
        if kept:
            _fails[ip] = kept
        else:
            del _fails[ip]


def fail_counts(ip: str, record: bool = False) -> tuple[int, int]:
    """Renvoie (echecs de cette IP, echecs toutes IP confondues)."""
    with _fail_lock:
        now = time.time()
        _prune(now)
        if record:
            _fails[ip].append(now)
        return len(_fails.get(ip, [])), sum(len(v) for v in _fails.values())


def clear_fails(ip: str) -> None:
    with _fail_lock:
        _fails.pop(ip, None)


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
    conf_path: str = ""
    host: str = ""
    _addrs: set[str] = set()
    _addrs_at: float = 0.0
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

    @staticmethod
    def _machine_addrs() -> set[str]:
        """Adresses IPv4 de cette machine.

        Sert a valider l'en-tete Host : sans cette liste, accepter un Host
        quelconque rouvrirait l'open redirect corrige par ailleurs, tandis que
        le refuser systematiquement renverrait vers le nom .ts.net — injoignable
        en salle sans Internet.
        """
        now = time.time()
        if now - Handler._addrs_at < 60 and Handler._addrs:
            return Handler._addrs
        addrs = set()
        try:
            out = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) > 3:
                    addrs.add(parts[3].split("/")[0])
        except (OSError, subprocess.TimeoutExpired):
            pass
        Handler._addrs, Handler._addrs_at = addrs, now
        return addrs

    def _origin(self) -> str:
        """Origine par laquelle le client nous joint, apres validation.

        Le service renvoyait jusqu'ici vers `https://<nom .ts.net>` en dur. En
        salle, un enseignant arrivant par l'IP locale etait donc redirige vers
        une adresse que son poste ne peut pas resoudre sans Tailscale : le mode
        local etait inutilisable des la premiere redirection.

        On repart donc du Host reellement demande — mais en le validant contre
        le nom declare et les adresses de la machine, sinon un Host forge
        suffirait a detourner la redirection post-connexion.
        """
        host = (self.headers.get("X-Forwarded-Host")
                or self.headers.get("Host") or "").strip()
        nom, _, port = host.partition(":")

        # Le nom du tailnet n'est jamais servi qu'en TLS par `tailscale serve` :
        # on force https, sans quoi une redirection en http renverrait vers un
        # port que Caddy n'ecoute pas sur cette interface.
        if nom == self.host:
            return f"https://{host}"

        # Adresse locale : HTTP simple, aucun certificat n'etant valable pour
        # une IP privee.
        if nom in self._machine_addrs():
            proto = self.headers.get("X-Forwarded-Proto") or "http"
            return f"{proto}://{host}"
        # Host inconnu ou absent : on retombe sur le nom declare.
        return f"https://{self.host}"

    def _cookie(self, token: str, ttl: int) -> str:
        """En-tete Set-Cookie de la session.

        `Domain` sans port : le cookie vaut donc aussi pour hote:10443, etc.

        `Secure` est CONDITIONNEL. Le tableau de bord est desormais joignable
        depuis le reseau local, en HTTP simple — aucun certificat n'est valable
        pour une IP privee. Un cookie Secure y serait purement ignore par le
        navigateur : la connexion reussirait puis serait aussitot oubliee, et
        l'enseignant tournerait en rond sur la page de connexion.
        Via Tailscale et via le Funnel, la requete arrive en HTTPS et le
        drapeau est bien pose.
        """
        proto = self.headers.get("X-Forwarded-Proto", "")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        secure = "Secure; " if proto == "https" or host.endswith(".ts.net") else ""
        # Sur une adresse IP, `Domain` est refuse par les navigateurs : on
        # laisse alors le cookie lie a l'hote exact (les cookies ignorent le
        # port, il couvre donc quand meme les autres services).
        domain = f"Domain={self.host}; " if host.endswith(".ts.net") else ""
        return (f"{COOKIE}={token}; {domain}Path=/; Max-Age={ttl}; "
                f"{secure}HttpOnly; SameSite=Lax")

    def _token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw)
        except cookies.CookieError:
            return None
        morsel = jar.get(COOKIE)
        if not morsel:
            return None
        key = base64.b64decode(self.conf["key"])
        return morsel.value if check_token(key, morsel.value) else None

    def _authed(self, activity: bool = False) -> bool:
        """Session valide ?

        `activity` distingue une requete de l'utilisateur (qui repousse la
        fenetre d'inactivite) d'une simple consultation d'etat. Seul
        /auth/verify, appele par Caddy avant chaque requete reelle, la repousse.
        """
        token = self._token()
        if token is None:
            return False
        if not activity:
            return True
        idle = int(self.conf.get("idle", 15 * 60))
        return touch(token, idle) if idle > 0 else True

    def _client_ip(self) -> str:
        """IP du pair telle que vue par Caddy.

        Caddy AJOUTE l'adresse du pair a droite de X-Forwarded-For : les valeurs
        de gauche proviennent du client et sont donc falsifiables, la derniere
        ne l'est pas. On ne lit que celle-la.

        Limite connue : derriere Tailscale Funnel, tailscaled relaie depuis
        127.0.0.1 et toutes les requetes publiques partagent un seul compteur.
        C'est precisement le role de GLOBAL_LOCK_AT.
        """
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[-1].strip()
        return self.client_address[0]

    def _safe_next(self, raw: str) -> str:
        """Ne laisse passer qu'une destination interne.

        Sans ce filtre, /auth/login?next=https://evil.tld renvoie l'utilisateur
        chez un tiers juste apres qu'il a saisi son mot de passe maitre — et la
        page d'arrivee peut imiter Sampana pour le lui redemander.

        Les adresses de la machine sont acceptees au meme titre que le nom
        declare : en salle, sans Internet, c'est par elles qu'on arrive.
        """
        default = f"{self._origin()}/"
        if not raw:
            return default
        # Chemin relatif : sur. Sauf «//hote», qui est une URL protocole-relative.
        if raw.startswith("/") and not raw.startswith("//"):
            return raw
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return default
        # hostname ignore le port : c'est voulu, les services sont sur :10443 etc.
        if parsed.hostname == self.host and parsed.scheme == "https":
            return raw
        if parsed.hostname in self._machine_addrs():
            return raw
        return default

    def _login_redirect(self) -> None:
        # Caddy relaie telle quelle toute reponse non-2xx de forward_auth.
        # L'URL d'origine est reconstruite depuis les en-tetes poses par Caddy,
        # mais ces en-tetes proviennent de la requete du client : on les valide
        # au lieu de les recopier (sinon X-Forwarded-Host suffit a detourner la
        # redirection post-connexion).
        origine = self._origin()
        uri = self.headers.get("X-Forwarded-Uri", "/")
        if not uri.startswith("/") or uri.startswith("//"):
            uri = "/"
        nxt = urllib.parse.quote(f"{origine}{uri}", safe="")
        self._send(302, b"", {"Location": f"{origine}/auth/login?next={nxt}"})

    # ── routes ─────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802 - impose par BaseHTTPRequestHandler
        path = urllib.parse.urlparse(self.path).path

        if path == "/auth/verify":
            # Seul point appele par Caddy avant chaque requete : c'est donc ici
            # que se mesure l'activite.
            self._send(204) if self._authed(activity=True) else self._login_redirect()

        elif path == "/auth/config":
            # /auth/* est exclu du forward_auth (sinon la page de connexion se
            # protegerait elle-meme). Cette route verifie donc la session
            # elle-meme, sans quoi elle serait ouverte a tous.
            if not self._authed():
                self._send(403)
                return
            self._json(200, {
                "idleMinutes": int(self.conf.get("idle", 15 * 60)) // 60,
                "sessionDays": int(self.conf.get("ttl", 30 * 24 * 3600)) // 86400,
            })

        elif path == "/auth/login":
            if self._authed():
                self._send(302, b"", {"Location": f"{self._origin()}/"})
                return
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            nxt = self._safe_next((q.get("next") or [""])[0])
            page = LOGIN_PAGE.format(next=html.escape(nxt, quote=True), error="")
            self._send(200, page.encode(), {"Content-Type": "text/html; charset=utf-8"})

        elif path == "/auth/logout":
            tok = self._token()
            if tok:
                forget(tok)
            self._send(302, b"", {
                "Location": f"{self._origin()}/auth/login",
                "Set-Cookie": f"{COOKIE}=; Domain={self.host}; Path=/; Max-Age=0; "
                              "Secure; HttpOnly; SameSite=Lax",
            })
        else:
            self._send(404)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(),
                   {"Content-Type": "application/json; charset=utf-8"})

    def _save_conf(self) -> bool:
        try:
            with open(self.conf_path, "w") as fh:
                json.dump(self.conf, fh)
            os.chmod(self.conf_path, 0o600)
            return True
        except OSError:
            return False

    def _do_config(self, path: str) -> bool:
        """Routes de configuration. Renvoie True si la requete a ete traitee."""
        if path not in ("/auth/password", "/auth/config"):
            return False

        if not self._authed():
            self._send(403)
            return True

        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}

        if path == "/auth/config":
            idle = int(body.get("idleMinutes", 15))
            # 0 desactive l'expiration ; au-dela d'une journee elle ne protege
            # plus rien d'utile.
            self.conf["idle"] = max(0, min(idle, 1440)) * 60
            if not self._save_conf():
                self._json(500, {"error": "ecriture impossible"})
                return True
            self._json(200, {"idleMinutes": self.conf["idle"] // 60})
            return True

        # Changement de mot de passe : l'ancien est exige meme si la session est
        # valide. Sinon un poste laisse ouvert suffirait a verrouiller dehors le
        # proprietaire legitime.
        current = str(body.get("current", ""))
        new = str(body.get("new", ""))
        if len(new) < 8:
            self._json(400, {"error": "8 caractères minimum."})
            return True

        salt = base64.b64decode(self.conf["salt"])
        with SCRYPT_SLOTS:
            ok = hmac.compare_digest(hash_password(current, salt),
                                     base64.b64decode(self.conf["hash"]))
        if not ok:
            time.sleep(1.0)
            self._json(403, {"error": "Mot de passe actuel incorrect."})
            return True

        new_salt = secrets.token_bytes(16)
        with SCRYPT_SLOTS:
            self.conf["hash"] = base64.b64encode(hash_password(new, new_salt)).decode()
        self.conf["salt"] = base64.b64encode(new_salt).decode()
        # Cle renouvelee : toutes les autres sessions tombent. C'est le but —
        # on change souvent de mot de passe parce qu'on le croit compromis.
        self.conf["key"] = base64.b64encode(secrets.token_bytes(32)).decode()
        if not self._save_conf():
            self._json(500, {"error": "ecriture impossible"})
            return True

        # Le navigateur qui vient de faire le changement recoit un jeton neuf :
        # sans cela il se deconnecterait lui-meme dans la seconde.
        ttl = int(self.conf.get("ttl", 30 * 24 * 3600))
        token = make_token(base64.b64decode(self.conf["key"]), ttl)
        self._send(200, json.dumps({"ok": True}).encode(), {
            "Content-Type": "application/json; charset=utf-8",
            "Set-Cookie": self._cookie(token, ttl),
        })
        return True

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if self._do_config(path):
            return
        if path != "/auth/login":
            self._send(404)
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        password = (form.get("password") or [""])[0]
        nxt = self._safe_next((form.get("next") or [""])[0])
        ip = self._client_ip()

        # Le prix des echecs precedents se paie AVANT la verification : une
        # tentative automatisee est donc freinee des la deuxieme. Une connexion
        # legitime ne subit ce delai qu'une fois, puis repart de zero.
        mine, _ = fail_counts(ip)
        if mine:
            time.sleep(min(2.0 ** (mine - 1), FAIL_MAX_DELAY))

        salt = base64.b64decode(self.conf["salt"])
        expected = base64.b64decode(self.conf["hash"])
        with SCRYPT_SLOTS:
            candidate = hash_password(password, salt)
        if not hmac.compare_digest(candidate, expected):
            fail_counts(ip, record=True)
            page = LOGIN_PAGE.format(
                next=html.escape(nxt, quote=True),
                error='<div class="err">Mot de passe incorrect.</div>',
            )
            self._send(401, page.encode(), {"Content-Type": "text/html; charset=utf-8"})
            return

        clear_fails(ip)
        key = base64.b64decode(self.conf["key"])
        ttl = int(self.conf.get("ttl", 30 * 24 * 3600))
        token = make_token(key, ttl)
        # 303 : apres un POST, c'est le code qui demande au client de
        # repartir en GET, sans dependre de la tolerance des navigateurs.
        self._send(303, b"", {"Location": nxt, "Set-Cookie": self._cookie(token, ttl)})

    def log_message(self, *args) -> None:
        """Silence : forward_auth genere une requete par requete utilisateur."""


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2

    conf_path, port, host = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    Handler.conf = json.loads(open(conf_path).read())
    Handler.conf_path = conf_path
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
