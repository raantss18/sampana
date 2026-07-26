# Prompt pour un agent IA — installer Sampana

Destiné à **Claude Code** ou à tout agent capable d'exécuter des commandes.
Il décrit Sampana **dans son état actuel, débogué**, et encode les pièges déjà
rencontrés. Chacun a coûté des heures : ne les retirez pas.

Copiez tout ce qui suit la ligne, adaptez le bloc **Contexte**, et donnez-le à
l'agent depuis un terminal sur la machine cible.

---

## Contexte

Machine Linux avec systemd. Adapte les chemins, le nom d'hôte et les adresses à
ce que tu trouves réellement — ne suppose rien.

Objectif : installer **Sampana**, un tableau de bord qui rassemble mes services
auto-hébergés derrière une seule adresse et un mot de passe unique, accessible
par Tailscale et par le réseau local, avec un mode invité pour une classe.

## Règles de travail

**Vérifie avant d'agir, et vérifie après.** Plusieurs outils de cette pile
**renvoient un code de sortie 0 sans avoir rien fait**. Un code de retour ne
prouve rien : contrôle le contenu réel du fichier, l'en-tête réellement servi, le
processus réellement en écoute.

**N'exécute aucune commande destructive avant d'avoir inventorié l'existant et
obtenu ma confirmation.**

**Ne teste pas depuis la machine ce qui doit marcher depuis ailleurs.** Une
requête vers l'adresse tailnet de la machine, émise depuis cette machine, ne
traverse pas le tunnel : elle ne reproduit pas ce que vit un téléphone.

**Quand je signale une panne, demande le message d'erreur exact avant de
diagnostiquer.** « La page ne marche pas » recouvre une boucle de redirection, un
DNS absent et un serveur éteint — trois causes sans rapport. Une capture d'écran
vaut mieux qu'une hypothèse.

## Étape 0 — Inventaire, sans rien modifier

Relève et rapporte :

- Ports en écoute (`ss -tlnp`), en distinguant `127.0.0.1` d'une adresse
  routable. Un service qui écoute sur `0.0.0.0` restera accessible **sans** le
  mot de passe maître.
- Unités systemd **système** *et* **utilisateur** (`systemctl --user`). Beaucoup
  de services tournent en unité utilisateur ; c'est facile à manquer.
- Conteneurs Docker **et** Podman.
- `loginctl show-user $USER -p Linger` — sans `Linger=yes`, aucune unité
  utilisateur ne démarre au boot.
- État de Tailscale, et si les certificats HTTPS sont activés pour le tailnet.

## Étape 1 — Installation

```bash
git clone https://github.com/raantss18/sampana.git
cd sampana
cp config/sampana.env.example   config/sampana.env
cp config/services.example.json config/services.json
```

Remplis `config/services.json` à partir de l'inventaire, puis lance
`./install.sh`.

**Le choix décisif est `route`, service par service :**

- `path` — l'application accepte un préfixe (`base_url`, `basePath`). Servie sous
  `https://hôte/nom/`.
- `port` — l'application exige la racine. Servie sur son propre port.

Tout mettre en sous-chemin **ne marche pas** : beaucoup d'applications fabriquent
des adresses absolues depuis la racine.

Décide `route` et `embed` **par mesure, pas par supposition** :

```bash
curl -sD - -o /dev/null http://127.0.0.1:PORT/ | grep -iE 'x-frame-options|content-security-policy'
```

Un seul en-tête ne suffit pas à conclure, et il faut suivre toute la chaîne de
redirection : `bin/check-embed.py <url>` fait les deux.

## Étape 2 — Les pièges, dans l'ordre où ils frappent

### Caddy

- **N'écris jamais un bloc `http://127.0.0.1:PORT`.** Caddy filtrerait sur le
  `Host` ; Tailscale transmet le nom `.ts.net`, aucun site ne correspondrait, et
  Caddy répondrait **200 avec un corps vide** sur toutes les URL. Les tests en
  local passent, ceux par Tailscale non. Écris `:PORT` avec `bind`.
- **`forward_auth` s'applique AVANT les blocs `handle`.** Sans matcher excluant
  `/auth/*`, la page de connexion se protège elle-même et le navigateur boucle
  indéfiniment. Tout bloc protégé doit router `/auth/*` **et** l'exclure.
- **Ne mets pas `admin off`** : `systemctl reload caddy` passe par l'API
  d'administration locale.
- **Valide avant d'installer, et vérifie que le rechargement a réussi.** Un
  rechargement raté n'applique rien et garde l'ancienne configuration **en
  silence** : le fichier sur le disque est à jour, le service sert l'ancien.
- **Le durcissement systemd interdit à Caddy d'écrire sous `/var/log`**, quels
  que soient le propriétaire et les droits du dossier. N'ajoute pas de journal de
  fichier sans l'avoir testé — sinon plus rien ne se recharge.
- **Un bloc adressé par le seul port devient la politique de certificats par
  défaut.** Si l'un demande un certificat et les autres non, Caddy refuse de
  démarrer. Déclare `cert_issuer internal` en global.

### Tailscale

- `tailscale serve` filtre sur le **nom d'hôte** : l'adresse `100.x` brute ne
  passe pas par lui, elle atteint directement ce qui écoute.
