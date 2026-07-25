#!/usr/bin/env python3
"""Sampana — portail invite, sans compte et sans persistance.

Deux mondes cohabitent sur la meme machine et ne doivent jamais se croiser :

  session normale  cookie sampana_session, mot de passe maitre, tous les
                   services, y compris un shell (ttyd). Reservee au tailnet.
  session invitee  cookie sampana_guest, simple code de classe, trois outils
                   en lecture/ecriture ephemere. Joignable depuis le LAN et,
                   si le Funnel est actif, depuis Internet.

L'etancheite repose sur deux choses, et il faut les deux :
  - deux noms de cookie distincts ;
  - deux CLES DE SIGNATURE distinctes. Sans cela, quiconque connait le code de
    classe pourrait forger un jeton accepte par /auth/verify, donc obtenir un
    shell. Le code de classe n'est pas un secret : il se dit a voix haute.

Le code n'est d'ailleurs pas une mesure de securite mais un filtre a robots :
une URL Funnel apparait dans les journaux de Certificate Transparency et se
fait scanner en quelques heures. Il ecarte le balayage automatique, rien de
plus — c'est l'isolation du conteneur qui protege la machine.

Stdlib pure. Ecoute sur 127.0.0.1 uniquement.

Usage : guest.py <guest.json> <port> <host>
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from collections import defaultdict
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COOKIE = "sampana_guest"
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}

# Meme raisonnement que dans auth.py : on ralentit, on ne verrouille jamais,
# sinon un tiers ferme la porte a toute la classe en echouant en boucle.
FAIL_WINDOW = 900
FAIL_MAX_DELAY = 8.0  # plus bas qu'en session normale : 20 etudiants tapent mal
SCRYPT_SLOTS = threading.Semaphore(4)

_fails: dict[str, list[float]] = defaultdict(list)
_fail_lock = threading.Lock()


def _prune(now: float) -> None:
    for ip in list(_fails):
        kept = [t for t in _fails[ip] if now - t < FAIL_WINDOW]
        if kept:
            _fails[ip] = kept
        else:
            del _fails[ip]


def fail_counts(ip: str, record: bool = False) -> int:
    with _fail_lock:
        now = time.time()
        _prune(now)
        if record:
            _fails[ip].append(now)
        return len(_fails.get(ip, []))


def clear_fails(ip: str) -> None:
    with _fail_lock:
        _fails.pop(ip, None)


# ── etat du mode invite ────────────────────────────────────────────
# Couper l'acces en retirant le site de Caddy imposerait un rechargement a
# chaque bascule. On garde donc le site en ecoute, et c'est CE service qui
# refuse : quand le mode est eteint, /guest/verify rejette tout et les jetons
# deja distribues cessent d'etre honores. L'effet est immediat, sans rechargement.
_state_path = ""
_state_lock = threading.Lock()


def read_state() -> dict:
    try:
        with open(_state_path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"enabled": False, "funnel": False}


def write_state(state: dict) -> None:
    with _state_lock:
        tmp = f"{_state_path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        # Le code de seance y figure en clair — il est fait pour etre dit a
        # voix haute, mais rien ne justifie de le laisser lisible par les
        # autres comptes de la machine, dont celui sous lequel tourne Caddy.
        os.chmod(tmp, 0o600)
        os.replace(tmp, _state_path)


def set_funnel(on: bool, ports: list[str]) -> tuple[bool, str]:
    """Ouvre ou ferme l'exposition Internet.

    `ports` contient des paires «public:local». Les deux different pour le
    portail : Tailscale n'accepte de publier que 443, 8443 et 10000, alors que
    le site invite ecoute sur 8081. Supposer les deux egaux publiait 10000 vers
    un 127.0.0.1:10000 ou rien n'ecoutait — le Funnel s'affichait actif et ne
    servait rien.

    Renvoie (succes, message). L'echec le plus courant est un tailnet ou le
    Funnel n'a jamais ete autorise : tailscale repond alors par une URL a
    visiter, qu'on remonte telle quelle a l'enseignant plutot que d'echouer
    en silence.
    """
    if on and not online():
        # Cas du partage de connexion en salle, sans Internet. Ce n'est pas une
        # panne : la seance doit s'ouvrir normalement, seule la publication
        # exterieure est impossible.
        return False, ("hors ligne — la seance est ouverte en salle, "
                       "mais elle n'est pas publiee sur Internet")

    msgs = []
    for pair in ports:
        public, _, local = pair.partition(":")
        local = local or public
        cmd = (["tailscale", "funnel", "--bg", f"--https={public}",
                f"http://127.0.0.1:{local}"] if on
               else ["tailscale", "funnel", f"--https={public}", "off"])
        port = public
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"tailscale injoignable : {e}"
        if r.returncode != 0:
            return False, (r.stderr or r.stdout).strip()
        msgs.append(port)
    return True, ("ports publies : " if on else "ports fermes : ") + ", ".join(msgs)


# ── feuille de presence ────────────────────────────────────────────
# Le portail est interroge par Caddy AVANT chaque requete d'un invite
# (forward_auth). Il voit donc tout le trafic sans rien installer chez
# l'etudiant : la presence et l'outil actif se deduisent de ce flux.
#
# Consequence : ecrire sur disque a chaque requete saturerait les E/S. Les
# sessions vivent en memoire et sont vidangees periodiquement, plus une fois
# a la fermeture de la seance — moment ou la perte serait irrattrapable.
IDLE_AFTER = 180  # sans requete depuis 3 min, l'etudiant est considere inactif
FLUSH_EVERY = 20  # secondes entre deux ecritures sur disque

_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()
_sessions_path = ""
_history_path = ""
_latex_helper = ""
_last_flush = 0.0

MOIS = ["janv", "fevr", "mars", "avr", "mai", "juin",
        "juil", "aout", "sept", "oct", "nov", "dec"]


def new_room_credentials() -> tuple[str, str, str]:
    """Identifiant de seance, code a dicter et mot de passe LaTeX Lab.

    Le code porte la date pour etre reconnaissable a l'oral, mais se termine
    par des chiffres TIRES AU SORT. Une valeur purement calculee depuis la date
    serait devinable par quiconque connait la regle — et le portail est
    joignable depuis Internet des que le Funnel est ouvert.

    Le mot de passe LaTeX Lab evite «invite» et «sampana» : Overleaf refuse
    tout mot de passe contenant un fragment de l'adresse du compte.
    """
    now = time.localtime()
    stamp = f"{now.tm_mday}{MOIS[now.tm_mon - 1]}"
    code = f"{stamp}-{secrets.randbelow(9000) + 1000}"
    password = f"atelier-{stamp}-{secrets.randbelow(9000) + 1000}"
    room = time.strftime("%Y-%m-%dT%H:%M", now)
    return room, code, password


def archive_room(room: str, code: str) -> None:
    """Deplace les sessions de la seance courante vers l'historique.

    Appele a la fermeture du mode invite. Sans cela, la feuille de presence
    d'un cours serait ecrasee par le suivant et le suivi ulterieur demande
    n'aurait aucune matiere.
    """
    rows = session_view(room)
    if not rows:
        return
    try:
        with open(_history_path) as fh:
            hist = json.load(fh)
    except (OSError, ValueError):
        hist = {"rooms": []}

    hist.setdefault("rooms", []).insert(0, {
        "room": room,
        "code": code,
        "closedAt": time.time(),
        "count": len(rows),
        "students": rows,
    })
    hist["rooms"] = hist["rooms"][:200]  # on garde les 200 dernieres seances

    try:
        tmp = f"{_history_path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(hist, fh, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _history_path)
    except OSError:
        return

    with _sessions_lock:
        for sid in [k for k, v in _sessions.items() if v.get("room") == room]:
            del _sessions[sid]
    flush_sessions(force=True)


def tool_from_request(host: str, uri: str) -> str:
    """Nom lisible de l'outil vise par une requete.

    LaTeX Lab et l'assistant occupent la racine d'un port a eux : leur URI ne
    porte aucun prefixe distinctif, seul le port les identifie.
    """
    port = host.rpartition(":")[2]
    by_port = {"8443": "LaTeX Lab", "10002": "Assistant IA"}
    if port in by_port:
        return by_port[port]
    if uri.startswith("/guest/jupyter"):
        return "JupyterLab"
    if uri.startswith("/guest/lean"):
        return "Lean4Web"
    if uri.startswith("/guest/partage"):
        return "Fichiers partagés"
    return "Tableau de bord"


def load_sessions() -> None:
    global _sessions
    try:
        with open(_sessions_path) as fh:
            data = json.load(fh)
        with _sessions_lock:
            _sessions = data.get("sessions", {})
    except (OSError, ValueError):
        _sessions = {}


def flush_sessions(force: bool = False) -> None:
    global _last_flush
    now = time.time()
    if not force and now - _last_flush < FLUSH_EVERY:
        return
    _last_flush = now
    with _sessions_lock:
        payload = {"sessions": _sessions}
    try:
        tmp = f"{_sessions_path}.tmp"
        with open(tmp, "w") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        # Donnees nominatives d'etudiants : lisibles par leur seul proprietaire.
        os.chmod(tmp, 0o600)
        os.replace(tmp, _sessions_path)
    except OSError:
        pass  # une vidange ratee ne doit jamais interrompre une seance


def open_session(sid: str, first: str, last: str, ip: str, room: str) -> None:
    with _sessions_lock:
        _sessions[sid] = {
            "first": first, "last": last, "ip": ip, "room": room,
            "started": time.time(), "lastSeen": time.time(),
            "tool": "Tableau de bord",
            "timeline": [{"t": time.time(), "tool": "Tableau de bord"}],
        }
    flush_sessions(force=True)


def touch_session(sid: str, tool: str) -> bool:
    """Enregistre une requete. Renvoie False si la session est inconnue."""
    with _sessions_lock:
        s = _sessions.get(sid)
        if s is None:
            return False
        now = time.time()
        # Une entree n'est ajoutee qu'au CHANGEMENT d'outil, ou apres une
        # inactivite : sinon la chronologie compterait des milliers de lignes
        # pour une heure de travail et ne dirait plus rien.
        gap = now - s["lastSeen"]
        if tool != s.get("tool") or gap > IDLE_AFTER:
            s["timeline"].append({"t": now, "tool": tool, "afterIdle": gap > IDLE_AFTER})
            s["tool"] = tool
        s["lastSeen"] = now
    flush_sessions()
    return True


def session_view(room: str | None = None) -> list[dict]:
    """Sessions triees, avec l'etat actif/inactif calcule a la lecture."""
    now = time.time()
    with _sessions_lock:
        rows = [dict(s, id=sid) for sid, s in _sessions.items()
                if room is None or s.get("room") == room]
    for r in rows:
        r["idle"] = (now - r["lastSeen"]) > IDLE_AFTER
        r["idleSeconds"] = int(now - r["lastSeen"])
        r["durationSeconds"] = int(r["lastSeen"] - r["started"])
    rows.sort(key=lambda r: (r["last"].lower(), r["first"].lower()))
    return rows


