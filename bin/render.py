#!/usr/bin/env python3
"""Genere le Caddyfile, le manifeste web et les commandes `tailscale serve`
a partir de config/sampana.env et config/services.json.

Aucune dependance : stdlib uniquement.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def flatten(cfg: dict) -> list[dict]:
    out = []
    for group in cfg.get("groups", []):
        for svc in group.get("services", []):
            svc = dict(svc)
            svc["group"] = group["name"]
            out.append(svc)
    return out


def auth_block(env: dict[str, str], exclude: bool = False) -> list[str]:
    """Bloc forward_auth interrogeant le service d'authentification.

    Toute reponse non-2xx du verificateur est relayee telle quelle au client :
    c'est ainsi que la 302 vers la page de connexion parvient au navigateur.

    `exclude` ajoute un matcher laissant passer les pages de connexion. C'est
    indispensable sur le site principal : Caddy applique forward_auth AVANT les
    blocs `handle`, donc sans exclusion /auth/login se protegerait elle-meme et
    le navigateur boucherait indefiniment sur la redirection.
    """
    if env.get("AUTH_ENABLED", "1") not in ("1", "true", "yes"):
        return []

    lines = []
    matcher = ""
    if exclude:
        lines.append("\t@protected not path /auth/* /logo.svg")
        matcher = "@protected "
    lines += [
        f"\tforward_auth {matcher}127.0.0.1:{env['AUTH_PORT']} {{",
        "\t\turi /auth/verify",
        "\t\tcopy_headers X-Forwarded-Uri X-Forwarded-Host X-Forwarded-Proto",
        "\t}",
        "",
    ]
    return lines


def caddyfile(env: dict[str, str], services: list[dict]) -> str:
    port = env["CADDY_PORT"]
    web_root = env["WEB_ROOT"]

    lines = [
        "# GENERE PAR sampana — ne pas editer a la main.",
        "# Source : config/sampana.env + config/services.json",
        "# Regenerer : ./install.sh",
        "",
        "{",
        "\t# L'API admin (127.0.0.1:2019) doit rester active : `systemctl reload",
        "\t# caddy` passe par elle. Avec `admin off`, tout reload echoue.",
        "\tauto_https off",
        "}",
        "",
        "# Pas de bloc `http://127.0.0.1:PORT` : Caddy filtrerait alors sur le Host,",
        "# or Tailscale transmet le nom .ts.net et toutes les requetes recevraient",
        "# un 200 vide. On ecoute sur le port, et `bind` restreint a la boucle locale.",
        f":{port} {{",
        "\tbind 127.0.0.1",
        "\tencode gzip",
        "",
        "\t# Pages de connexion : accessibles SANS session, sinon on ne pourrait",
        "\t# jamais s'authentifier. Doit venir avant le forward_auth.",
        "\thandle /auth/* {",
        f"\t\treverse_proxy 127.0.0.1:{env['AUTH_PORT']}",
        "\t}",
        "",
        "\t# Le logo est utilise par la page de connexion elle-meme.",
        "\thandle /logo.svg {",
        f"\t\troot * {web_root}",
        "\t\tfile_server",
        "\t}",
        "",
    ]
    lines += auth_block(env, exclude=True)
    lines += [
        "\t# Etat de sante agrege, consomme par le dashboard.",
        "\thandle /api/status {",
        f"\t\treverse_proxy 127.0.0.1:{env['HEALTH_PORT']}",
        "\t}",
        "",
    ]

    # Les routes 'strip' d'abord : un prefixe d'API doit gagner sur le prefixe
    # applicatif dont il partage le debut (/mi-saina-api vs /mi-saina).
    path_services = [s for s in services if s.get("route") == "path"]
    for svc in sorted(path_services, key=lambda s: (not s.get("strip"), s["path"])):
        p = svc["path"]
        if svc.get("strip"):
            lines += [
                f"\t# {svc['label']} — prefixe retire, le backend voit la racine.",
                f"\thandle_path {p}/* {{",
                f"\t\treverse_proxy {svc['upstream']}",
                "\t}",
                "",
            ]
        else:
            mid = svc["id"].replace("-", "_")
            lines += [
                f"\t# {svc['label']} — prefixe conserve (le backend est configure avec).",
                f"\t@{mid} path {p} {p}/*",
                f"\thandle @{mid} {{",
                f"\t\treverse_proxy {svc['upstream']}",
                "\t}",
                "",
            ]

    # Redirections vers le slash final, uniquement quand le backend l'exige.
    # Surtout pas pour Next.js, qui retire le slash : les deux boucleraient.
    for svc in path_services:
        if svc.get("trailing_slash"):
            lines.append(f"\tredir {svc['path']} {svc['path']}/ permanent")
    lines.append("")

    lines += [
        "\t# Dashboard et shell de navigation.",
        "\thandle {",
        f"\t\troot * {web_root}",
        "\t\tfile_server",
        "\t}",
        "}",
        "",
    ]

    # Chaque service « port » passe lui aussi par Caddy, sur la boucle locale et
    # sur le meme numero de port que celui expose publiquement. C'est ce qui
    # permet d'appliquer le mot de passe maitre partout : sans cela, Tailscale
    # proxifierait directement le service, hors de portee de forward_auth.
    for svc in services:
        if svc.get("route") != "port":
            continue
        up_port = int(svc["upstream"].rsplit(":", 1)[1])
        if up_port == svc["port"]:
            raise SystemExit(
                f"ERREUR : {svc['id']} — le port public ({svc['port']}) est identique "
                f"au port du backend. Choisis un autre port public."
            )
        lines += [
            f"# {svc['label']}",
            f":{svc['port']} {{",
            "\tbind 127.0.0.1",
        ]
        lines += auth_block(env)

        # `https+insecure` est une syntaxe propre a `tailscale serve`. Cote
        # Caddy, un backend en TLS auto-signe se declare via un bloc transport.
        if svc.get("upstream_scheme", "http").startswith("https"):
            lines += [
                f"\treverse_proxy https://{svc['upstream']} {{",
                "\t\ttransport http {",
                "\t\t\ttls",
                "\t\t\ttls_insecure_skip_verify",
                "\t\t}",
                "\t}",
            ]
        else:
            lines.append(f"\treverse_proxy {svc['upstream']}")
        lines += ["}", ""]

    return "\n".join(lines)


def serve_commands(env: dict[str, str], services: list[dict]) -> list[str]:
    cmds = [f"tailscale serve --bg --https=443 http://127.0.0.1:{env['CADDY_PORT']}"]
    for svc in services:
        if svc.get("route") != "port":
            continue
        # On pointe vers Caddy (meme numero de port, sur la boucle locale), et
        # non vers le backend : c'est Caddy qui applique le mot de passe maitre.
        cmds.append(
            f"tailscale serve --bg --https={svc['port']} "
            f"http://127.0.0.1:{svc['port']}"
        )
    return cmds


def web_manifest(env: dict[str, str], cfg: dict) -> dict:
    """Manifeste consomme par le dashboard : uniquement ce qui est affichable."""
    groups = []
    for group in cfg.get("groups", []):
        items = []
        for svc in group.get("services", []):
            if svc.get("hidden"):
                continue
            items.append(
                {
                    "id": svc["id"],
                    "label": svc["label"],
                    "desc": svc.get("desc", ""),
                    "icon": svc.get("icon", "box"),
                    "route": svc["route"],
                    "path": svc.get("path"),
                    "port": svc.get("port"),
                    "openPath": svc.get("open_path", "/"),
                    "embed": bool(svc.get("embed")),
                    "embedReason": svc.get("embed_reason", ""),
                    "locked": bool(svc.get("locked")),
                }
            )
        if items:
            groups.append({"name": group["name"], "services": items})
    return {"host": env["SAMPANA_HOST"], "groups": groups}


def health_targets(cfg: dict) -> list[dict]:
    out = []
    for svc in flatten(cfg):
        out.append(
            {
                "id": svc["id"],
                "label": svc["label"],
                "upstream": svc["upstream"],
                "scheme": "https" if "https" in svc.get("upstream_scheme", "") else "http",
                "probe": svc.get("probe", "/"),
                "expect": svc.get("probe_expect", []),
            }
        )
    return out


def main() -> int:
    env_path = ROOT / "config" / "sampana.env"
    svc_path = ROOT / "config" / "services.json"
    for p in (env_path, svc_path):
        if not p.exists():
            print(f"ERREUR : {p} manquant. Copie le fichier .example correspondant.",
                  file=sys.stderr)
            return 1

    env = load_env(env_path)
    cfg = json.loads(svc_path.read_text())
    services = flatten(cfg)

    out = ROOT / "build"
    out.mkdir(exist_ok=True)

    (out / "Caddyfile").write_text(caddyfile(env, services))
    (out / "services.web.json").write_text(
        json.dumps(web_manifest(env, cfg), indent=2, ensure_ascii=False)
    )
    (out / "health.targets.json").write_text(
        json.dumps(health_targets(cfg), indent=2, ensure_ascii=False)
    )
    (out / "serve.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        + "\n".join(serve_commands(env, services))
        + "\n"
    )
    os.chmod(out / "serve.sh", 0o755)

    print(f"Genere dans {out} :")
    for f in ("Caddyfile", "services.web.json", "health.targets.json", "serve.sh"):
        print(f"  - {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
