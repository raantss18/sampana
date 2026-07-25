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


def security_headers(frameable: bool = False) -> list[str]:
    """En-tetes de securite, absents jusqu'ici de toutes les reponses.

    `X-Frame-Options` protege du detournement de clic : sans lui, un site tiers
    pouvait encadrer le tableau de bord et faire cliquer l'enseignant a son insu
    — l'interrupteur du mode invite et le bouton de deconnexion s'y pretent.

    `frameable` vaut True pour les services montres dans l'enveloppe «retour
    aux outils» : leur interdire l'encadrement afficherait un cadre blanc.
    SAMEORIGIN suffit alors, l'enveloppe etant servie sur la meme origine.

    Pas de HSTS : le tableau de bord est volontairement joignable en HTTP simple
    sur le reseau local, ou aucun certificat n'est valable pour une IP privee.
    L'imposer y rendrait le service inaccessible apres une seule visite en HTTPS.
    """
    return [
        "\theader {",
        f"\t\tX-Frame-Options {'SAMEORIGIN' if frameable else 'DENY'}",
        "\t\tX-Content-Type-Options nosniff",
        "\t\tReferrer-Policy same-origin",
        "\t\t# Les serveurs applicatifs posent parfois le leur : on remplace.",
        "\t\t-Server",
        "\t}",
        "",
    ]


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


def guest_enabled(env: dict[str, str]) -> bool:
    return env.get("GUEST_ENABLED", "0") in ("1", "true", "yes")


def upstream_directive(svc: dict) -> str:
    """Cible d'un reverse_proxy, cote Caddy.

    Le JupyterLab invite n'ecoute sur aucun port : prive de reseau, il ne peut
    exposer qu'une socket Unix. Caddy la designe par `unix/<chemin>`.
    """
    up = svc["upstream"]
    if up.startswith("unix/"):
        return up
    if svc.get("upstream_scheme", "http").startswith("https"):
        return f"https://{up}"
    return up


def socketio_block(svc: dict, env: dict[str, str]) -> list[str]:
    """Route /socket.io vers le serveur de collaboration, s'il en faut un.

    Le client se connecte a la MEME ORIGINE que la page. Lui donner une adresse
    absolue imposerait un port supplementaire, or Tailscale n'en publie que
    trois et ils sont tous pris : la collaboration serait morte des qu'on sort
    de la salle. En passant par le meme port, elle suit l'outil partout.
    """
    salle = env.get("COLLAB_UPSTREAM", "")
    if not svc.get("collab") or not salle:
        return []
    return [
        "\t# Collaboration en direct : socket.io relaye vers le serveur de",
        "\t# salles. Il ne voit que du chiffre — la cle vit dans le fragment",
        "\t# de l'URL, jamais transmis au serveur.",
        "\thandle /socket.io/* {",
        f"\t\treverse_proxy {salle}",
        "\t}",
        "",
    ]


