# Obsidian — Sampana.
#
# Obsidian n'a pas de version web : c'est une application de bureau Electron.
# L'image de base la fait tourner dans un bureau minimal, diffuse au navigateur
# par Selkies. C'est donc le VRAI Obsidian, avec ses greffons, et non une
# imitation qui n'en lirait que les fichiers.
#
# Le seul ajout de Sampana : la mise a l'echelle par defaut sur ecran tactile.
# Selkies affiche le bureau a sa taille reelle, si bien qu'une tablette n'en
# montre qu'un coin. Le reglage existe mais vit dans le stockage local du
# navigateur — ni parametre d'URL ni variable d'environnement ne permettent de
# le fixer. On l'ecrit donc avant que l'application ne le lise.
FROM lscr.io/linuxserver/obsidian:latest

# On modifie les DOSSIERS SOURCES, pas `/usr/share/selkies/web`.
#
# Ce dernier n'existe pas dans l'image : le script d'initialisation le fabrique
# au demarrage, par `cp -a` depuis le dossier designe par la variable
# `DASHBOARD`. Patcher `web/` a la construction echouerait donc — et patcher une
# seule variante laisserait l'autre intacte si la variable venait a changer.
COPY services/obsidian-tactile.js /usr/share/selkies/selkies-dashboard/sampana-tactile.js
COPY services/obsidian-tactile.js /usr/share/selkies/selkies-dashboard-wish/sampana-tactile.js

# Le script doit s'executer AVANT le bundle. Un `<script>` classique en ligne
# passe avant un `type="module"`, differé par definition.
#
# La substitution echoue bruyamment si le point d'ancrage a disparu : mieux vaut
# une construction qui s'arrete qu'une image silencieusement identique a
# l'originale, dont on ne verrait le probleme qu'en classe.
RUN set -eu; \
    touche=0; \
    for d in selkies-dashboard selkies-dashboard-wish; do \
      f="/usr/share/selkies/$d/index.html"; \
      [ -f "$f" ] || continue; \
      grep -q '<script type="module"' "$f" || { \
        echo "POINT D'ANCRAGE INTROUVABLE dans $d — Selkies a change de forme." >&2; \
        exit 1; \
      }; \
      sed -i 's|<script type="module"|<script src="./sampana-tactile.js"></script><script type="module"|' "$f"; \
      grep -q 'sampana-tactile.js' "$f" || { echo "injection echouee dans $d" >&2; exit 1; }; \
      test -s "/usr/share/selkies/$d/sampana-tactile.js" || { echo "script absent de $d" >&2; exit 1; }; \
      touche=$((touche + 1)); \
    done; \
    [ "$touche" -gt 0 ] || { echo "aucun dossier de tableau de bord trouve" >&2; exit 1; }; \
    echo "variantes corrigees : $touche"
