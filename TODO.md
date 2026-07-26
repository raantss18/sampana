# À faire

Classé par ce que ça coûte si on ne le fait pas. Ce qui est livré vit dans
[CHANGELOG.md](CHANGELOG.md).

---

## Sécurité — priorité

### Mot de passe maître en clair sur le réseau local

Le tableau de bord est servi en HTTP. Quiconque partage le Wi-Fi de la salle peut
lire le mot de passe au moment de la connexion.

L'autorité de certification locale est déjà en place pour Obsidian, qui exige un
contexte sécurisé. L'étendre au site principal demande d'ajouter
`"secure_context": true` et de faire installer l'autorité sur les appareils de
l'enseignant — la mécanique existe et est décrite dans le README.

*Si on l'ignore : le mot de passe qui ouvre tout circule en clair devant une
classe entière.*

### Aucun pare-feu par défaut

Les ports d'outils (11000 et suivants) et le portail invité écoutent sur toutes
les interfaces. Ils sont protégés par mot de passe, mais rien ne limite qui peut
frapper. Une règle n'acceptant ces ports que depuis le réseau local et le tailnet
suffirait.

### Session de 30 jours

Longue pour un poste d'enseignant. Le verrouillage par inactivité de 15 minutes
compense en partie, mais un cookie volé reste utilisable un mois.

---

## Fonctionnalités promises, non livrées

### Modèles pré-remplis par outil

Le dossier partagé et son montage dans JupyterLab existent, mais rien n'est
pré-rempli côté LaTeX Lab (projets modèles) ni Lean4Web (projets d'exercice).

### Collaboration en direct sur les notebooks

Le partage enseignant → invités marche pour le tableau blanc et les fichiers, pas
pour l'édition simultanée d'un notebook.

`jupyter-collaboration` exige une version de JupyterLab antérieure à celle de
l'image. **Ne pas l'installer sur l'image en service** : ce décalage est la cause
de trois pannes déjà corrigées (résultats absents, « File ID error », chargement
sans fin). À tenter sur une image de test à version figée.

### Assistant IA invité et tableau blanc hors de la salle

Tailscale ne publie que trois ports sur Internet (443, 8443, 10000), tous
attribués. Piste : rendre ces outils routables en sous-chemin d'un port déjà
publié, si l'application le supporte.

---

## Limites connues, à dire aux utilisateurs

### L'espace de travail invité n'est pas attribuable

L'instance JupyterLab est partagée : `/home/jovyan/work` est commun, les fichiers
ne peuvent pas être rattachés à un élève. Le ZIP par élève ne contient que sa
fiche et son parcours. Un vrai rendu individuel demanderait un conteneur par
session — décision initiale contraire, à rediscuter si des examens l'exigent.

### La mémoire graphique n'est pas cloisonnée

Elle est partagée entre tous les élèves, dans une réserve commune amputée de ce
que retient le service d'IA local. Un notebook trop gourmand fait échouer les
autres. Pistes : libérer le modèle à l'inactivité (`OLLAMA_KEEP_ALIVE`), limiter
le nombre de places en séance, ou imposer des lots réduits. Les cartes grand
public n'offrent aucun cloisonnement matériel.

### Le conteneur invité n'a aucun accès réseau

C'est voulu — seul l'assistant local répond, par une socket Unix. Mais un TP qui
s'appuierait sur un service distant échouera : à annoncer aux élèves.

### Obsidian est un bureau distant

Image lourde, et le flux vidéo passe mal sur un téléphone en connexion mobile. À
réserver à un écran d'ordinateur.

### L'image invitée est volumineuse

Environnement scientifique complet, calcul formel et pilotes GPU. À surveiller si
l'espace disque se réduit.

---

## Reproductibilité

### Configuration du serveur LaTeX non versionnée

Les surcharges d'Overleaf sont posées à la main. Une réinstallation les perdrait,
et l'inscription des invités échouerait silencieusement.

### Vérification post-démarrage en dur

`bin/postboot-check.sh` liste des unités écrites à la main. Elles devraient être
déduites de `config/services.json`, comme le reste.

---

## Confort

- **Guide élève imprimable** — une page avec le code de séance en gros.
- **Reprise de séance** — un élève déconnecté doit ressaisir son nom.
- **Quota par élève** — rien ne limite ce qu'un invité peut déposer.

---

## Vérifié, rien à faire

Contrôlé au dernier audit ; à ne pas reprendre sans raison nouvelle.

| Point | Résultat |
|---|---|
| Traversée de chemin (7 variantes, dont encodées) | toutes rejetées |
| Force brute | ralentissement exponentiel plafonné |
| Isolation du JupyterLab invité | réseau coupé, partage en lecture seule |
| Redémarrage machine | tous les services repartent seuls |
| Secrets dans le dépôt et l'historique | aucun |
