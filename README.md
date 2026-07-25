<div align="center">

<img src="web/logo.svg" width="72" alt="Sampana" />

# Sampana

**Un point d'entrée unique, en HTTPS, pour tous les services auto-hébergés d'une machine — accessible partout dans le monde via Tailscale, exposé à personne d'autre.**

*« Sampana » : branche, ramification, en malgache.*

</div>

---

## Ce que c'est

Un dashboard qui rassemble vos services locaux (JupyterLab, Overleaf, un
assistant IA, un terminal…) derrière **une seule URL HTTPS**, servie par Caddy
et exposée uniquement à votre tailnet.

- **Rien n'écoute sur le LAN.** Caddy est lié à `127.0.0.1` ; l'exposition passe
  exclusivement par `tailscale serve`, qui fournit aussi le TLS.
- **État de santé en direct.** Chaque service est sondé, le dashboard affiche
  en ligne / dégradé / hors ligne avec la latence.
- **Bouton retour partout.** Les services s'ouvrent dans un shell avec un
  bandeau Sampana et un bouton *Retour*.
- **Un seul mot de passe maître.** Une connexion, et tous les services sont
  ouverts — y compris ceux servis sur leur propre port. Aucun mot de passe
  interne à saisir ensuite.
- **Terminal web** intégré, plus Tailscale SSH.
- **Déclaratif.** Un fichier JSON décrit les services ; le Caddyfile, le
  manifeste web et les commandes `tailscale serve` en sont dérivés.
- **Idempotent.** `./install.sh` peut être relancé sans risque.

## Le problème que ça résout

Tout mettre sous des sous-chemins (`/overleaf/`, `/jupyter/`…) **ne fonctionne
pas** : beaucoup d'applications génèrent des URL absolues depuis la racine et
cassent derrière un préfixe.

Sampana assume une **architecture hybride**, décrite service par service dans
la configuration :

| `route` | Pour qui | Résultat |
|---|---|---|
| `path` | Apps supportant un préfixe (`base_url`, `basePath`) | `https://hôte/jupyter/` |
| `port` | Apps exigeant la racine | `https://hôte:10443/` |

Le champ `embed` indique si l'app tolère une iframe. Sinon, sa carte ouvre un
nouvel onglet — le dashboard reste alors accessible dans l'onglet précédent.

## Installation

```bash
git clone https://github.com/raantss18/sampana.git
cd sampana
cp config/sampana.env.example      config/sampana.env
cp config/services.example.json    config/services.json
$EDITOR config/services.json       # décrivez vos services
./install.sh
```

Prérequis : `caddy`, `ttyd`, `tailscale`, `python3`, `curl`.
Sur Arch / EndeavourOS : `sudo pacman -S --needed caddy ttyd tailscale python`

Les **certificats HTTPS Tailscale** doivent être activés pour votre tailnet :
<https://login.tailscale.com/admin/dns> → *HTTPS Certificates* → **Enable**.
`install.sh` s'arrête avec un message clair si ce n'est pas fait.

`config/sampana.env` et `config/services.json` sont **non versionnés** : ils
contiennent votre nom d'hôte et votre topologie.

## Déclarer un service

```jsonc
{
  "id": "jupyter",
  "label": "JupyterLab",
  "desc": "Notebooks et environnement ML.",
  "icon": "notebook",
  "route": "path",              // "path" ou "port"
  "path": "/jupyter",
  "upstream": "127.0.0.1:8888",
  "embed": true,                // false si X-Frame-Options / frame-ancestors
  "trailing_slash": true,       // le backend exige le / final
  "probe": "/",                 // chemin sondé pour l'état de santé
  "probe_expect": [200, 401]    // codes considérés comme sains
}
```

Options : `strip` (retire le préfixe, pour une API), `hidden` (proxifié mais
masqué du dashboard), `open_path`
(ex. `/docs`), `upstream_scheme` (`https+insecure` si le backend est en TLS
auto-signé, comme Syncthing).

**Ne devinez pas `embed`, mesurez-le :**
```bash
bin/check-embed.py
```
Le script compare chaque déclaration à la réalité et signale les écarts.
`install.sh` le lance automatiquement et vous avertit.