def guest_site(env: dict[str, str], guest: dict) -> list[str]:
    """Site invite : le SEUL de Sampana lie a une adresse routable.

    Deux zones y cohabitent et l'ordre des blocs les separe :
      - le portail (chooser, saisie du code) doit rester libre d'acces, sinon
        personne ne pourrait jamais entrer le code ;
      - tout le reste passe par /guest/verify.

    Meme piege que sur le site principal : Caddy evalue forward_auth avant les
    `handle`, il faut donc exclure explicitement les chemins du portail.
    """
    gate = f"127.0.0.1:{env['GUEST_GATE_PORT']}"
    web_root = env["WEB_ROOT"]
    services = guest.get("services", [])
    path_services = [s for s in services if s.get("route") == "path"]

    # `/guest/remaining` est servi par le portail, pas par le disque, et doit
    # rester joignable meme sans session : il repond alors «0 seconde», ce qui
    # est precisement ce que la page doit afficher quand la seance est close.
    portal = ["/", "/guest/enter", "/guest/logout", "/guest/remaining",
              "/guest/files", "/guest/open", "/guest/save",
              "/guest/submit", "/guest/help"]
    free = portal + ["/logo.svg"]

    lines = [
        "# ── Mode invite ────────────────────────────────────────────────",
        "# Sans mot de passe et sans persistance. Contrairement a tout le reste",
        "# de Sampana, ce bloc ecoute sur une adresse routable : c'est son role.",
        f":{env['GUEST_PORT']} {{",
        f"\tbind {env.get('GUEST_BIND', '0.0.0.0')}",
        "\tencode zstd gzip",
        "",
    ] + security_headers(frameable=True) + [
        "\t# Portail : accessible SANS session invitee, sinon le code de seance",
        "\t# ne pourrait jamais etre saisi. `handle` n'accepte qu'un seul matcher,",
        "\t# d'ou le matcher nomme.",
        f"\t@portal path {' '.join(portal)}",
        "\thandle @portal {",
        f"\t\treverse_proxy {gate}",
        "\t}",
        "",
        "\thandle /logo.svg {",
        f"\t\troot * {web_root}",
        "\t\tfile_server",
        "\t}",
        "",
        f"\t@guarded not path {' '.join(free)}",
        f"\tforward_auth @guarded {gate} {{",
        "\t\turi /guest/verify",
        "\t\tcopy_headers X-Forwarded-Uri X-Forwarded-Host X-Forwarded-Proto",
        "\t}",
        "",
    ]

    for svc in path_services:
        if svc.get("direct"):
            continue  # page statique : servie par le bloc de fichiers final
        p = svc["path"]
        mid = svc["id"].replace("-", "_")
        if svc.get("strip"):
            lines += [
                f"\t# {svc['label']} — prefixe RETIRE : le backend n'accepte que",
                "\t# des chemins absolus depuis sa racine.",
                f"\thandle_path {p}/* {{",
                f"\t\treverse_proxy {upstream_directive(svc)}",
                "\t}",
                "",
            ]
        else:
            lines += [
                f"\t# {svc['label']} — prefixe conserve (le backend est configure avec).",
                f"\t@{mid} path {p} {p}/*",
                f"\thandle @{mid} {{",
                f"\t\treverse_proxy {upstream_directive(svc)}",
                "\t}",
                "",
            ]

    for svc in path_services:
        if svc.get("direct"):
            continue
        if svc.get("trailing_slash"):
            lines.append(f"\tredir {svc['path']} {svc['path']}/ permanent")
    lines.append("")

    share = env.get("GUEST_SHARE_DIR", "")
    if share:
        lines += [
            "\t# Dossier partage, en consultation et telechargement.",
            "\t# file_server ne sert que des GET : la lecture seule est structurelle",
            "\t# ici, elle ne depend pas seulement des permissions du disque.",
            "\thandle_path /guest/partage/* {",
            f"\t\troot * {share}",
            "\t\tfile_server browse",
            "\t}",
            "",
            "\tredir /guest/partage /guest/partage/ permanent",
            "",
        ]

    lines += [
        "\t# Tableau de bord invite. `handle_path` retire le prefixe : sans lui,",
        f"\t# une requete /guest/ irait chercher {web_root}/guest/guest/.",
        "\thandle_path /guest/* {",
        f"\t\troot * {web_root}/guest",
        "\t\tfile_server",
        "\t}",
        "",
        "\t# Toute autre URL ramene au tableau de bord plutot qu'a une 404 nue :",
        "\t# un invite qui tombe sur un lien mort doit pouvoir repartir.",
        "\thandle {",
        "\t\tredir * /guest/",
        "\t}",
        "}",
        "",
    ]

    # Services invites sur un port dedie (ceux qui exigent la racine).
    for svc in services:
        if svc.get("route") != "port":
            continue
        lines += [
            f"# {svc['label']} — invite",
            f":{svc['port']} {{",
            f"\tbind {env.get('GUEST_BIND', '0.0.0.0')}",
            "\tencode zstd gzip",
            "",
        ] + security_headers(frameable=True) + [
            "\t# Enveloppe «retour aux outils», servie SUR CE PORT. Elle doit y",
            "\t# etre : LaTeX Lab renvoie X-Frame-Options SAMEORIGIN, et un port",
            "\t# different est une origine differente. Encadre depuis le port du",
            "\t# tableau de bord, il n'afficherait qu'un cadre blanc.",
            "\thandle /sampana/tool.html {",
            f"\t\troot * {web_root}/guest",
            "\t\trewrite * /tool.html",
            "\t\tfile_server",
            "\t}",
            "",
            "\t# Le compte a rebours de l'enveloppe interroge le portail, qui",
            "\t# n'est monte que sur le port du tableau de bord. On le relaie.",
            "\thandle /guest/remaining {",
            f"\t\treverse_proxy {gate}",
            "\t}",
            "",
        ] + socketio_block(svc, env) + [
            f"\t@app not path /sampana/* /guest/remaining",
            f"\tforward_auth @app {gate} {{",
            "\t\turi /guest/verify",
            "\t\tcopy_headers X-Forwarded-Uri X-Forwarded-Host X-Forwarded-Proto",
            "\t}",
            "",
            f"\treverse_proxy {upstream_directive(svc)}",
            "}",
            "",
        ]

    return lines


