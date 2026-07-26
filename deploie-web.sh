#!/usr/bin/env bash
# Deploie les pages web vers la racine servie par Caddy.
#
# Pourquoi ce script existe : /srv/sampana appartient a l'utilisateur `caddy`,
# donc chaque mise a jour demande sudo. Les longues lignes de commande enchainees
# par `&&` echouaient en silence des qu'un maillon renvoyait autre chose que 0 —
# on croyait avoir deploye, et le correctif n'etait jamais parti. Ce script fait
# le strict necessaire, et DIT ce qu'il a fait.
#
# Usage : sudo ./deploie-web.sh
set -uo pipefail

RACINE="$(cd "$(dirname "$0")" && pwd)"
CIBLE=/srv/sampana

if [ "$(id -u)" -ne 0 ]; then
    echo "A lancer avec sudo :  sudo $0" >&2
    exit 1
fi

deploie() {
    local src="$1" dst="$2"
    if [ ! -f "$src" ]; then
        return
    fi
    if cmp -s "$src" "$dst"; then
        printf '  inchange  %s\n' "${dst#$CIBLE/}"
        return
    fi
    if install -o caddy -g caddy -m 644 "$src" "$dst"; then
        printf '  DEPLOYE   %s\n' "${dst#$CIBLE/}"
    else
        printf '  ECHEC     %s\n' "${dst#$CIBLE/}" >&2
        return 1
    fi
}

echo "Deploiement des pages vers $CIBLE"

install -d -o caddy -g caddy "$CIBLE" "$CIBLE/guest"

erreurs=0
for f in "$RACINE"/web/*.html "$RACINE"/web/*.css "$RACINE"/web/*.js "$RACINE"/web/*.svg; do
    [ -e "$f" ] || continue
    deploie "$f" "$CIBLE/$(basename "$f")" || erreurs=1
done
for f in "$RACINE"/web/guest/*; do
    [ -f "$f" ] || continue
    deploie "$f" "$CIBLE/guest/$(basename "$f")" || erreurs=1
done

# Fichiers engendres : le manifeste que lisent les pages, et celui du portail.
[ -f "$RACINE/build/services.web.json" ] &&
    deploie "$RACINE/build/services.web.json" "$CIBLE/services.json"
[ -f "$RACINE/build/guest.web.json" ] &&
    deploie "$RACINE/build/guest.web.json" "$CIBLE/guest/guest.web.json"

# Verification par le contenu, pas par le code de retour : c'est la seule preuve
# que le fichier servi est bien celui du depot.
echo
if cmp -s "$RACINE/web/app.html" "$CIBLE/app.html"; then
    echo "OK — app.html servi est identique au depot."
else
    echo "ATTENTION — app.html servi DIFFERE du depot." >&2
    erreurs=1
fi

exit "$erreurs"
