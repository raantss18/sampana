#!/usr/bin/env python3
"""Determine, par mesure, quels services acceptent d'etre affiches en iframe.

A lancer apres avoir declare un service, et a chaque mise a jour d'une app :
les en-tetes d'encadrement changent d'une version a l'autre.

Deux pieges que ce script evite :
  - il faut regarder X-Frame-Options ET la directive frame-ancestors de la CSP ;
    un service peut n'envoyer que l'un des deux ;
  - il faut suivre les redirections : une application peut autoriser
    l'encadrement sur sa page de connexion tout en le refusant sur la 302 qui y
    mene. C'est le cas d'Overleaf.

Usage : bin/check-embed.py [config/services.json]
"""
from __future__ import annotations

import http.client
import json
import re
import ssl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 6


def fetch_headers(host: str, port: int, path: str, https: bool, hops: int = 4):
    """Renvoie (code, headers) en suivant les redirections internes."""
    seen = []
    for _ in range(hops):
        if https:
            conn = http.client.HTTPSConnection(
                host, port, timeout=TIMEOUT, context=ssl._create_unverified_context()
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        conn.request("GET", path or "/")
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        code = resp.status
        conn.close()
        seen.append((code, headers))

        loc = headers.get("location", "")
        if code in (301, 302, 303, 307, 308) and loc.startswith("/"):
            path = loc
            continue
        break
    return seen


def verdict(hops) -> tuple[bool, str]:
    for code, h in hops:
        xfo = (h.get("x-frame-options") or "").strip()
        csp = h.get("content-security-policy") or ""
        m = re.search(r"frame-ancestors([^;]*)", csp, re.I)
        fa = (m.group(1).strip() if m else "")

        if xfo.upper() in ("DENY", "SAMEORIGIN"):
            return False, f"X-Frame-Options: {xfo} (HTTP {code})"
        if fa and "'none'" in fa:
            return False, f"frame-ancestors {fa} (HTTP {code})"
        if fa and "'self'" in fa and "*" not in fa:
            # 'self' = meme origine. Le dashboard etant sur le port 443, cela ne
            # marche que pour un service servi sous un sous-chemin.
            return True, f"frame-ancestors 'self' — OK uniquement en route \"path\""
    return True, "aucun en-tete restrictif"


def main() -> int:
    cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "config/services.json"
    cfg = json.loads(cfg_path.read_text())

    print(f"{'SERVICE':<16}{'DECLARE':<10}{'MESURE':<10}RAISON")
    print("-" * 78)

    changed = []
    for group in cfg.get("groups", []):
        for svc in group.get("services", []):
            # Un service masque n'a pas de carte, donc jamais d'iframe.
            if svc.get("hidden"):
                continue
            host, _, port_s = svc["upstream"].partition(":")
            https = svc.get("upstream_scheme", "http").startswith("https")
            probe = svc.get("open_path") or svc.get("probe") or "/"

            try:
                hops = fetch_headers(host, int(port_s), probe, https)
                ok, why = verdict(hops)
            except Exception as exc:  # noqa: BLE001
                print(f"{svc['id']:<16}{'?':<10}{'injoign.':<10}{type(exc).__name__}")
                continue

            declared = bool(svc.get("embed"))
            # Un service en 'self' n'est encadrable que sous un sous-chemin.
            if ok and "'self'" in why and svc.get("route") != "path":
                ok, why = False, why.replace("OK uniquement", "INCOMPATIBLE :")

            flag = "" if ok == declared else "   <-- A CORRIGER"
            if ok != declared:
                changed.append((svc["id"], ok))
            print(f"{svc['id']:<16}{str(declared):<10}{str(ok):<10}{why}{flag}")

    if changed:
        print("\nCorrections a porter dans", cfg_path)
        for sid, ok in changed:
            print(f'  "{sid}" : "embed": {str(ok).lower()}')
        return 1

    print("\nToutes les declarations `embed` correspondent a la mesure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