def caddyfile(env: dict[str, str], services: list[dict], guest: dict | None = None) -> str:
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
        f"\tbind {env.get('CADDY_BIND', '127.0.0.1')}",
        "\tencode zstd gzip",
        "",
    ] + security_headers() + [
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

    if guest_enabled(env):
        lines += [
            "\t# Pilotage du mode invite. Ces routes sont sur le site PRINCIPAL,",
            "\t# donc derriere le mot de passe maitre : le portail invite, lui,",
            "\t# ne proxifie que /guest/*, et ne peut pas les atteindre.",
            "\thandle /guest-admin/* {",
            f"\t\treverse_proxy 127.0.0.1:{env['GUEST_GATE_PORT']}",
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
        if svc.get("direct"):
            continue
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
            # Sans compression, la feuille de style de LaTeX Lab pesait 872 ko
            # sur le reseau. Les services a port dedie en etaient prives : seuls
            # les deux sites principaux la declaraient.
            "\tencode zstd gzip",
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
            lines += socketio_block(svc, env)
            lines.append(f"\treverse_proxy {svc['upstream']}")
        lines += ["}", ""]

    # Port 80 : redirection vers HTTPS, rien de plus.
    #
    # Sans lui, taper le nom d'hote sans schema donne un ERR_CONNECTION_REFUSED
    # brut — le navigateur complete en http://, que Tailscale ne sert pas. Et
    # SERVIR le tableau de bord en clair ici serait pire : le cookie de session
    # est marque Secure pour ce nom, il ne serait donc jamais conserve, et la
    # connexion echouerait en boucle sans expliquer pourquoi.
    lines += [
        "# Redirection HTTP vers HTTPS pour le nom du tailnet.",
        f":{env.get('REDIR_PORT', '8087')} {{",
        "\tbind 127.0.0.1",
        f"\tredir https://{env['SAMPANA_HOST']}{{uri}} permanent",
        "}",
        "",
    ]

    if guest and guest_enabled(env):
        lines += guest_site(env, guest)

    return "\n".join(lines)


def serve_commands(env: dict[str, str], services: list[dict]) -> list[str]:
    cmds = [
        f"tailscale serve --bg --https=443 http://127.0.0.1:{env['CADDY_PORT']}",
        # Le nom d'hote tape sans schema arrive en http : sans cette regle, le
        # navigateur affiche un refus de connexion au lieu de basculer en TLS.
        f"tailscale serve --bg --http=80 http://127.0.0.1:{env.get('REDIR_PORT', '8087')}",
    ]
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


def funnel_commands(env: dict[str, str], guest: dict) -> tuple[list[str], list[str]]:
    """Commandes d'ouverture et de fermeture du Funnel public.

    Le Funnel publie sur Internet, sans restriction d'origine, et l'URL .ts.net
    apparait dans les journaux de Certificate Transparency : elle est scannee en
    quelques heures. On genere donc AUSSI la commande de fermeture, pour que le
    mode invite public puisse n'etre ouvert que pendant le cours.

    Rappel : Tailscale n'accepte le Funnel que sur 443, 8443 et 10000.
    """
    allowed = {443, 8443, 10000}
    on = [f"tailscale funnel --bg --https={env['GUEST_FUNNEL_PORT']} "
          f"http://127.0.0.1:{env['GUEST_PORT']}"]
    off = [f"tailscale funnel --https={env['GUEST_FUNNEL_PORT']} off"]

    for svc in guest.get("services", []):
        if svc.get("route") != "port":
            continue
        # `funnel_port` separe le port publie du port ecoute localement. Sans
        # cette separation, tailscaled lie 100.x:PORT pendant que Caddy tente
        # 0.0.0.0:PORT — et Caddy refuse alors de demarrer, emportant le
        # dashboard avec lui des que le Funnel est ouvert.
        pub = svc.get("funnel_port", svc["port"])
        if pub in allowed and pub == svc["port"]:
            # Seuls les ports REELLEMENT publies posent probleme : un service
            # qui reste en salle ne croise jamais tailscaled.
            print(
                f"  ATTENTION : {svc['id']} publie et ecoute sur le meme port "
                f"({pub}). Declare `funnel_port` different de `port`, sinon "
                f"Caddy ne pourra plus demarrer une fois le Funnel ouvert.",
                file=sys.stderr,
            )
        if pub not in allowed:
            # Pas une erreur : un service invite peut n'exister qu'en salle.
            # Tailscale ne publie que 443, 8443 et 10000, et ils sont comptes.
            print(
                f"  note : {svc['id']} (port {pub}) restera accessible "
                f"sur le LAN uniquement — Funnel n'accepte que 443, 8443, 10000.",
                file=sys.stderr,
            )
            continue
        on.append(f"tailscale funnel --bg --https={pub} "
                  f"http://127.0.0.1:{svc['port']}")
        off.append(f"tailscale funnel --https={pub} off")

    if int(env["GUEST_FUNNEL_PORT"]) not in allowed:
        raise SystemExit(
            f"ERREUR : GUEST_FUNNEL_PORT={env['GUEST_FUNNEL_PORT']} n'est pas "
            f"publiable. Ports autorises : 443, 8443, 10000."
        )
    return on, off


def check_ports(env: dict[str, str], services: list[dict], guest: dict) -> None:
    """Deux sites Caddy sur le meme port, c'est un service invite qui se
    retrouve devant le mot de passe maitre — ou l'inverse. On refuse tot."""
    seen: dict[int, str] = {}
    for svc in services:
        if svc.get("route") == "port":
            seen[svc["port"]] = svc["id"]
    seen[int(env["CADDY_PORT"])] = "CADDY_PORT"

    if not guest_enabled(env):
        return
    for port, who in (
        (int(env["GUEST_PORT"]), "GUEST_PORT"),
        (int(env["GUEST_GATE_PORT"]), "GUEST_GATE_PORT"),
    ):
        if port in seen:
            raise SystemExit(f"ERREUR : {who}={port} est deja pris par {seen[port]}.")
        seen[port] = who
    for svc in guest.get("services", []):
        if svc.get("route") != "port":
            continue
        if svc["port"] in seen:
            raise SystemExit(
                f"ERREUR : le service invite {svc['id']} veut le port {svc['port']}, "
                f"deja utilise par {seen[svc['port']]}. Un port ne peut pas etre a "
                f"la fois protege par le mot de passe maitre et ouvert aux invites."
            )
        seen[svc["port"]] = svc["id"]


def guest_manifest(env: dict[str, str], guest: dict) -> dict:
    """Manifeste du tableau de bord invite."""
    items = []
    for svc in guest.get("services", []):
        items.append({
            "id": svc["id"],
            "label": svc["label"],
            "desc": svc.get("desc", ""),
            "icon": svc.get("icon", "box"),
            "route": svc["route"],
            "path": svc.get("path"),
            "port": svc.get("port"),
            # Port publie par Funnel, quand il differe de l'ecoute locale.
            # La page invitee choisit selon l'origine par laquelle on la joint.
            "funnelPort": svc.get("funnel_port", svc.get("port")),
            # Tailscale ne publie que 443, 8443 et 10000. Un service sur un
            # autre port reste joignable en salle, jamais depuis Internet —
            # l'interface doit le dire plutot que de fabriquer un lien mort.
            "funnelOk": svc.get("route") != "port"
                        or svc.get("funnel_port", svc.get("port")) in (443, 8443, 10000),
            # Page servie directement par Caddy : elle a son propre bandeau,
            # l'enveloppe ferait doublon.
            "direct": bool(svc.get("direct")),
            "openPath": svc.get("open_path", "/"),
            "sharedAccount": bool(svc.get("shared_account")),
        })
    # Le dossier partage n'est pas un service proxifie mais un file_server :
    # il n'a ni port ni upstream, d'ou cette carte ajoutee ici plutot que
    # declaree dans services.json, ou elle aurait exige des champs vides.
    if env.get("GUEST_SHARE_DIR"):
        items.append({
            "id": "guest-partage",
            "label": "Fichiers partagés",
            "desc": "Cours, énoncés et modèles déposés par l'enseignant. "
                    "En lecture seule, téléchargeables.",
            "icon": "folder",
            "route": "path",
            # Le navigateur maison remplace le listing brut de Caddy : il
            # reconnait les types de fichiers et ouvre l'outil correspondant.
            "path": "/guest/fichiers.html",
            "openPath": "/",
            "direct": True,
            "funnelOk": True,
        })

    return {
        "services": items,
        "ttl": int(env.get("GUEST_TTL", 7200)),
        "latexEmail": env.get("GUEST_LATEX_EMAIL", ""),
        # Compte partage : le mot de passe doit etre visible des invites,
        # sinon LaTeX Lab leur reste inutilisable. La page est deja derriere
        # le code de seance.
        "latexPassword": env.get("GUEST_LATEX_PASSWORD", ""),
    }


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
    guest = cfg.get("guest", {})

    check_ports(env, services, guest)

    out = ROOT / "build"
    out.mkdir(exist_ok=True)

    (out / "Caddyfile").write_text(caddyfile(env, services, guest))
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

    written = ["Caddyfile", "services.web.json", "health.targets.json", "serve.sh"]

    if guest_enabled(env) and guest:
        (out / "guest.web.json").write_text(
            json.dumps(guest_manifest(env, guest), indent=2, ensure_ascii=False)
        )
        on, off = funnel_commands(env, guest)
        (out / "funnel.sh").write_text(
            "#!/usr/bin/env bash\n"
            "# GENERE PAR sampana — ouvre ou ferme l'acces PUBLIC au mode invite.\n"
            "#\n"
            "# `funnel.sh on` publie le portail invite sur Internet. L'URL .ts.net\n"
            "# est listee dans les journaux de Certificate Transparency : elle sera\n"
            "# scannee. Ne la laisse ouverte que pendant les seances.\n"
            "set -euo pipefail\n\n"
            'case "${1:-}" in\n'
            "  on)\n    " + "\n    ".join(on) + "\n"
            '    echo "Mode invite PUBLIC. Ferme-le apres le cours : $0 off"\n'
            "    ;;\n"
            "  off)\n    " + "\n    ".join(off) + "\n"
            '    echo "Funnel ferme. Le mode invite reste joignable sur le LAN."\n'
            "    ;;\n"
            "  *)\n"
            '    echo "Usage : $0 on|off" >&2\n'
            "    exit 2\n"
            "    ;;\n"
            "esac\n"
        )
        os.chmod(out / "funnel.sh", 0o755)
        written += ["guest.web.json", "funnel.sh"]

    print(f"Genere dans {out} :")
    for f in written:
        print(f"  - {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
