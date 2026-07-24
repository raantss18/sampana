#!/usr/bin/env bash
# Sampana — desinstallation. Ne touche a AUCUN des services proxifies
# (Overleaf, JupyterLab, mi-saina...), uniquement a ce que Sampana a installe.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$ROOT/config/sampana.env" ] && { set -a; source "$ROOT/config/sampana.env"; set +a; }
SAMPANA_HOST="${SAMPANA_HOST:-}"
WEB_ROOT="${WEB_ROOT:-/srv/sampana}"

echo "Cette operation va :"
echo "  - arreter et desactiver sampana-health, sampana-auth et ttyd"
echo "  - supprimer $WEB_ROOT"
echo "  - retirer les mappings tailscale serve de Sampana"
echo "  - restaurer /etc/caddy/Caddyfile.pre-sampana s'il existe"
echo
read -r -p "Continuer ? [o/N] " ans
[[ "$ans" =~ ^[oOyY]$ ]] || { echo "Annule."; exit 0; }

systemctl --user disable --now sampana-health sampana-auth ttyd 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/sampana-health.service" \
      "$HOME/.config/systemd/user/sampana-auth.service" \
      "$HOME/.config/systemd/user/ttyd.service"
systemctl --user daemon-reload
rm -rf "$HOME/.local/share/sampana"
echo "Services utilisateur retires."

if [ -n "$SAMPANA_HOST" ] && [ -f "$ROOT/build/serve.sh" ]; then
    # Retire exactement les ports que Sampana avait declares.
    grep -oP '(?<=--https=)\d+' "$ROOT/build/serve.sh" | while read -r p; do
        tailscale serve --https="$p" off 2>/dev/null || true
    done
    echo "Mappings tailscale serve retires."
fi

sudo rm -rf "$WEB_ROOT"

if [ -f /etc/caddy/Caddyfile.pre-sampana ]; then
    sudo mv /etc/caddy/Caddyfile.pre-sampana /etc/caddy/Caddyfile
    sudo systemctl restart caddy
    echo "Caddyfile precedent restaure."
else
    sudo systemctl stop caddy 2>/dev/null || true
    echo "Caddy arrete (aucune configuration precedente a restaurer)."
fi

echo
echo "Sampana desinstalle. Le mot de passe maitre est conserve dans"
echo "~/.config/sampana/auth.json (a supprimer manuellement si besoin)."
