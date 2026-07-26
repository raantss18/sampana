<div align="center">

<img src="web/logo.svg" width="72" alt="Sampana" />

# Sampana

**Tous vos services auto-hébergés derrière une seule adresse et un seul mot de
passe — plus un mode classe qui les ouvre à des invités, sans compte et sans
trace.**

*« Sampana » : branche, ramification, en malgache.*

</div>

---

## Ce que c'est

Un tableau de bord qui rassemble les services d'une machine — notebooks, serveur
LaTeX, assistant IA, terminal, ce que vous voulez — derrière une seule porte
gardée par un mot de passe unique.

Et, en un clic, un **mode invité** : vos outils deviennent accessibles à un
groupe de personnes sur le réseau local, sans compte, sans accès à vos fichiers,
et tout disparaît à la fermeture.

Sampana est né pour une salle de classe. Il sert aussi bien à un poste personnel
qu'on veut atteindre depuis son téléphone.

## Ce qu'il fait

- **Une adresse, un mot de passe.** Les services n'ont plus de mot de passe
  propre ; Sampana les garde en amont. Une connexion vaut pour tous.
- **Accessible de partout, exposé à personne.** Par Tailscale hors de chez vous,
  par le réseau local en salle — y compris sur un partage de connexion sans
  Internet.
- **Mode classe.** Un interrupteur, un code de séance à dicter, une durée. Les
  invités arrivent sur un choix d'outils ; à la fin, tout est effacé.
- **Feuille de présence vivante.** Qui est connecté, sur quel outil, depuis
  combien de temps, qui lève la main. Historique, export CSV, travail de chacun
  en ZIP.
- **Dossier partagé.** Vous y déposez fichiers et dossiers ; les invités les
  lisent seulement. Un double-clic ouvre l'outil correspondant.
- **Partage en direct.** Un bouton par outil publie une instance ou un fichier
  vers les invités, dans les deux sens.
- **État de santé** de chaque service, en direct.
- **Déclaratif.** Un fichier JSON décrit vos services ; toute la configuration en
  est dérivée. `./install.sh` est rejouable sans risque.

## Ce dont vous avez besoin

- Linux avec **systemd**
- **Caddy**, **Python 3.11+** (aucune dépendance : bibliothèque standard), `curl`
- **Tailscale**, pour y accéder hors de votre réseau
- **Podman**, pour le mode invité et les outils conteneurisés
- `ttyd` si vous voulez le terminal web
- Une carte **NVIDIA** avec `nvidia-container-toolkit`, uniquement si vous voulez
  le GPU dans les notebooks

Sur Arch et dérivées :
`sudo pacman -S --needed caddy ttyd tailscale python podman`

Les **certificats HTTPS Tailscale** doivent être activés pour votre tailnet :
<https://login.tailscale.com/admin/dns> → *HTTPS Certificates* → **Enable**.
L'installation s'arrête avec un message clair si ce n'est pas fait.

## Démarrer

```bash
git clone https://github.com/raantss18/sampana.git
cd sampana

cp config/sampana.env.example   config/sampana.env
cp config/services.example.json config/services.json

$EDITOR config/services.json    # décrivez vos services
./install.sh
```

L'installation demande un mot de passe maître à la première exécution et n'en
garde qu'une empreinte scrypt. Elle affiche ensuite l'adresse de votre tableau de
bord.

`config/sampana.env` et `config/services.json` ne sont **pas versionnés** : ils
décrivent votre machine.

Pour tout retirer : `./uninstall.sh`. Il ne touche qu'à ce que Sampana a
installé, restaure le `Caddyfile` précédent, et laisse vos services intacts.

## Décrire un service

```json
{
  "id": "jupyter",
  "label": "JupyterLab",
  "desc": "Notebooks Python.",
  "route": "path",
  "path": "/jupyter",
  "upstream": "127.0.0.1:8888",
  "embed": true
}
```

Le choix qui compte est `route` :

| `route` | Pour quelles applications | Adresse produite |
|---|---|---|
| `path` | celles qui acceptent un préfixe (`base_url`, `basePath`) | `https://hôte/jupyter/` |
| `port` | celles qui exigent la racine | `https://hôte:10443/` |