- **Le port publié et le port d'écoute doivent différer.** Le démon tient déjà
  `100.x:PORT` pour publier ; si Caddy tentait `0.0.0.0:PORT`, la collision
  l'empêcherait de démarrer et emporterait tout le tableau de bord. Fais écouter
  Caddy ailleurs et pointe `tailscale serve` dessus.
- **Funnel n'accepte que 443, 8443 et 10000.** Trois créneaux, pas un de plus.
- Un amont servi en TLS par une autorité locale se déclare `https+insecure://`
  — syntaxe propre à `tailscale serve`. Côté Caddy, c'est un bloc `transport`.
- **Si un nom `.ts.net` ne résout pas** sur un appareil, ce n'est presque jamais
  le serveur : l'appareil court-circuite le DNS de Tailscale (DNS privé Android,
  DNS sécurisé du navigateur). Compare `dig @1.1.1.1 <nom>` et
  `getent hosts <nom>` : le DNS public renvoie l'entrée publique, où seuls les
  ports Funnel répondent.

### Navigateur

- **Sers tes pages avec `Cache-Control: no-cache`.** Sans consigne, le navigateur
  applique son heuristique et garde l'ancienne version : un correctif déployé
  reste invisible, et rien ne le signale. Limite la consigne à tes fichiers, pour
  ne pas priver de cache les paquets JavaScript des applications proxifiées.
- **`X-Frame-Options: DENY` interdit le cadre même depuis la même origine.** Un
  site qui héberge à la fois le cadre et les outils affichés dedans doit poser
  `SAMEORIGIN`.
- **Une iframe dimensionnée par un pourcentage** exige un parent à hauteur
  définie. Quand celle-ci vient d'un étirement flex, le calcul échoue et l'iframe
  retombe à sa hauteur par défaut, 150 px. Cale-la sur les quatre bords.
- **Un élément flex vaut `min-height: auto`** : il refuse de descendre sous la
  taille de son contenu et déborde. Pose `min-height: 0`.
- **Certaines applications exigent un contexte sécurisé** et refusent de démarrer
  en HTTP — tout ce qui diffuse en WebCodecs, par exemple. Sers-les en HTTPS même
  en local, avec l'autorité locale de Caddy et un certificat émis à la demande :
  l'adresse de la machine change d'un réseau à l'autre.

### Authentification

- **Ne bloque jamais sur un mot de passe correct.** Derrière un tunnel, tout le
  trafic partage une adresse source : un verrouillage dur offrirait à un inconnu
  le moyen de fermer la porte au propriétaire. Ralentis, ne bloque pas.
- **Si tu ajoutes un verrouillage par inactivité**, la page de connexion doit
  consulter la révocation, sans quoi elle croira valide un jeton que la
  vérification rejette : chacune renverra vers l'autre, boucle infinie, sans accès
  au formulaire. Seul un effacement des cookies débloque.
- **Échappe toute valeur extérieure** insérée dans une page. Le cas grave n'est
  pas la page publique mais la page d'administration : un nom saisi par un
  utilisateur non authentifié y exécuterait du code avec la session maître.

### Conteneurs

- Un conteneur **sans réseau** ne peut pas écouter sur un port : expose une
  socket Unix.
- Une image officielle est un paquet **déjà compilé**. Y modifier une chaîne
  demande une substitution à la construction — et cette substitution doit
  **échouer bruyamment** si le motif a disparu, sinon tu construis une image
  silencieusement inchangée.
- **Un état à moitié installé est pire que les deux extrêmes.** Désactiver
  l'extension serveur d'un greffon en laissant sa partie navigateur produit des
  pannes plus obscures que de tout garder ou tout retirer.

## Étape 3 — Vérification

Ne conclus pas sans avoir vérifié, **sur les deux chemins d'accès** — par
Tailscale et par le réseau local :

- Chaque service répond (302 = redirection vers la connexion, donc joignable et
  protégé ; 000 = injoignable).
- La page de connexion s'affiche depuis **chaque** port d'outil, pas seulement
  depuis le tableau de bord.
- Les fichiers servis sont **identiques** à ceux du dépôt — compare le contenu,
  pas les dates.
- La sonde de santé voit tous les services déclarés : un service absent du
  fichier de cibles n'est jamais interrogé et s'affiche éteint.
- Après redémarrage de la machine, tout revient seul.
- Traversée de chemin sur le dossier partagé : essaie `../`, les formes encodées
  et les doublements.

## Étape 4 — Ce qu'il ne faut pas faire

- **Ne réinstalle pas les extensions de collaboration JupyterLab** sur l'image en
  service. Elles exigent une version antérieure, et le décalage détourne le canal
  du noyau : résultats absents, « File ID error », chargement sans fin.
- **Ne publie pas le tableau de bord en Funnel** sans me le demander : la page de
  connexion deviendrait atteignable depuis tout Internet.
- **N'utilise pas `rsync --delete`** vers la racine servie : elle contient des
  fichiers engendrés qui n'existent pas dans le dépôt.

## Rapport attendu

À la fin, dis-moi :

1. Ce qui fonctionne, avec la preuve (code HTTP, contenu comparé).
2. Ce qui ne fonctionne pas, sans l'enrober.
3. Ce que tu as laissé de côté, et pourquoi.
4. Ce qui reste à faire de mon côté — mots de passe, réglages hors dépôt.
