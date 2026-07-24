#!/usr/bin/env python3
"""Sonde chaque service local et expose son etat en JSON.

Ecoute sur 127.0.0.1 uniquement ; Caddy l'expose sous /api/status.
Stdlib pure : aucune dependance a installer, aucun venv.

Usage : health.py <targets.json> [port]
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TIMEOUT = 2.5
CACHE_TTL = 10.0  # les sondes coutent cher, le dashboard rafraichit souvent

_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()


def probe(target: dict) -> dict:
    host, _, port_s = target["upstream"].partition(":")
    port = int(port_s)
    started = time.perf_counter()

    result = {"id": target["id"], "label": target["label"], "status": "down"}

    try:
        if target.get("scheme") == "https":
            ctx = ssl._create_unverified_context()
            conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        conn.request("GET", target.get("probe") or "/")
        resp = conn.getresponse()
        code = resp.status
        conn.close()

        expected = target.get("expect") or []
        # 401 sur un service protege, 3xx sur une redirection de login : le
        # service repond, donc il est bien vivant.
        ok = code in expected if expected else (code < 500)
        result["status"] = "up" if ok else "degraded"
        result["code"] = code
    except (socket.timeout, TimeoutError):
        result["error"] = "timeout"
    except ConnectionRefusedError:
        result["error"] = "connexion refusee"
    except OSError as exc:
        result["error"] = type(exc).__name__
    except Exception as exc:  # noqa: BLE001 - on veut un statut, jamais une trace
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["ms"] = round((time.perf_counter() - started) * 1000)
    return result


def snapshot(targets: list[dict]) -> dict:
    with _lock:
        now = time.time()
        if _cache["data"] is not None and now - _cache["at"] < CACHE_TTL:
            return _cache["data"]

    with ThreadPoolExecutor(max_workers=min(12, len(targets) or 1)) as pool:
        services = list(pool.map(probe, targets))

    data = {
        "generated_at": int(time.time()),
        "up": sum(1 for s in services if s["status"] == "up"),
        "total": len(services),
        "services": services,
    }
    with _lock:
        _cache.update(at=time.time(), data=data)
    return data


class Handler(BaseHTTPRequestHandler):
    targets: list[dict] = []
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - impose par BaseHTTPRequestHandler
        body = json.dumps(snapshot(self.targets)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """Silence : une sonde toutes les 15 s noierait le journal."""


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    Handler.targets = json.loads(open(sys.argv[1]).read())
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8089

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"sampana-health sur 127.0.0.1:{port} — {len(Handler.targets)} services",
          flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