def set_latex_password(email: str, password: str) -> tuple[bool, str]:
    """Change le mot de passe du compte LaTeX Lab partage.

    LaTeX Lab (Overleaf) gere ses comptes lui-meme : le mot de passe ne peut
    pas etre simplement declare ici, il faut le poser dans sa base. Le script
    est recopie dans le conteneur a chaque appel, car une mise a jour
    d'Overleaf le recree et emporterait tout ce qui y avait ete depose.

    Un echec n'interrompt pas l'ouverture de la seance : les autres outils
    restent utilisables, et l'enseignant est prevenu que ce mot de passe-la
    n'a pas change.
    """
    if not email or not _latex_helper:
        return False, "rotation non configuree"
    try:
        r = subprocess.run([_latex_helper, email, password],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"LaTeX Lab injoignable : {e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:300]
    return True, "mot de passe LaTeX Lab renouvele"


# ── dossier partage ────────────────────────────────────────────────
_share_dir = ""
MAX_UPLOAD = 200 * 2**20


def safe_filename(name: str) -> str:
    """Nom de fichier accepte, ou chaine vide.

    On ne garde que le nom de base : un `name` valant «../../etc/passwd»
    ecrirait hors du dossier partage. Les fichiers caches sont refuses, ils
    n'ont rien a faire dans un depot de cours et masqueraient leur presence.
    """
    name = name.strip()
    # On REFUSE tout nom contenant un separateur plutot que de le tronquer.
    # `basename` seul suffisait a contenir «../../etc/passwd» dans le dossier,
    # mais acceptait la requete en silence : mieux vaut la rejeter, un nom
    # pareil ne vient jamais d'un depot legitime.
    if not name or "/" in name or "\\" in name or ".." in name:
        return ""
    if name.startswith("."):  # fichier cache : masquerait sa presence
        return ""
    return name[:120]


def list_share() -> list[dict]:
    if not _share_dir:
        return []
    out = []
    try:
        for entry in os.scandir(_share_dir):
            if entry.name.startswith("."):
                continue
            st = entry.stat()
            out.append({
                "name": entry.name,
                "size": st.st_size,
                "modified": st.st_mtime,
                "dir": entry.is_dir(),
            })
    except OSError:
        return []
    out.sort(key=lambda f: (not f["dir"], f["name"].lower()))
    return out


# ── gestion des outils ─────────────────────────────────────────────
# Le tableau de bord peut ajouter, retirer et masquer des services. Les champs
# sont valides STRICTEMENT : ce formulaire finit dans un Caddyfile, et un
# `upstream` libre permettrait d'y injecter n'importe quelle directive.
_services_path = ""
_apply_cmd = "/usr/local/lib/sampana/apply.sh"

RE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}$")
RE_UPSTREAM = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}:\d{1,5}$|^localhost:\d{1,5}$")