Tout mettre sous des sous-chemins **ne marche pas** : beaucoup d'applications
fabriquent des adresses absolues depuis la racine et cassent derrière un préfixe.
Sampana assume donc une architecture mixte, choisie service par service.

| Champ | Effet |
|---|---|
| `embed` | l'application tolère d'être affichée dans un cadre |
| `hidden` | service réel, mais absent du tableau de bord |
| `strip` | retire le préfixe avant de transmettre |
| `trailing_slash` | ajoute la barre finale (à éviter devant Next.js, voir plus bas) |
| `secure_context` | sert ce service en HTTPS même en local |
| `collab` | branche un relais de collaboration en direct |

Pour trancher `embed` : `bin/check-embed.py <url>` suit toute la chaîne de
redirection et vérifie les deux en-têtes qui gouvernent l'encadrement.

## Le mode invité

Depuis le tableau de bord : une durée, un interrupteur. Sampana affiche un guide
à dicter — une adresse et un code de séance, tous deux renouvelés à chaque fois.

**Ce que voient les invités** : un choix d'outils, le dossier partagé en lecture
seule, un éditeur de notes, un tableau blanc. Ils sont prévenus 10 puis 5 minutes
avant la fermeture, y compris depuis l'intérieur d'un outil.

**Ce qu'ils ne voient pas** : vos fichiers.

|  | Session normale | Session invitée |
|---|---|---|
| Entrée | mot de passe maître | prénom, nom, code de séance |
| Portée | tous les services, dont un terminal | notebooks, LaTeX, Lean, IA, dossier partagé |
| Persistance | complète | aucune |

Les deux sessions portent des **clés de signature distinctes**. C'est ce qui
empêche un jeton invité d'être accepté comme session normale — donc d'ouvrir un
terminal. Sans cela, connaître le code de séance suffirait : il se dit à voix
haute.

### Isolation

Le JupyterLab invité tourne **sans aucune interface réseau**. Un notebook exécute
du code arbitraire, et l'instance peut être publiée : sans réseau, ce code ne
peut ni miner, ni relayer d'attaque depuis votre adresse, ni joindre vos autres
services sur `127.0.0.1` — qui n'ont plus de mot de passe propre.

Conséquence : le conteneur ne peut pas écouter sur un port. Il expose une
**socket Unix** que Caddy vient chercher. Deux exceptions, étroites et
explicites : une socket vers l'assistant IA local, et le dossier partagé monté en
lecture seule.

L'espace de travail vit en mémoire et disparaît à la fermeture.

### Séance

Chaque ouverture génère un code neuf et une nouvelle clé de signature : les
jetons de la séance précédente cessent d'être honorés. Le code porte la date pour
être dictable, mais se termine par des chiffres tirés au sort — une valeur
calculée depuis la seule date serait devinable, or le portail peut être joignable
depuis Internet.

### Feuille de présence

Caddy interroge le portail avant *chaque* requête d'un invité : la présence,
l'outil actif et la chronologie s'en déduisent, sans rien installer chez
l'utilisateur. La page d'entrée en informe explicitement.

### Hors ligne

Tout fonctionne sans Internet : les pages n'ont aucune dépendance externe, les
modèles sont locaux. En partage de connexion, le tableau de bord détecte les
adresses du hotspot et les met en tête des consignes à dicter.

## Comment on y accède

Sampana écoute sur deux chemins, et les pages choisissent le bon toutes seules :

- **Par Tailscale** — `https://votre-machine.votre-tailnet.ts.net`, le TLS est
  fourni par `tailscale serve`.
- **Par le réseau local** — `http://<adresse-de-la-machine>:8088`, utile en
  salle, indispensable sans Internet.

Les services à port dédié écoutent sur **deux ports** : celui que publie
Tailscale, et un port local à partir de 11000. Les deux ne peuvent pas être
identiques — le démon Tailscale tient déjà le port publié, et la collision
empêcherait Caddy de démarrer, emportant tout le tableau de bord.