Il faut regarder **`X-Frame-Options` *et* `frame-ancestors`** — une application
peut n'envoyer que l'un des deux — et **suivre les redirections** : Overleaf
autorise l'encadrement sur sa page de connexion mais le refuse sur la 302 qui y
mène. Un service encadré à tort n'affiche qu'un cadre blanc.

## Utilisation

| Raccourci | Effet |
|---|---|
| `/` | Focus sur la recherche |
| `Entrée` | Ouvre le premier résultat |
| `Échap` | Efface la recherche · revient au dashboard depuis un service |

## Architecture

```
                    tailnet (WireGuard)
                            │
                    tailscale serve ── TLS
                            │
        ┌───────────────────┼────────────────────┐
        ▼                   ▼                    ▼
   :443 → Caddy      :10443 → Overleaf   :10445 → Syncthing
        │                (racine)             (racine)
        ├── /            dashboard statique
        ├── /app.html    shell (bandeau + bouton retour)
        ├── /auth/*      connexion (mot de passe maître)
        ├── /api/status  sonde de santé (stdlib Python)
        ├── /jupyter/    → 127.0.0.1:8888
        ├── /mi-saina/   → 127.0.0.1:3001
        └── /terminal/   → ttyd
```

## Pièges rencontrés

Chacun a coûté du temps ; ils sont désormais évités par construction.

- **Un bloc Caddy `http://127.0.0.1:8088` filtre sur le `Host`.** Tailscale
  transmet `Host: hôte.ts.net`, aucun site ne correspond, et Caddy répond
  **200 avec un corps vide** sur *toutes* les URL. Les tests en localhost
  passent, ceux via Tailscale non. Solution : `:8088` + `bind 127.0.0.1`.
- **`admin off` casse `systemctl reload caddy`** : le reload passe par l'API
  admin locale.
- **Pas de `redir /x /x/`** devant Next.js : il retire le slash final, les deux
  règles se renvoient la balle (308 infini). Sampana ne génère cette
  redirection que si `trailing_slash` est déclaré.
- **Ordre des routes** : un préfixe d'API (`/mi-saina-api`) doit être déclaré
  avant le préfixe applicatif dont il partage le début. Le générateur trie.
- **`tailscale cert` refuse `/dev/null`** (« not a regular file ») : utiliser
  un fichier régulier pour tester la disponibilité des certificats.
- **Ports différents = origines différentes.** Une app en `frame-ancestors
  'self'` sur `:10445` ne peut pas être encadrée depuis `:443`.
- **Un seul en-tête ne suffit pas à conclure.** Overleaf n'a pas de
  `frame-ancestors` sur `/login`, mais envoie `X-Frame-Options: SAMEORIGIN` sur
  toutes ses réponses et `frame-ancestors 'none'` sur la redirection. Vérifier
  les deux en-têtes, sur toute la chaîne de redirection — d'où `check-embed.py`.

## Authentification

Un **mot de passe maître unique** protège l'ensemble. `install.sh` le demande
à la première exécution et n'en conserve qu'une empreinte **scrypt** dans
`~/.config/sampana/auth.json` (mode 600) — jamais le mot de passe en clair.

Pourquoi une session par cookie plutôt que Basic Auth : les cookies sont
attachés au **nom de domaine et ignorent le port**. Une seule connexion vaut
donc pour `https://hôte/` *et* `https://hôte:10443/`. Basic Auth aurait
redemandé le mot de passe sur chaque port, chacun étant une origine distincte.

Caddy interroge le service d'authentification via `forward_auth` avant chaque
requête. Les pages de connexion sont exclues par un matcher : sans cela,
`/auth/login` se protégerait elle-même et le navigateur bouclerait sur la
redirection.

```bash
rm ~/.config/sampana/auth.json && ./install.sh   # changer le mot de passe
```

Session valable 30 jours (`ttl` dans `auth.json`). Déconnexion : `/auth/logout`.

### Ce que Sampana ne protège pas

Les services n'ont **plus de mot de passe propre** : ils sont protégés en
amont. Cela suppose qu'ils ne soient joignables que via Sampana.
Vérifiez qu'aucun n'écoute sur une adresse routable :