def read_services() -> dict:
    with open(_services_path) as fh:
        return json.load(fh)


def write_services(cfg: dict) -> None:
    tmp = f"{_services_path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, _services_path)


def validate_tool(body: dict, cfg: dict) -> tuple[dict | None, str]:
    """Construit un service a partir du formulaire, ou explique le refus."""
    tid = str(body.get("id", "")).strip().lower()
    if not RE_ID.match(tid):
        return None, "Identifiant invalide (minuscules, chiffres et tirets)."

    used = {s["id"] for g in cfg.get("groups", []) for s in g["services"]}
    used |= {s["id"] for s in cfg.get("guest", {}).get("services", [])}
    if tid in used:
        return None, f"L'identifiant « {tid} » est déjà pris."

    upstream = str(body.get("upstream", "")).strip()
    if not RE_UPSTREAM.match(upstream):
        return None, "Adresse locale attendue, par exemple 127.0.0.1:9000."

    label = str(body.get("label", "")).strip()[:60] or tid
    svc = {
        "id": tid,
        "label": label,
        "desc": str(body.get("desc", "")).strip()[:160],
        "icon": "box",
        "upstream": upstream,
    }

    if body.get("route") == "port":
        try:
            port = int(body.get("port", 0))
        except (TypeError, ValueError):
            return None, "Port invalide."
        if not (1024 <= port <= 65535):
            return None, "Le port doit être compris entre 1024 et 65535."
        if port == int(upstream.rsplit(":", 1)[1]):
            return None, "Le port public doit différer de celui du service."
        taken = {s.get("port") for g in cfg.get("groups", []) for s in g["services"]}
        taken |= {s.get("port") for s in cfg.get("guest", {}).get("services", [])}
        if port in taken:
            return None, f"Le port {port} est déjà utilisé."
        svc.update(route="port", port=port, embed=False)
    else:
        svc.update(route="path", path=f"/{tid}", trailing_slash=True)

    return svc, ""


