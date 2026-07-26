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

# ── Polices : couper le cordon avec le CDN ─────────────────────────
#
# Excalidraw charge ses polices depuis un CDN distant. En classe sur un partage
# de connexion sans Internet, ces requetes echouent : le texte bascule sur des
# polices de repli et les dessins ne ressemblent plus a ce que l'enseignant
# voit sur son ecran. Pire, un dessin exporte n'a pas la meme allure selon que
# la machine avait Internet ou non.
#
# L'image embarque DEJA les polices de dessin, aux chemins exacts que le CDN
# sert : retirer le prefixe distant suffit a les faire resoudre localement.
ARG CDN=https://excalidraw.nyc3.cdn.digitaloceanspaces.com/oss/

# Seule Assistant — la police de l'interface — manque a l'appel. On la recupere
# ICI, a la construction, pour qu'aucune execution n'ait besoin du reseau.
# 80 Ko pour quatre graisses : l'image ne s'en ressent pas.
#
# La signature `wOF2` est verifiee : un CDN qui repondrait une page d'erreur en
# 200 produirait un fichier plausible mais illisible, et l'echec ne se verrait
# qu'en classe. (Commentaire hors du RUN a dessein : joints par `\`, les `#`
# internes dependent du parseur pour etre retires, sinon ils avalent la suite.)
RUN set -eu; \
    racine=/usr/share/nginx/html; \
    mkdir -p "$racine/fonts/Assistant"; \
    for g in Regular Medium SemiBold Bold; do \
      f="$racine/fonts/Assistant/Assistant-$g.woff2"; \
      curl -fsS --retry 3 -o "$f" "${CDN}fonts/Assistant/Assistant-$g.woff2"; \
      head -c 4 "$f" | grep -q 'wOF2' || { echo "Assistant-$g n'est pas une woff2" >&2; exit 1; }; \
    done

# Reecriture des references. Le prefixe se remplace par `/` : `${CDN}fonts/X`
# devient `/fonts/X`, servi par le nginx de l'image.
RUN set -eu; \
    racine=/usr/share/nginx/html; \
    cibles="$(grep -rl "$CDN" "$racine" || true)"; \
    if [ -z "$cibles" ]; then \
      echo "AUCUNE REFERENCE CDN — Excalidraw a change de forme, revoir ce fichier." >&2; \
      exit 1; \
    fi; \
    attendues="$(grep -rhoE "$CDN[A-Za-z0-9/._-]+\.woff2" $cibles | sed "s|$CDN||" | sort -u)"; \
    sed -i "s|$CDN|/|g" $cibles; \
    if grep -rq "$CDN" "$racine"; then \
      echo "reference CDN residuelle apres reecriture" >&2; exit 1; \
    fi; \
    for p in $attendues; do \
      [ -f "$racine/$p" ] || { echo "police desormais locale mais absente : $p" >&2; exit 1; }; \
    done; \
    echo "polices rapatriees : $(echo "$attendues" | wc -l)"

# ── Appels sortants restants ───────────────────────────────────────
#
# Un mouchard d'audience et deux `preconnect` vers Google Fonts. Hors ligne ils
# ne servent a rien ; en ligne ils font partir vers des tiers la trace de
# chaque eleve qui ouvre le tableau blanc. Rien dans Sampana n'en depend.
#
# Le mouchard n'est pas une balise <script> mais une URL injectee a l'execution
# par le bundle. On la fait pointer vers un fichier local vide plutot que de la
# supprimer : le code qui l'insere continue de fonctionner, sans rien emettre.
RUN set -eu; \
    racine=/usr/share/nginx/html; \
    f="$racine/index.html"; \
    : > "$racine/noop.js"; \
    sed -i \
      -e 's|https://scripts\.simpleanalyticscdn\.com/latest\.js|/noop.js|g' \
      -e 's|<link rel="preconnect" href="https://fonts\.googleapis\.com"/>||g' \
      -e 's|<link rel="preconnect" href="https://fonts\.gstatic\.com" crossorigin/>||g' \
      "$f"; \
    ! grep -q 'simpleanalyticscdn\|fonts\.googleapis\|fonts\.gstatic' "$f" \
      || { echo "appel sortant residuel dans index.html" >&2; exit 1; }