> **Si le nom `.ts.net` ne résout pas** sur un appareil, c'est que son système
> court-circuite le DNS de Tailscale (DNS privé Android, DNS sécurisé du
> navigateur). L'adresse `100.x.y.z` du tailnet fonctionne toujours.

### Exposition sur Internet

Tailscale Funnel n'accepte que **443, 8443 et 10000**. Les outils qui ne rentrent
pas dans ces créneaux restent accessibles en salle, mais pas depuis l'extérieur.

## Sécurité

**Un mot de passe maître**, haché en scrypt dans `~/.config/sampana/auth.json`
(mode 600). Jamais stocké en clair.

Une session par cookie plutôt que Basic Auth : les cookies sont attachés au nom
de domaine et **ignorent le port**, si bien qu'une connexion vaut pour la racine
*et* pour chaque port dédié. Basic Auth aurait redemandé le mot de passe sur
chacun, chaque port étant une origine distincte.

- **Verrouillage après 15 minutes d'inactivité**, sans rien interrompre : les
  séances invitées et les conteneurs continuent de tourner. La fenêtre est
  glissante et tenue côté serveur — `forward_auth` ne permet pas de renvoyer un
  cookie rafraîchi, un registre d'activité fait le même travail.
- **Ralentissement exponentiel** sur les tentatives ratées, plafonné. Il ne
  bloque jamais un mot de passe correct : derrière un tunnel, tout le trafic
  partage une adresse source, et un blocage dur offrirait à un inconnu le moyen
  de vous fermer la porte.
- **Valeurs extérieures échappées** partout où elles entrent dans une page. Un
  nom saisi par un invité s'affiche dans la feuille de présence de
  l'administrateur : sans échappement, il y exécuterait du code.

Changer le mot de passe : depuis la section Configuration, ou

```bash
rm ~/.config/sampana/auth.json && ./install.sh
```

### Ce que Sampana ne protège pas

Vos services n'ont plus de mot de passe propre. Cela suppose qu'ils ne soient
joignables **que** par Sampana :

```bash
ss -tlnp | grep -v '127.0.0.1'
```

Un service qui écoute sur `0.0.0.0` reste accessible depuis le réseau local
**sans** passer par le mot de passe maître.

Le tableau de bord est servi en HTTP sur le réseau local : le mot de passe y
circule en clair. Voir [TODO.md](TODO.md).

`AUTH_ENABLED=0` désactive toute authentification — à ne faire que si chaque
service porte déjà la sienne.

### Servir un outil en HTTPS localement

Certaines applications exigent un « contexte sécurisé » et refusent de démarrer
en HTTP — tout ce qui diffuse de la vidéo par WebCodecs, par exemple. Ajoutez
`"secure_context": true` : Caddy le sert alors en TLS avec son autorité locale,
en émettant le certificat à la demande, car l'adresse de la machine change d'un
réseau à l'autre.

L'installation pose cette autorité dans le magasin du système **et** dans celui
des navigateurs, qui ont chacun le leur. Une copie est déposée dans votre dossier
personnel pour vos téléphones et tablettes.

### Un service qui garde son mot de passe

Un serveur LaTeX de type Overleaf gère ses propres comptes et n'expose aucun
moyen de s'en passer. Le mot de passe maître protège la *route* — il vous laisse
arriver à la page de connexion — mais ne peut pas vous y connecter. C'est aussi
pourquoi le mode invité y utilise un compte partagé, dont le mot de passe est
renouvelé à chaque séance et affiché dans le guide.

Pensez à définir un secret de session côté Overleaf ; sans lui, aucune session ne
survit à une recréation du conteneur.

## Carte graphique

Le JupyterLab invité reçoit le GPU de la machine via CDI. Cela ne lui ouvre aucun
accès réseau : le conteneur reste sans interface, seul le périphérique est
partagé.

**La mémoire vidéo n'est pas cloisonnée** : elle est puisée dans une réserve
commune, amputée de ce que retient déjà votre service d'IA local. Un notebook
trop gourmand fera échouer ceux des autres. Deux leviers — libérer le modèle à
l'inactivité (`OLLAMA_KEEP_ALIVE`), et des lots réduits côté utilisateurs.