def run_apply() -> tuple[bool, str]:
    """Regenere et recharge Caddy via le helper privilegie."""
    try:
        r = subprocess.run(["sudo", "-n", _apply_cmd],
                           capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"application impossible : {e}"
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()[:400]
    return True, (r.stdout or "").strip()


def lan_ips() -> list[str]:
    """Adresses par lesquelles les etudiants peuvent joindre la machine.

    L'astuce habituelle — ouvrir une socket UDP vers une adresse externe pour
    que le noyau revele l'interface de sortie — echoue precisement dans le cas
    qui compte le plus : un partage de connexion sans Internet, ou il n'y a
    aucune route par defaut. On enumere donc les interfaces.

    Les adresses de hotspot passent en tete : quand l'enseignant partage sa
    connexion, c'est par la que la classe arrive. Tailscale, Docker et Podman
    sont ecartes — les dicter enverrait les etudiants dans le vide.
    """
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []

    found: list[tuple[int, str]] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface, addr = parts[1], parts[3].split("/")[0]
        if iface.startswith(("tailscale", "docker", "br-", "veth", "podman", "cni")):
            continue
        # 10.42/16 est la plage du partage de connexion de NetworkManager,
        # 10.0.0/24 celle d'Android. Elles priment sur un LAN ordinaire.
        rank = 0 if addr.startswith(("10.42.", "10.0.0.")) else 1
        found.append((rank, addr))

    found.sort()
    return [a for _, a in found]


def lan_ip() -> str:
    ips = lan_ips()
    return ips[0] if ips else ""


def online() -> bool:
    """Y a-t-il une route vers Internet ?

    Sans elle, `tailscale funnel` met des dizaines de secondes a echouer. On
    veut ouvrir la seance immediatement en salle, meme hors ligne : la classe
    n'a pas a attendre un service qui, de toute facon, ne peut pas repondre.
    """
    try:
        r = subprocess.run(["ip", "route", "show", "default"],
                           capture_output=True, text=True, timeout=5)
        return bool(r.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        return False


def hash_code(code: str, salt: bytes) -> bytes:
    # Le code est normalise : dicte a l'oral, il sera tape avec des majuscules
    # aleatoires et des espaces parasites.
    return hashlib.scrypt(code.strip().lower().encode(), salt=salt, **SCRYPT)


def make_token(key: bytes, ttl: int, sid: str) -> str:
    """Jeton signe portant l'identifiant de session et son expiration.

    Le jeton transportait auparavant la seule expiration, ce qui rendait tous
    les invites indistinguables. Il porte desormais un identifiant : c'est lui
    qui relie une requete a un etudiant nomme, donc qui rend la feuille de
    presence possible. L'identifiant n'est pas un secret — la signature reste
    ce qui empeche de le fabriquer.
    """
    payload = f"{sid}:{int(time.time()) + ttl}".encode()
    sig = hmac.new(key, payload, hashlib.sha256).digest()
    return (
        f"{base64.urlsafe_b64encode(payload).decode()}."
        f"{base64.urlsafe_b64encode(sig).decode()}"
    )


def check_token(key: bytes, token: str) -> str | None:
    """Renvoie l'identifiant de session si le jeton est valide, sinon None."""
    try:
        raw, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(raw)
        expected = hmac.new(key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, base64.urlsafe_b64decode(sig)):
            return None
        sid, _, exp = payload.decode().rpartition(":")
        if not sid or int(exp) <= time.time():
            return None
        return sid
    except Exception:  # noqa: BLE001 - un jeton illisible est simplement invalide
        return None


STYLE = """
:root{--accent:#38bdf8;--guest:#a78bfa;--bg:#0d1117;--surface:#161b22;
--line:#2a3441;--txt:#e6edf3;--dim:#8b98a9;--err:#f85149}
@media(prefers-color-scheme:light){:root{--bg:#f6f8fa;--surface:#fff;
--line:#d8dee4;--txt:#1f2328;--dim:#5b6673;--err:#cf222e}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);
color:var(--txt);font:15px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;padding:20px}
.wrap{width:100%;max-width:620px}
.brand{display:flex;align-items:center;gap:11px;margin-bottom:6px;justify-content:center}
.brand b{font-size:22px;letter-spacing:-.3px}
p.sub{color:var(--dim);font-size:13.5px;margin:0 0 26px;text-align:center}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:560px){.cards{grid-template-columns:1fr}}
a.card{display:block;text-decoration:none;color:inherit;background:var(--surface);
border:1px solid var(--line);border-radius:14px;padding:22px;transition:.15s}
a.card:hover{border-color:var(--accent);transform:translateY(-2px)}
a.card.guest:hover{border-color:var(--guest)}
.card h2{margin:0 0 6px;font-size:16.5px}
.card p{margin:0;color:var(--dim);font-size:13px}
.tag{display:inline-block;font-size:11px;font-weight:650;padding:2px 8px;border-radius:99px;
margin-bottom:10px;background:color-mix(in srgb,var(--accent) 18%,transparent);color:var(--accent)}
.tag.g{background:color-mix(in srgb,var(--guest) 18%,transparent);color:var(--guest)}
form{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:32px}
label{display:block;font-size:13px;font-weight:600;margin-bottom:6px}
input{width:100%;padding:10px 12px;background:var(--bg);color:var(--txt);
border:1px solid var(--line);border-radius:8px;font:inherit}
input:focus{outline:none;border-color:var(--guest);
box-shadow:0 0 0 3px color-mix(in srgb,var(--guest) 22%,transparent)}
button{width:100%;margin-top:16px;padding:10px;background:var(--guest);color:#1a0b3d;
border:0;border-radius:8px;font:inherit;font-weight:650;cursor:pointer}
button:hover{filter:brightness(1.07)}
.err{color:var(--err);font-size:13px;margin-top:14px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:460px){.row{grid-template-columns:1fr}}
.notice{margin:18px 0 0;padding-top:14px;border-top:1px dashed var(--line);
color:var(--dim);font-size:12px;line-height:1.6}
.back{display:block;text-align:center;margin-top:18px;color:var(--dim);font-size:13px}
.note{margin-top:22px;color:var(--dim);font-size:12.5px;text-align:center;line-height:1.7}
"""

LOGO = """<svg width="28" height="28" viewBox="0 0 32 32" fill="none" style="color:var(--accent)">
<path d="M16 30V17" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>
<path d="M16 17L7 8M16 17V6M16 17l9-9" stroke="currentColor" stroke-width="2.4"
stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="7" cy="7" r="3" fill="currentColor"/><circle cx="16" cy="5" r="3" fill="currentColor"/>
<circle cx="25" cy="7" r="3" fill="currentColor"/></svg>"""

PAGE = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><link rel="icon" href="/logo.svg" type="image/svg+xml">
<style>{style}</style></head><body><div class="wrap">{body}</div></body></html>"""

DISABLED = """
<div class="brand">{logo}<b>Sampana</b></div>
<p class="sub">Le mode invité est actuellement fermé.</p>
<form onsubmit="return false">
  <p style="margin:0;color:var(--dim);font-size:13.5px;line-height:1.7">
    Cet espace n'est ouvert que pendant les séances.<br>
    Adressez-vous à votre enseignant.
  </p>
</form>
"""

ENTER = """
<div class="brand">{logo}<b>Mode invité</b></div>
<p class="sub">Identifiez-vous pour accéder aux outils.</p>
<form method="post" action="/guest/enter">
  <div class="row">
    <div>
      <label for="f">Prénom</label>
      <input id="f" name="first" type="text" autofocus autocomplete="given-name"
             maxlength="40" required>
    </div>
    <div>
      <label for="l">Nom</label>
      <input id="l" name="last" type="text" autocomplete="family-name"
             maxlength="40" required>
    </div>
  </div>
  <label for="c" style="margin-top:14px">Code de la séance</label>
  <input id="c" name="code" type="text" autocomplete="off"
         autocapitalize="none" spellcheck="false" required>
  <button type="submit">Entrer</button>
  {error}
  <p class="notice">Votre nom, votre heure de connexion et les outils que vous
  ouvrez sont enregistrés par l'enseignant pour la feuille de présence et le
  suivi de la séance.</p>
</form>
"""


class Handler(BaseHTTPRequestHandler):
    conf: dict = {}
    conf_path: str = ""
    host: str = ""
    funnel_ports: list[str] = []
    guest_port: str = "8081"
    latex_email: str = ""
    latex_password: str = ""
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

    def _page(self, code: int, title: str, body: str) -> None:
        out = PAGE.format(title=title, style=STYLE, body=body)
        self._send(code, out.encode(), {"Content-Type": "text/html; charset=utf-8"})

    def _client_ip(self) -> str:
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[-1].strip()
        return self.client_address[0]

    def _origin(self) -> str:
        """Origine par laquelle l'invite nous joint.

        Elle varie : https://hote.ts.net:10000 par le Funnel, http://192.168.1.x
        en salle. On ne peut donc pas coder l'URL en dur comme le fait auth.py,
        et il faut se fier a l'en-tete Host — que Caddy transmet tel quel.
        """
        proto = self.headers.get("X-Forwarded-Proto", "http")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        return f"{proto}://{host}"

    def _sid(self) -> str | None:
        """Identifiant de session porte par le cookie, s'il est valide."""
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
        return check_token(key, morsel.value)

    def _expiry(self) -> int:
        """Instant d'expiration du jeton courant, 0 s'il n'y en a pas.

        Le cookie est HttpOnly : le navigateur ne peut pas le lire pour
        calculer lui-meme le temps restant. C'est donc au serveur de le dire,
        ce qui alimente les preavis de 10 et 5 minutes.
        """
        raw = self.headers.get("Cookie") or ""
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw)
        except cookies.CookieError:
            return 0
        morsel = jar.get(COOKIE)
        if not morsel:
            return 0
        try:
            payload = base64.urlsafe_b64decode(morsel.value.split(".", 1)[0])
            return int(payload.decode().rpartition(":")[2])
        except Exception:  # noqa: BLE001
            return 0

    def _authed(self) -> bool:
        return self._sid() is not None

    def _cookie_header(self, token: str, ttl: int) -> str:
        # Pas d'attribut Domain : le cookie reste lie a l'hote exact. Les cookies
        # ignorent le port, il couvre donc aussi :8443 (LaTeX Lab invite) sur le
        # meme hote — c'est ce qui evite de redemander le code d'un outil a l'autre.
        #
        # Secure est conditionnel : en salle l'acces est en HTTP simple (aucun
        # certificat valable pour une IP privee), et un cookie Secure y serait
        # tout bonnement ignore. Consequence assumee : sur le LAN, le code de
        # seance circule en clair. Il est public par nature.
        secure = "Secure; " if self._origin().startswith("https") else ""
        return f"{COOKIE}={token}; Path=/; Max-Age={ttl}; {secure}HttpOnly; SameSite=Lax"

    # ── routes ─────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802 - impose par BaseHTTPRequestHandler
        path = urllib.parse.urlparse(self.path).path
        enabled = bool(read_state().get("enabled"))

        # ── cote enseignant ────────────────────────────────────────
        # Ces routes sont servies par le site ADMIN, derriere le mot de passe
        # maitre (forward_auth). Elles ne sont pas joignables depuis le site
        # invite : celui-ci ne proxifie que /guest/*.
        if path == "/guest-admin/tools":
            try:
                cfg = read_services()
            except (OSError, ValueError) as e:
                self._json(500, {"error": str(e)})
                return
            self._json(200, {
                "admin": [
                    {"id": s["id"], "label": s["label"], "group": g["name"],
                     "route": s.get("route"), "hidden": bool(s.get("hidden"))}
                    for g in cfg.get("groups", []) for s in g["services"]
                ],
                "guest": [
                    {"id": s["id"], "label": s["label"], "route": s.get("route")}
                    for s in cfg.get("guest", {}).get("services", [])
                ],
            })
            return

        if path == "/guest-admin/files":
            self._json(200, {"files": list_share(), "dir": _share_dir})
            return

        if path == "/guest-admin/sessions":
            st = read_state()
            self._json(200, {"sessions": session_view(st.get("room"))})
            return

        if path == "/guest-admin/history":
            # Seances archivees, pour un suivi apres coup.
            try:
                with open(_history_path) as fh:
                    self._json(200, json.load(fh))
            except (OSError, ValueError):
                self._json(200, {"rooms": []})
            return

        if path == "/guest-admin/state":
            st = read_state()
            self._json(200, {
                "enabled": bool(st.get("enabled")),
                "funnel": bool(st.get("funnel")),
                "code": st.get("code", ""),
                "ttlMinutes": int(self.conf.get("ttl", 7200)) // 60,
                # Le tableau de bord construit les consignes a dicter : il lui
                # faut le nom public, que lui seul ne peut pas deviner.
                "host": self.host,
                "guestPort": self.guest_port,
                # L'adresse a dicter en salle. Elle ne peut pas etre deduite de
                # location.hostname cote navigateur : un enseignant qui ouvre
                # son tableau de bord par Tailscale y lirait le nom .ts.net, qui
                # ne sert pas le portail invite. Seul le serveur connait l'IP
                # locale de la machine.
                "defaultTtlMinutes": st.get("defaultTtlMinutes", 240),
                "lanIp": lan_ip(),
                # Toutes les adresses joignables : en partage de connexion, la
                # machine en a souvent deux (hotspot + reseau de l'ecole) et
                # l'enseignant doit pouvoir dicter la bonne.
                "lanIps": lan_ips(),
                "online": online(),
                # Repris de la configuration : le tableau de bord enseignant
                # doit pouvoir dicter les identifiants du compte partage.
                "latexEmail": self.latex_email,
                # Celui de l'etat, renouvele a chaque ouverture de seance. La
                # valeur passee en argument n'est qu'un repli pour une
                # installation ou la rotation n'a jamais tourne.
                "latexPassword": st.get("latexPassword") or self.latex_password,
            })
            return

        if path == "/guest/verify":
            # forward_auth : 204 laisse passer, tout le reste est relaye au client.
            # C'est aussi le point de mesure de la presence : Caddy appelle cette
            # route pour CHAQUE requete d'un invite.
            sid = self._sid() if enabled else None
            if sid is None:
                self._send(302, b"", {"Location": f"{self._origin()}/"})
                return
            tool = tool_from_request(
                self.headers.get("X-Forwarded-Host", ""),
                self.headers.get("X-Forwarded-Uri", "/"),
            )
            if not touch_session(sid, tool):
                # Jeton signe valide mais session inconnue : la seance a ete
                # fermee et archivee entre-temps. On renvoie a l'accueil.
                self._send(302, b"", {"Location": f"{self._origin()}/"})
                return
            self._send(204)

        elif path == "/guest/remaining":
            # Consomme par le tableau de bord invite et par l'enveloppe des
            # outils : preavis de fin, et identifiants du compte LaTeX Lab.
            exp = self._expiry()
            out = {
                "secondsLeft": max(0, int(exp - time.time())) if exp else 0,
                "enabled": enabled,
            }
            # Le mot de passe LaTeX Lab change a chaque seance : le figer dans
            # un fichier statique afficherait celui de la fois precedente. Il
            # n'est communique qu'a une session valide.
            if self._sid() is not None:
                st = read_state()
                out["latexEmail"] = self.latex_email
                out["latexPassword"] = st.get("latexPassword") or self.latex_password
            self._json(200, out)

        elif path in ("/", "/index.html", "/guest/enter"):
            # Plus de page de choix : un etudiant saisit son code et arrive
            # directement sur les outils. Le choix «session normale» n'avait
            # de sens que pour l'enseignant, qui passe par son propre tableau
            # de bord.
            if not enabled:
                self._page(403, "Sampana", DISABLED.format(logo=LOGO))
            elif self._authed():
                self._send(302, b"", {"Location": f"{self._origin()}/guest/"})
            else:
                self._page(200, "Mode invité", ENTER.format(logo=LOGO, error=""))

        elif path == "/guest/logout":
            self._send(302, b"", {
                "Location": f"{self._origin()}/",
                "Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
            })
        else:
            self._send(404)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(),
                   {"Content-Type": "application/json; charset=utf-8"})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length)
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path

        # ── cote enseignant (derriere le mot de passe maitre) ──────
        if path == "/guest-admin/toggle":
            body = self._body()
            want = bool(body.get("enabled"))
            st = read_state()

            if want:
                # Chaque ouverture est une SEANCE NEUVE : nouveau code, nouveau
                # mot de passe LaTeX Lab, nouvelle cle de signature. Cette
                # derniere est ce qui empeche un jeton de la seance precedente
                # de servir a la suivante.
                room, code, password = new_room_credentials()
                ttl = max(600, min(int(body.get("ttlMinutes", 120)), 24 * 60) * 60)

                salt = secrets.token_bytes(16)
                self.conf["salt"] = base64.b64encode(salt).decode()
                self.conf["hash"] = base64.b64encode(hash_code(code, salt)).decode()
                self.conf["key"] = base64.b64encode(secrets.token_bytes(32)).decode()
                self.conf["ttl"] = ttl
                try:
                    with open(self.conf_path, "w") as fh:
                        json.dump(self.conf, fh)
                    os.chmod(self.conf_path, 0o600)
                except OSError as e:
                    self._json(500, {"error": f"ecriture de la configuration : {e}"})
                    return

                ok_pw, msg_pw = set_latex_password(self.latex_email, password)
                st.update(room=room, code=code, ttlMinutes=ttl // 60,
                          latexPassword=password if ok_pw else st.get("latexPassword", ""),
                          latexPasswordOk=ok_pw, latexPasswordMsg=msg_pw)

            # Ordre volontaire : a l'ouverture on publie APRES avoir active,
            # a la fermeture on depublie AVANT de desactiver. Aucun instant
            # ou le Funnel pointe vers un portail qui accepte encore des codes
            # alors qu'on croit avoir ferme.
            if want:
                st["enabled"] = True
                write_state(st)
                ok, msg = set_funnel(True, self.funnel_ports)
                st["funnel"] = ok
                write_state(st)
            else:
                ok, msg = set_funnel(False, self.funnel_ports)
                # Archiver AVANT d'effacer l'etat : c'est le champ `room` qui
                # designe les sessions a conserver.
                archive_room(st.get("room", ""), st.get("code", ""))
                st["funnel"] = False
                st["enabled"] = False
                write_state(st)

            self._json(200, {
                "enabled": bool(st.get("enabled")),
                "funnel": bool(st.get("funnel")),
                "funnelOk": ok,
                "message": msg,
                "code": st.get("code", ""),
                "latexPassword": st.get("latexPassword", ""),
                "latexPasswordOk": st.get("latexPasswordOk", True),
                "latexPasswordMsg": st.get("latexPasswordMsg", ""),
                "ttlMinutes": st.get("ttlMinutes", 120),
            })
            return

        if path in ("/guest-admin/tool-add", "/guest-admin/tool-remove",
                    "/guest-admin/tool-hide"):
            body = self._body()
            try:
                cfg = read_services()
            except (OSError, ValueError) as e:
                self._json(500, {"error": str(e)})
                return

            # `guest` decide de la liste visee. Les deux sont volontairement
            # separees : un service du tableau de bord est protege par le mot
            # de passe maitre, un service invite ne l'est pas. Une case a
            # cocher qui melangerait les deux serait un piege.
            for_guest = bool(body.get("guest"))

            if path == "/guest-admin/tool-add":
                svc, err = validate_tool(body, cfg)
                if err:
                    self._json(400, {"error": err})
                    return
                if for_guest:
                    if svc["route"] == "path":
                        svc["path"] = f"/guest/{svc['id']}"
                        svc["strip"] = True
                    cfg.setdefault("guest", {}).setdefault("services", []).append(svc)
                else:
                    groups = cfg.setdefault("groups", [])
                    dest = next((g for g in groups if g["name"] == "Ajouts"), None)
                    if dest is None:
                        dest = {"name": "Ajouts", "services": []}
                        groups.append(dest)
                    svc["embed"] = svc.get("embed", True)
                    dest["services"].append(svc)

            elif path == "/guest-admin/tool-remove":
                tid = str(body.get("id", ""))
                if for_guest:
                    lst = cfg.get("guest", {}).get("services", [])
                    cfg["guest"]["services"] = [s for s in lst if s["id"] != tid]
                else:
                    for g in cfg.get("groups", []):
                        g["services"] = [s for s in g["services"] if s["id"] != tid]
                    cfg["groups"] = [g for g in cfg["groups"] if g["services"]]

            else:  # tool-hide
                tid = str(body.get("id", ""))
                for g in cfg.get("groups", []):
                    for s in g["services"]:
                        if s["id"] == tid:
                            s["hidden"] = bool(body.get("hidden"))

            try:
                write_services(cfg)
            except OSError as e:
                self._json(500, {"error": str(e)})
                return

            ok, msg = run_apply()
            self._json(200 if ok else 500, {"ok": ok, "message": msg})
            return

        if path in ("/guest-admin/upload", "/guest-admin/delete"):
            # Depot et retrait des fichiers partages. Ces routes ne sont
            # atteignables que par le site principal, donc derriere le mot de
            # passe maitre : le portail invite ne proxifie que /guest/*.
            if not _share_dir:
                self._json(500, {"error": "dossier partage non configure"})
                return
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = safe_filename((q.get("name") or [""])[0])
            if not name:
                self._json(400, {"error": "nom de fichier invalide"})
                return
            dest = os.path.join(_share_dir, name)

            if path == "/guest-admin/delete":
                try:
                    os.remove(dest)
                except OSError as e:
                    self._json(400, {"error": str(e)})
                    return
                self._json(200, {"deleted": name})
                return

            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0 or length > MAX_UPLOAD:
                self._json(413, {"error": f"taille invalide (max {MAX_UPLOAD // 2**20} Mo)"})
                return
            try:
                # Ecriture par blocs : un fichier de 200 Mo ne doit pas etre
                # charge en memoire d'un seul tenant.
                with open(dest, "wb") as fh:
                    left = length
                    while left > 0:
                        chunk = self.rfile.read(min(65536, left))
                        if not chunk:
                            break
                        fh.write(chunk)
                        left -= len(chunk)
            except OSError as e:
                self._json(500, {"error": str(e)})
                return
            self._json(200, {"saved": name, "size": length})
            return

        if path == "/guest-admin/defaults":
            minutes = int(self._body().get("ttlMinutes", 240))
            st = read_state()
            st["defaultTtlMinutes"] = max(10, min(minutes, 24 * 60))
            write_state(st)
            self._json(200, {"defaultTtlMinutes": st["defaultTtlMinutes"]})
            return

        if path == "/guest-admin/code":
            code = str(self._body().get("code", "")).strip()
            if len(code) < 4:
                self._json(400, {"error": "Code trop court (4 caractères minimum)."})
                return
            salt = secrets.token_bytes(16)
            self.conf["salt"] = base64.b64encode(salt).decode()
            self.conf["hash"] = base64.b64encode(hash_code(code, salt)).decode()
            # Nouvelle cle de signature : changer le code doit invalider les
            # sessions en cours, sinon un etudiant parti garde son acces.
            self.conf["key"] = base64.b64encode(secrets.token_bytes(32)).decode()
            with open(self.conf_path, "w") as fh:
                json.dump(self.conf, fh)
            os.chmod(self.conf_path, 0o600)
            st = read_state()
            st["code"] = code
            write_state(st)
            self._json(200, {"code": code})
            return

        if path != "/guest/enter":
            self._send(404)
            return

        if not read_state().get("enabled"):
            self._page(403, "Sampana", DISABLED.format(logo=LOGO))
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        form = urllib.parse.parse_qs(self.rfile.read(length).decode())
        code = (form.get("code") or [""])[0]
        first = (form.get("first") or [""])[0].strip()[:40]
        last = (form.get("last") or [""])[0].strip()[:40]
        ip = self._client_ip()

        if not first or not last:
            self._page(400, "Mode invité", ENTER.format(
                logo=LOGO, error='<div class="err">Prénom et nom sont requis.</div>'))
            return

        seen = fail_counts(ip)
        if seen:
            time.sleep(min(2.0 ** (seen - 1), FAIL_MAX_DELAY))

        salt = base64.b64decode(self.conf["salt"])
        expected = base64.b64decode(self.conf["hash"])
        with SCRYPT_SLOTS:
            candidate = hash_code(code, salt)
        if not hmac.compare_digest(candidate, expected):
            fail_counts(ip, record=True)
            self._page(401, "Mode invité", ENTER.format(
                logo=LOGO, error='<div class="err">Code incorrect.</div>'))
            return

        clear_fails(ip)
        key = base64.b64decode(self.conf["key"])
        ttl = int(self.conf.get("ttl", 2 * 3600))
        sid = secrets.token_urlsafe(9)
        open_session(sid, first, last, ip, read_state().get("room", ""))
        self._send(302, b"", {
            "Location": f"{self._origin()}/guest/",
            "Set-Cookie": self._cookie_header(make_token(key, ttl, sid), ttl),
        })

    def log_message(self, *args) -> None:
        """Silence : forward_auth genere une requete par requete utilisateur."""


