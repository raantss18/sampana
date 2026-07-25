#!/usr/bin/env bash
# Sampana — applique la configuration : regenere, VALIDE, installe, recharge.
#
# Ce script est le seul point par lequel le tableau de bord obtient des droits
# root, via une regle sudo NOPASSWD qui ne vise que lui. Il est donc installe
# hors du depot, appartenant a root et NON modifiable par l'utilisateur :
# sinon la restriction ne vaudrait rien, il suffirait d'en reecrire le contenu.
#
# Il n'accepte aucun argument, pour la meme raison.
#
# Le garde-fou essentiel est la validation AVANT installation : un Caddyfile
# invalide laisse en place l'ancien plutot que d'empecher Caddy de redemarrer
# — ce qui rendrait le tableau de bord inaccessible, donc irreparable depuis
# l'interface elle-meme.
set -euo pipefail

# Refus explicite : la regle sudo autorise le chemin, pas une ligne de commande.
# Sans ce garde-fou, un argument inattendu serait simplement ignore — et la
# promesse faite plus haut ne serait pas tenue.
if [ "$#" -ne 0 ]; then
    echo "Ce script n'accepte aucun argument." >&2
    exit 2
fi

REPO="@REPO@"
WEB_ROOT="@WEB_ROOT@"
RUN_AS="@USER@"

cd "$REPO"

# La generation lit la configuration de l'utilisateur : on l'execute sous son
# identite, pas sous root.
sudo -u "$RUN_AS" python3 bin/render.py >/dev/null

if ! caddy validate --config build/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    echo "Caddyfile genere invalide — rien n'a ete applique." >&2
    caddy validate --config build/Caddyfile --adapter caddyfile 2>&1 \
        | grep -v '"level":"info"' | tail -3 >&2
    exit 1
fi

cp -a /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak" 2>/dev/null || true
install -m 644 build/Caddyfile /etc/caddy/Caddyfile

install -o caddy -g caddy -m 644 build/services.web.json "$WEB_ROOT/services.json"
[ -f build/guest.web.json ] && \
    install -o caddy -g caddy -m 644 build/guest.web.json "$WEB_ROOT/guest/guest.web.json"

if ! systemctl reload caddy; then
    echo "Rechargement refuse — restauration de la configuration precedente." >&2
    cp -a /etc/caddy/Caddyfile.bak /etc/caddy/Caddyfile
    systemctl reload caddy || true
    exit 1
fi

echo "Configuration appliquee."
