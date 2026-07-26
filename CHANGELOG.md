# Journal des modifications

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
Les entrées décrivent l'effet observable, et la raison quand elle n'est pas
évidente.

---

## [Non publié]

Cycle consacré au mode invité (usage en classe), puis à une campagne de
correction déclenchée par des pannes constatées en usage réel.

### Ajouté

- **Mode invité** — session sans compte ni persistance, pour une classe.
  L'enseignant l'active depuis le tableau de bord, dicte un code de séance, et
  les élèves accèdent à JupyterLab, LaTeX Lab, Lean4Web, un tableau blanc et un
  lecteur de fichiers. Tout est effacé à la fermeture.
- **Isolation stricte du JupyterLab invité** — conteneur sans réseau
  (`Network=none`), espace de travail en mémoire (`tmpfs`), dossier partagé
  monté en lecture seule. L'assistant IA reste joignable par une socket Unix :
  aucune route réseau n'est ouverte pour autant.
- **Feuille de présence et suivi** — liste des appareils connectés avec les noms
  saisis par les élèves, outil en cours, parcours condensé, main levée, temps de
  présence. Historique des séances conservé.
- **Exports** — présence en CSV, travail d'un élève en ZIP, ou tout d'un coup.
- **Partage enseignant → invités** — un bouton par outil publie une instance ou
  un fichier vers la classe, dans les deux sens.
- **Tableau blanc collaboratif** (Excalidraw) avec relais de collaboration en
  direct, et **Obsidian** en bureau distant pour l'enseignant.
- **Passage GPU** vers JupyterLab (CUDA), pour les travaux d'apprentissage
  automatique.
- **Fonctionnement sans Internet** — le mode invité, le mode enseignant et le
  guide des élèves marchent sur un simple partage de connexion.
- **Verrouillage par inactivité** — le mode enseignant redemande le mot de passe
  après 15 minutes, sans interrompre les tâches en cours (le mode invité
  continue de tourner).
- **Section Configuration** — mot de passe maître, délais, ajout et retrait
  d'outils, depuis l'interface.
- **`deploie-web.sh`** — déploiement des pages qui annonce chaque fichier et
  vérifie le résultat par comparaison de contenu.

### Corrigé — sécurité

- **Injection stockée du mode invité vers la session enseignant.** Les noms
  saisis par les élèves étaient insérés dans la feuille de présence sans
  échappement, et cette page porte la session maître. Un invité non authentifié
  pouvait ainsi faire exécuter du code chez l'enseignant : bascule du mode
  invité, lecture du dossier partagé, changement du mot de passe maître. Corrigé
  à l'affichage, sur neuf points.
- **Verrouillage permanent possible par un tiers.** La première limitation de
  débit refusait les requêtes avant de vérifier le mot de passe ; derrière un
  tunnel, tout le trafic partage une même adresse source, si bien qu'un
  inconnu pouvait fermer la porte au propriétaire en échouant en boucle.
  Remplacée par un ralentissement exponentiel plafonné, qui ne bloque jamais un
  mot de passe correct.
- **Deux détournements par URL** — `?b=javascript:…` dans l'enveloppe des outils,
  et une adresse de partage `//site-tiers/x`, acceptée parce qu'elle ressemble à
  un chemin relatif. Le second piège avait déjà été corrigé ailleurs puis
  réintroduit.
- **Traversée de chemin** dans le dossier partagé — double barrière, vérifiée sur
  sept variantes dont les formes encodées.
- **Absence d'en-têtes de sécurité** sur toutes les réponses.

### Corrigé — accès

- **Outils injoignables hors Tailscale.** Les sept services à port dédié
  n'écoutaient que sur la boucle locale. Depuis que le tableau de bord est
  accessible en réseau local, chaque carte menait à un lien mort dès qu'on
  sortait du tailnet. Caddy écoute désormais sur un port distinct de celui
  publié — les deux ne peuvent pas être identiques, le démon Tailscale tenant
  déjà le port publié, et la collision empêcherait Caddy de démarrer.