def init_config(code: str, ttl: int = 2 * 3600) -> dict:
    """Sel, empreinte du code et cle de signature PROPRE au mode invite.

    La cle est tiree independamment de celle de auth.json : c'est elle qui
    empeche un jeton invite d'etre accepte comme session normale.
    """
    salt = secrets.token_bytes(16)
    return {
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(hash_code(code, salt)).decode(),
        "key": base64.b64encode(secrets.token_bytes(32)).decode(),
        "ttl": ttl,
    }


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2

    global _state_path, _sessions_path, _history_path, _latex_helper, _share_dir
    global _services_path
    conf_path, port, host = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    _state_path = sys.argv[4] if len(sys.argv) > 4 else (
        os.path.dirname(conf_path) + "/guest-state.json")
    base = os.path.dirname(_state_path)
    _sessions_path = f"{base}/guest-sessions.json"
    _history_path = f"{base}/guest-history.json"
    _latex_helper = os.path.expanduser("~/.local/share/sampana/set-latex-password.sh")
    if not os.path.exists(_latex_helper):
        _latex_helper = ""
    _share_dir = os.environ.get("SAMPANA_SHARE_DIR", "")
    _services_path = os.environ.get("SAMPANA_SERVICES", "")
    load_sessions()
    Handler.funnel_ports = (sys.argv[5].split(",") if len(sys.argv) > 5 else [])
    Handler.guest_port = sys.argv[6] if len(sys.argv) > 6 else "8081"
    Handler.latex_email = sys.argv[7] if len(sys.argv) > 7 else ""
    Handler.latex_password = sys.argv[8] if len(sys.argv) > 8 else ""

    Handler.conf = json.loads(open(conf_path).read())
    Handler.conf_path = conf_path
    Handler.host = host

    # Au demarrage le mode invite est FERME, quoi qu'il se soit passe avant.
    # Un redemarrage (mise a jour, coupure de courant) ne doit jamais rouvrir
    # tout seul un acces sans mot de passe.
    st = read_state()
    st["enabled"] = False
    st["funnel"] = False
    write_state(st)

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--init":
        ttl = int(sys.argv[3]) if len(sys.argv) > 3 else 2 * 3600
        json.dump(init_config(sys.argv[2], ttl), sys.stdout)
    else:
        sys.exit(main())
