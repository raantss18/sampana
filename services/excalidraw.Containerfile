# Excalidraw — Sampana.
#
# L'image officielle est un bundle DEJA COMPILE, dans lequel l'adresse du
# serveur de collaboration est figee sur celui d'Excalidraw
# (https://oss-collab.excalidraw.com). L'`envsubst` de nginx ne s'applique
# qu'a ses fichiers de configuration, jamais au JavaScript.
#
# Recompiler Excalidraw depuis les sources demanderait une chaine Node
# complete pour changer une seule chaine de caracteres. On la remplace donc
# directement dans le bundle : une chaine litterale JavaScript se substitue
# sans rien casser, quelle que soit sa longueur.
#
# La substitution est faite A LA CONSTRUCTION et non au demarrage, pour que le
# conteneur puisse rester en lecture seule.
FROM docker.io/excalidraw/excalidraw:latest

# Adresse RELATIVE : le client se connecte a la meme origine que la page, et
# Caddy y route /socket.io vers le serveur de salles. Une adresse absolue
# imposerait un port de plus — or Tailscale n a que trois ports publiables,
# tous pris : la collaboration serait morte des qu on sort de la salle.
ARG SALLE=/

RUN set -eu; \
    cible="$(grep -rl 'oss-collab.excalidraw.com' /usr/share/nginx/html/assets/ || true)"; \
    if [ -z "$cible" ]; then \
      echo "ADRESSE DE COLLABORATION INTROUVABLE — Excalidraw a change de forme." >&2; \
      exit 1; \
    fi; \
    sed -i "s|https://oss-collab.excalidraw.com|${SALLE}|g" $cible; \
    grep -q "${SALLE}" $cible || { echo "substitution echouee" >&2; exit 1; }