- **Outils morts après expiration de session.** Sans session valide, la
  redirection menait à `/auth/login` sur le port de l'outil, où ce chemin partait
  vers l'outil lui-même : réponse vide, aucun moyen de se connecter.
- **Boucle de redirection infinie** après verrouillage par inactivité. La page de
  connexion considérait comme valide un jeton que la vérification rejetait :
  chacune renvoyait vers l'autre. Seul un effacement des cookies débloquait.
  Symptôme trompeur — la panne épargnait le navigateur qui venait de servir, ce
  qui la faisait passer pour un problème de réseau.
- **Correctifs invisibles après déploiement.** Les pages n'annonçaient aucune
  consigne de cache ; le navigateur appliquait son heuristique et gardait
  l'ancienne version. Un journal d'accès ajouté pour diagnostiquer faisait par
  ailleurs **échouer le rechargement de Caddy**, qui conservait alors son
  ancienne configuration sans que rien ne le signale.

### Corrigé — affichage

- **Outils affichés sur une fraction de l'écran.** L'iframe se dimensionnait par
  un pourcentage, qui exige un parent à hauteur définie ; la hauteur venant d'un
  étirement flex, le calcul échouait et l'iframe retombait à 150 px.
- **Terminal « refused to connect ».** Le site posait `X-Frame-Options: DENY`,
  qui interdit l'encadrement même depuis la même origine, alors qu'il héberge à
  la fois l'enveloppe et les outils qu'elle affiche.
- **Obsidian ne démarrait pas hors Tailscale.** Le bureau est diffusé en
  WebCodecs, API réservée aux contextes sécurisés. Servi en HTTPS par une
  autorité locale, avec émission de certificat à la demande — l'adresse de la
  machine change selon le réseau.
- **Polices du tableau blanc dépendantes d'Internet.** Rapatriées dans l'image ;
  deux appels sortants vers des tiers supprimés au passage.
- **Refonte de l'interface** — pensée pour le téléphone d'abord, cadence de
  rafraîchissement adaptée au débit et à la machine.

### Corrigé — outils

- **Notebooks qui ne rendaient aucun résultat**, puis « File ID error », puis
  chargement sans fin : trois extensions de collaboration à moitié installées
  détournaient le canal du noyau. Un état intermédiaire s'est révélé pire que
  l'une ou l'autre extrémité.
- **LaTeX Lab refusait la connexion** (jeton anti-rejeu) : la variable qui
  contrôle le cookie sécurisé est testée sur son *existence*, pas sa valeur.
- **Lean4Web invité affichait un cadre vide** — un appel d'API oublié dans le
  code du client.
- **Dossier de travail invité persistant** d'une séance à l'autre : la bascule ne
  redémarrait pas le conteneur.

### Retiré

- Journal d'accès de Caddy — le durcissement systemd de l'unité lui interdit
  d'écrire dans `/var/log/caddy`, et un rechargement raté n'applique rien.

---

## Ce qui n'a pas abouti

Sont listés ici les points ouverts ou abandonnés, pour éviter de les redécouvrir.

- **Collaboration en direct sur les notebooks** — jamais mise en service. Les
  extensions nécessaires cassent le canal du noyau dans la version en place ;
  l'essai demande une image de test à version figée.
- **Modèles pré-remplis par outil** (projets LaTeX Lab, Lean4Web) — prévus,
  jamais faits.
- **Open WebUI et tableau blanc invités hors du réseau local** — Tailscale ne
  publie que trois ports sur Internet, tous déjà pris.
- **Configuration d'Overleaf non reproductible** — les réglages du serveur LaTeX
  vivent hors du dépôt et ne sont pas rejoués par l'installation.
- **Mot de passe maître en clair sur le réseau local** — le tableau de bord est
  servi en HTTP. L'autorité locale existe désormais ; l'étendre au site
  principal reste à faire.
- **Aucun pare-feu sur la machine hôte.**