Sans GPU ou sans `nvidia-container-toolkit`, l'installation retire la déclaration
plutôt que de refuser de démarrer sur un périphérique absent.

## Architecture

```
   tailnet (WireGuard)          réseau local
           │                         │
   tailscale serve ── TLS            │
           │                         │
           └───────────┬─────────────┘
                       ▼
                     Caddy
                       │
    ┌──────────────────┼───────────────────┐
    ▼                  ▼                   ▼
 :8088             :11000+              :8081
 tableau de bord   outils à port        portail invité
    │              dédié
    ├── /              dashboard
    ├── /app.html      cadre (bandeau + retour)
    ├── /auth/*        connexion
    ├── /api/status    sonde de santé
    └── /<service>/    services en sous-chemin
```

Trois services Python sans dépendances : authentification, sonde de santé,
portail invité.

Aucun outil ne propose de bouton retour. Ils sont donc affichés dans une
**enveloppe** surmontée d'une barre « ← Retour », servie **sur le port de
l'outil** et jamais sur celui du tableau de bord : un port différent est une
origine différente, et certaines applications refusent d'être encadrées depuis
ailleurs.

## Pièges rencontrés

Chacun a coûté du temps. Ils sont évités par construction, mais les connaître
évite de les réintroduire.

- **Un bloc `http://127.0.0.1:8088` filtre sur le `Host`.** Tailscale transmet le
  nom `.ts.net`, aucun site ne correspond, et Caddy répond **200 avec un corps
  vide** partout. Les tests en local passent, ceux par Tailscale non. Écrire
  `:8088` avec `bind`.
- **`forward_auth` s'applique AVANT les blocs `handle`.** Sans matcher excluant
  `/auth/*`, la page de connexion se protège elle-même et le navigateur boucle.
- **`X-Frame-Options: DENY` interdit le cadre même depuis la même origine.** Un
  site qui héberge à la fois le cadre et les outils affichés dedans doit poser
  `SAMEORIGIN`.
- **Une iframe dimensionnée en pourcentage** exige un parent à hauteur définie.
  Quand elle vient d'un étirement flex, le calcul échoue et l'iframe retombe à
  150 px. Caler sur les quatre bords.
- **Des pages sans consigne de cache** sont gardées par le navigateur selon son
  heuristique : un correctif déployé reste invisible, sans rien pour le signaler.
- **Un rechargement de Caddy qui échoue n'applique rien** et garde l'ancienne
  configuration en silence. Toujours valider avant d'installer.
- **`admin off` casse `systemctl reload caddy`** — le rechargement passe par
  l'API d'administration locale.
- **Le durcissement systemd interdit à Caddy d'écrire** dans un journal sous
  `/var/log`, quels que soient les droits du dossier.
- **Pas de `redir /x /x/` devant Next.js** : il retire la barre finale, les deux
  règles se renvoient la balle (308 infini).
- **Ordre des routes** : un préfixe d'API doit être déclaré avant le préfixe
  applicatif dont il partage le début. Le générateur trie.
- **Un seul en-tête ne suffit pas à conclure** sur l'encadrement : vérifier
  `X-Frame-Options` *et* `Content-Security-Policy`, sur toute la chaîne de
  redirection.

## Contenu du dépôt

```
bin/render.py        génère Caddyfile, manifeste web et commandes tailscale
bin/check-embed.py   vérifie qu'une application accepte d'être encadrée
config/              vos fichiers (non versionnés) et leurs exemples
services/            services Python, images de conteneurs, unités systemd
web/                 tableau de bord, cadre, portail invité
install.sh           installation, rejouable
deploie-web.sh       déploiement des seules pages
uninstall.sh         retire ce que Sampana a installé
```

## Documentation

- [CHANGELOG.md](CHANGELOG.md) — ce qui a été fait, corrigé, et ce qui a échoué
- [TODO.md](TODO.md) — ce qui reste, et les limites connues
- [docs/CLAUDE-CODE-PROMPT.md](docs/CLAUDE-CODE-PROMPT.md) — reconstruire
  Sampana avec un agent IA

## Licence

MIT — voir [LICENSE](LICENSE).
