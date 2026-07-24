# Prompt Claude Code — reconstruire toute la stack

Prompt destiné à **Claude Code**, pour régénérer sur une machine neuve
l'ensemble : Overleaf CE, assistant IA local, extensions, et le dashboard
Sampana. Il encode les pièges déjà rencontrés — ne les retirez pas, ils
coûtent chacun plusieurs heures de débogage.

Copiez tout ce qui suit la ligne, adaptez le bloc **Contexte**, et donnez-le
à Claude Code depuis un terminal sur la machine cible.

---

## Contexte

Machine : Arch / EndeavourOS, GPU NVIDIA 8 Go de VRAM, Tailscale déjà connecté.
Adapte les chemins et le nom d'hôte à ce que tu trouves réellement.

Objectif : une seule URL HTTPS, accessible depuis n'importe où via Tailscale,
donnant accès à Overleaf, JupyterLab, mes services locaux et un terminal web,
avec une assistance IA locale (Ollama) dans Overleaf **et** JupyterLab.

## Règle de travail

**N'exécute aucune commande destructive avant d'avoir inventorié l'existant et
obtenu ma confirmation.** À chaque étape : vérifie l'état réel, rapporte ce que
tu trouves, puis agis. Ne suppose jamais qu'une étape a échoué ou réussi sans
l'avoir testée — plusieurs outils de cette stack **renvoient un code de sortie 0
en n'ayant rien fait**.

## Étape 0 — Inventaire, sans rien modifier

Relève et rapporte-moi :

- Ports en écoute : `ss -tlnp`, en distinguant `127.0.0.1` de `0.0.0.0`.
- Unités systemd **système** *et* **utilisateur** (`systemctl --user`).
  Beaucoup de services tournent en unité utilisateur ; c'est facile à manquer.
- Conteneurs Docker **et** Podman (`docker ps -a`, `podman ps -a`).
- `loginctl show-user $USER -p Linger` — sans `Linger=yes`, aucune unité
  utilisateur ne démarre au boot.
- Pour chaque dépôt à installer : la version réellement présente (`VERSION`,
  `git log`) **comparée** à celle des artefacts pré-construits (`dist/*.run`).
  Un installeur trouvé dans un dépôt est souvent **plus ancien** que le dépôt
  lui-même : l'exécuter serait une régression.

Ne lance un script d'installation qu'après m'avoir montré ce qu'il fait
(chemin d'installation, ports, unités systemd qu'il écrit).

## Étape 1 — Overleaf Community Edition

Utilise `overleaf-toolkit`. Points critiques :

- **Épingle `MONGO_VERSION` à une version testée** (ex. `8.0.16`) dans
  `config/overleaf.rc`. Le tag flottant `mongo:8.0` refuse de démarrer sur les
  noyaux ≥ 6.19 (`SERVER-121912`) : le conteneur boucle en crash.
  Vérifie le noyau (`uname -r`) et teste l'image avant.
- `bin/start` fait `docker compose start` : il échoue si un conteneur a été
  supprimé. Utilise **`bin/up -d`**, qui initialise aussi le replica set Mongo.
- `SIBLING_CONTAINERS_ENABLED=true` n'existe pas en Community Edition → `false`.
- **`tlmgr install scheme-full` sort en code 0 sans rien installer** si
  `tlmgr update --self` n'a pas été lancé avant. Après coup, vérifie
  systématiquement : `tlmgr list --only-installed | wc -l` (~5000 attendu).
- TeX Live vit **dans le conteneur, pas dans un volume** : une recréation
  détruit les heures de téléchargement. Fige avec `docker commit` puis
  `OVERLEAF_IMAGE_NAME`, et **prouve-le** en recréant le conteneur et en
  recomptant les paquets.
- `grunt user:create-admin` n'existe plus. Utilise
  `node modules/server-ce-scripts/scripts/create-user.mjs --admin --email=...`.
- Avec `OVERLEAF_SECURE_COOKIE=true`, le login **exige** HTTPS.

## Étape 2 — Backend d'assistance IA (FastAPI + Ollama)

Service local exposant `/suggest` (modèle rapide) et `/diagnose` (modèle
capable), plus `/health`, avec un paramètre `mode` (`latex` | `python` | `sage`)
sélectionnant le prompt système.

- **Vérifie quels modèles sont réellement installés** (`ollama list`) avant
  d'écrire quoi que ce soit. Ne code pas contre des modèles absents.
- Ollama tourne peut-être en unité **utilisateur**. Si une unité *système*
  homonyme existe, elle bouclera en `address already in use` : désactive-la.
