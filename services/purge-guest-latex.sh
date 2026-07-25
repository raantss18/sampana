#!/usr/bin/env bash
# Sampana — purge quotidienne des projets du compte LaTeX Lab invite.
#
# Le script .mjs est recopie dans le conteneur a CHAQUE execution, et non une
# fois pour toutes : une mise a jour d'Overleaf recree le conteneur et emporte
# tout ce qui y avait ete depose. Sans cette recopie, la purge cesserait
# silencieusement — et le mode invite conserverait les documents des etudiants
# tout en pretendant le contraire.
set -euo pipefail

GUEST_EMAIL="${1:-invite@sampana.local}"
# Depuis le repertoire d'installation, et non le depot : celui-ci peut
# etre deplace ou supprime apres coup.
SRC="$HOME/.local/share/sampana/purge-guest-latex.mjs"
TOOLKIT="$HOME/overleaf-toolkit"

if ! docker ps --format '{{.Names}}' | grep -qx sharelatex; then
  echo "LaTeX Lab n'est pas demarre — purge reportee."
  exit 0
fi

docker cp "$SRC" sharelatex:/overleaf/services/web/scripts/sampana-purge-guest.mjs
cd "$TOOLKIT"
./bin/run-script scripts/sampana-purge-guest.mjs "$GUEST_EMAIL" 2>&1 \
  | grep -vE '^\{"name"' \
  | grep -iE 'projet|introuvable|echec'
