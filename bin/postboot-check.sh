#!/usr/bin/env bash
# Verifie que toute la stack est remontee seule apres un redemarrage.
# A lancer une fois la session ouverte : bin/postboot-check.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$ROOT/config/sampana.env" ] && { set -a; source "$ROOT/config/sampana.env"; set +a; }
HOST="${SAMPANA_HOST:?SAMPANA_HOST introuvable dans config/sampana.env}"

ok()   { printf '\033[32m  ok  \033[0m %s\n' "$*"; }
bad()  { printf '\033[31m FAIL \033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
head_() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
FAIL=0

echo "Uptime : $(uptime -p 2>/dev/null || uptime)"

head_ "Unites systeme"
for u in docker overleaf caddy latex-ai-assist tailscaled; do
    s="$(systemctl is-active "$u" 2>&1)"
    [ "$s" = active ] && ok "$(printf '%-20s' "$u")$s" || bad "$(printf '%-20s' "$u")$s"
done

head_ "Unites utilisateur"
for u in ollama jupyterlab ttyd sampana-auth sampana-health \
         mi-saina-backend mi-saina-frontend lean4web syncthing open-webui; do
    s="$(systemctl --user is-active "$u" 2>&1)"
    [ "$s" = active ] && ok "$(printf '%-20s' "$u")$s" || bad "$(printf '%-20s' "$u")$s"
done

head_ "Conteneurs"
for c in sharelatex mongo redis; do
    s="$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo absent)"
    [ "$s" = running ] && ok "$(printf '%-20s' "$c")$s" || bad "$(printf '%-20s' "$c")$s"
done

head_ "Exposition Tailscale"
n="$(tailscale serve status 2>/dev/null | grep -c proxy)"
[ "${n:-0}" -ge 6 ] && ok "mappings serve : $n" || bad "mappings serve : $n (6 attendus)"

head_ "Acces HTTPS"
code="$(curl -s -o /dev/null -w '%{http_code}' -m 30 "https://$HOST/" || echo 000)"
[ "$code" = 302 ] && ok "dashboard protege (302 vers la connexion)" \
                  || bad "dashboard : HTTP $code (302 attendu)"
code="$(curl -s -o /dev/null -w '%{http_code}' -m 30 "https://$HOST/auth/login" || echo 000)"
[ "$code" = 200 ] && ok "page de connexion accessible" || bad "connexion : HTTP $code"

head_ "Sante des services"
curl -s -m 30 "http://127.0.0.1:${HEALTH_PORT:-8089}/" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f\"  {d['up']}/{d['total']} en ligne\")
for s in d['services']:
    if s['status']!='up':
        print(f\"  KO {s['label']}: {s.get('error') or s.get('code')}\")
" || bad "sonde de sante injoignable"

echo
if [ "$FAIL" -eq 0 ]; then
    printf '\033[32m✓ La stack est remontee seule, sans intervention.\033[0m\n'
else
    printf '\033[31m✗ %s verification(s) en echec.\033[0m\n' "$FAIL"
    echo "  Comparer avec ~/.local/share/sampana/pre-reboot.txt"
fi
exit "$FAIL"