```bash
ss -tlnp | grep -v '127.0.0.1'
```

Un service qui écoute sur `0.0.0.0` reste accessible depuis le LAN **sans**
passer par le mot de passe maître.

Enfin, `AUTH_ENABLED=0` désactive toute authentification : à ne faire que si
chaque service porte déjà la sienne.

## Mode invité

Un second monde, étanche du premier : **sans compte, sans persistance**, pour
les étudiants. Il s'ouvre et se ferme depuis le tableau de bord.

| | Session normale | Session invitée |
|---|---|---|
| Entrée | mot de passe maître | prénom, nom, code de séance |
| Cookie | `sampana_session` | `sampana_guest` |
| Portée | tous les services, dont un shell | JupyterLab, Lean4Web, LaTeX Lab, assistant IA, dossier partagé |
| Persistance | complète | aucune |

Les deux cookies portent des **clés de signature distinctes**. C'est ce qui
empêche un jeton invité d'être accepté comme session normale — donc d'ouvrir
un shell. Sans cela, connaître le code de séance suffirait : il se dit à voix
haute.

### Isolation

Le JupyterLab invité tourne **sans aucune interface réseau** (`--network none`).
Un notebook exécute du code arbitraire, et l'instance peut être publiée sur
Internet : sans réseau, ce code ne peut ni miner, ni relayer d'attaque depuis
votre IP, ni joindre vos autres services sur `127.0.0.1` — qui n'ont plus de
mot de passe propre et comptent sur Sampana en amont.

Conséquence : le conteneur ne peut pas écouter sur un port. Il expose une
**socket Unix** que Caddy vient chercher. Deux exceptions, étroites et
explicites :

- une socket vers **Ollama**, pour que `jupyter-ai` fonctionne ;
- le **dossier partagé**, monté en lecture seule.

### Séance

Chaque ouverture génère un **code et un mot de passe LaTeX Lab neufs**, ainsi
qu'une nouvelle clé de signature : les jetons de la séance précédente cessent
d'être honorés. Le code porte la date pour être dictable, mais se termine par
des chiffres tirés au sort — une valeur calculée depuis la seule date serait
devinable, or le portail est joignable depuis Internet quand le Funnel est
ouvert.

La durée se choisit à l'ouverture. Les étudiants sont prévenus **10 puis 5
minutes** avant la fin, y compris depuis l'intérieur d'un outil.

### Feuille de présence

Caddy interroge le portail avant *chaque* requête d'un invité : la présence,
l'outil actif et la chronologie s'en déduisent, sans rien installer chez
l'étudiant. Le tableau de bord affiche qui est connecté, sur quoi, et depuis
combien de temps. À la fermeture, la séance est archivée pour un suivi
ultérieur.

La page d'entrée en informe explicitement l'étudiant.

### Hors ligne et partage de connexion

Tout fonctionne **sans Internet** : les pages n'ont aucune dépendance externe,
les modèles sont locaux. En partage de connexion, le tableau de bord détecte
les adresses de hotspot et les met en tête des consignes à dicter.

Sans route par défaut, la publication Funnel est **ignorée immédiatement** au
lieu d'échouer après des dizaines de secondes : la séance s'ouvre en salle,
seule l'exposition extérieure est impossible.

### Ports

Tailscale Funnel n'accepte que **443, 8443 et 10000**. Le dashboard occupe 443,
il reste donc 10000 (portail, JupyterLab, Lean4Web, dossier partagé — tous
routables en sous-chemin) et 8443 (LaTeX Lab, qui exige la racine d'un port).
L'assistant IA invité n'a plus de créneau : il reste accessible en salle.

### Bouton retour

Aucun des outils n'en propose. Ils sont donc affichés dans une **enveloppe**
surmontée d'une barre « ← Outils ». Elle est servie **sur le port de l'outil**,
jamais sur celui du tableau de bord : LaTeX Lab renvoie `X-Frame-Options:
SAMEORIGIN`, et un port différent est une origine différente.

## Désinstallation

```bash
./uninstall.sh
```

Retire uniquement ce que Sampana a installé, restaure le `Caddyfile` précédent,
et ne touche à aucun service proxifié.

## Licence

MIT
