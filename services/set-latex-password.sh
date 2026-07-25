#!/usr/bin/env bash
# Sampana — change le mot de passe du compte LaTeX Lab partage.
#
# Appele par le portail invite a chaque ouverture de seance : le code de salle
# et ce mot de passe doivent changer ensemble, sinon un etudiant de la semaine
# derniere garderait l'acces aux projets de cette semaine.
#
# Le script .mjs est recopie dans le conteneur a CHAQUE appel : une mise a jour
# d'Overleaf recree le conteneur et emporte tout ce qui y a ete depose.
set -euo pipefail

EMAIL="${1:?usage: set-latex-password.sh <email> <mot-de-passe>}"
PASSWORD="${2:?usage: set-latex-password.sh <email> <mot-de-passe>}"
# Depuis le repertoire d'installation, et non le depot : celui-ci peut
# etre deplace ou supprime apres coup.
SRC="$HOME/.local/share/sampana/set-latex-password.mjs"
TOOLKIT="$HOME/overleaf-toolkit"

if ! docker ps --format '{{.Names}}' | grep -qx sharelatex; then
  echo "LaTeX Lab n'est pas demarre" >&2
  exit 1
fi

docker cp "$SRC" sharelatex:/overleaf/services/web/scripts/sampana-set-latex-password.mjs
cd "$TOOLKIT"
./bin/run-script scripts/sampana-set-latex-password.mjs "$EMAIL" "$PASSWORD" 2>&1 \
  | grep -vE '^\{"name"' | tail -3