- Réponses en sorties structurées (`format` = schéma JSON) + `think: false`
  pour les modèles à raisonnement. **Le parsing doit rester tolérant** :
  malgré le schéma, les modèles renvoient tantôt un bloc ```json, tantôt une
  **liste** au premier niveau au lieu de `{"findings": [...]}`.
- Demande au modèle des clés **ASCII** et traduis-les ensuite : les clés
  accentuées dégradent nettement la fiabilité des petits modèles.
- N'écoute que sur `127.0.0.1`. Renvoie un **503 explicite** si Ollama est
  injoignable ou si un modèle manque.
- Fournis un script de test envoyant du code volontairement erroné et
  **vérifiant** que l'erreur est détectée.

## Étape 3 — Extension navigateur pour Overleaf (Manifest V3)

- Overleaf utilise **CodeMirror 6**. L'`EditorView` s'obtient via
  `document.querySelector('.cm-content').cmView.view`, ce qui **impose
  `"world": "MAIN"`** dans le content script : un monde isolé ne voit pas les
  propriétés JS des nœuds DOM. Prévois un pont `postMessage` entre les deux.
- **Fais les requêtes réseau depuis le service worker**, pas depuis le content
  script : cela élimine d'un coup le blocage mixed-content et le préflight CORS.
- Double les raccourcis : `chrome.commands` **et** un `keydown` en phase de
  capture, car Chromium réserve déjà certaines combinaisons.
- Affiche un diff avant/après et un bouton *Appliquer* par suggestion. Les
  diagnostics sont fiables, **les corrections automatiques ne le sont pas** :
  l'utilisateur doit relire.

## Étape 4 — Extension JupyterLab

- Le mode se déduit du kernel actif (nom contenant `sage` → mode `sage`).
- Trois écarts nécessaires au template officiel, sinon le build échoue :
  - `.yarnrc.yml` → `nodeLinker: node-modules` (`tsc` ne résout pas les modules
    en mode Plug'n'Play, qui est le défaut de `jlpm`) ;
  - `skipLibCheck: true` (des `.d.ts` de dépendances exigent TypeScript ≥ 5.7) ;
  - `moduleResolution: "bundler"` (requis par `@jupyterlab/lsp`).
- Sur Arch, `pip install -e .` échoue (PEP 668, Python « externally managed »).
  Plutôt que `--break-system-packages`, **lie symboliquement** le dossier
  `labextension` construit dans
  `~/.local/share/jupyter/labextensions/<nom>` : c'est ce que fait
  `jupyter labextension develop`.
- Vérifie avec `jupyter labextension list` (attendu : `enabled OK`), puis que
  le `remoteEntry.js` est bien servi en HTTP.

## Étape 5 — Dashboard Sampana

Clone <https://github.com/raantss18/sampana>, remplis `config/services.json`
à partir de l'inventaire de l'étape 0, puis lance `./install.sh`.

Décide `route` et `embed` **par mesure, pas par supposition** :

```bash
curl -sD - -o /dev/null http://127.0.0.1:PORT/ | grep -iE 'x-frame-options|frame-ancestors'
```

Rappels que le générateur applique déjà, à ne pas contourner :

- Jamais de bloc `http://127.0.0.1:PORT` dans le Caddyfile — Caddy filtrerait
  sur le `Host` et répondrait **200 vide** à tout ce qui vient de Tailscale.
  Utilise `:PORT` + `bind 127.0.0.1`.
- Jamais `admin off` (casse `systemctl reload caddy`).
- Jamais de `redir /x /x/` devant Next.js (boucle 308 infinie).
- Les préfixes d'API passent **avant** les préfixes applicatifs qu'ils
  préfixent.

Configurations à appliquer côté services :
- JupyterLab : `c.ServerApp.base_url`, plus `allow_origin` = l'URL Tailscale,
  sans quoi les websockets (donc l'exécution des cellules) sont refusés.
- Next.js : `basePath` + `assetPrefix`, activés par variable d'environnement et
  injectés par un **drop-in** systemd (l'unité principale est réécrite par
  l'installeur de l'app).
- Syncthing : GUI en **HTTPS** (donc `https+insecure` en amont) et
  `insecureSkipHostcheck` pour accepter un `Host` distant.

## Étape 6 — Vérification, sans complaisance

- Teste **via l'URL Tailscale**, jamais seulement en `localhost` : le piège du
  `Host` Caddy ne se voit que par là. Inclus une URL inexistante dans tes tests :
  elle **doit** renvoyer 404. Si elle renvoie 200, ton routage est cassé même si
  tout le reste semble marcher.
- Suis les redirections (`curl -L`) et vérifie l'URL finale.
- Vérifie que rien n'écoute sur l'IP du LAN.
- `systemctl is-enabled` sur chaque unité, `Linger=yes`, et propose-moi un
  redémarrage réel : c'est la seule preuve que la stack remonte seule.
- Dis-moi explicitement **ce que tu n'as pas pu tester** (typiquement : les
  extensions navigateur, qui exigent une interaction humaine).

## Livrables

Documentation indiquant : URL finale, port de chaque service, emplacement des
sauvegardes, procédure de restauration, et l'ordre de démarrage des services.
