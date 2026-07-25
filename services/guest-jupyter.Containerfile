# Image du JupyterLab invite — Sampana.
#
# Le conteneur tourne en `--network none` : il ne peut RIEN installer au
# demarrage. Tout ce dont l'invite aura besoin doit donc etre present ici,
# y compris l'assistant IA.
#
# Construire :
#   podman build -f services/guest-jupyter.Containerfile -t sampana/guest-jupyter:latest .
FROM quay.io/jupyter/scipy-notebook:latest

USER root
# fix-permissions vient de l'image de base et remet les droits sur les
# repertoires de conda apres installation.
USER ${NB_UID}

# jupyter-ai fournit le panneau de chat et la commande %%ai dans les notebooks.
# langchain-ollama est le connecteur : sans lui, jupyter-ai ne propose aucun
# fournisseur local et le panneau reste vide.
#
# SANS l'extra [all] : celui-ci tire toute la pile de collaboration temps reel,
# qui casse deux choses et ne sert a rien sur une instance ephemere partagee.
RUN pip install --no-cache-dir \
      jupyter-ai \
      langchain-ollama \
 && fix-permissions "${CONDA_DIR}" \
 && fix-permissions "/home/${NB_USER}"

# Retrait de la pile de collaboration temps reel, que jupyter-ai peut encore
# tirer par dependance transitive. Deux degats distincts, une seule origine :
#
#   - `jupyter_server_documents` DETOURNE le websocket des noyaux et n'accepte
#     que le protocole binaire v1. Tout message JSON le fait planter
#     (« cannot convert str object to bytes ») : le noyau demarre, l'API
#     repond, le websocket s'etablit — et aucun resultat ne revient jamais.
#
#   - `jupyter-collaboration` est INCOMPATIBLE avec le JupyterLab de cette
#     image et reclame malgre tout des identifiants de fichier : « File ID
#     error » a chaque ouverture de notebook.
#
# On DESINSTALLE, on ne se contente pas de desactiver : une premiere tentative
# avait coupe le service cote serveur en laissant le frontend en place, qui
# continuait d'appeler un service devenu absent. L'etat mi-installe etait pire
# que les deux extremes.
RUN pip uninstall -y \
      jupyter-collaboration jupyter-collaboration-ui \
      jupyter_server_documents jupyter_server_fileid jupyter-server-ydoc \
      >/dev/null 2>&1 || true

# Garde-fou : si un paquet a survecu, on desactive au moins son extension.
RUN jupyter server extension disable jupyter_server_documents --sys-prefix \
      >/dev/null 2>&1 || true

# SageMath : le Jupyter de l'enseignant en dispose, les etudiants l'attendent
# donc aussi. C'est un gros paquet (plusieurs Go) et la resolution conda est
# lente — d'ou une couche separee, qui evite de tout reconstruire quand seul
# jupyter-ai change.
# Environnement SEPARE : Sage epingle ses propres versions de Python et de
# nombreuses bibliotheques scientifiques. L'installer dans l'environnement du
# notebook rendrait la resolution insoluble, ou casserait scipy et pandas.
# Le noyau est ensuite declare aupres de JupyterLab.
# `|| echo` volontaire : Sage est lourd et sa resolution conda echoue selon
# les plateformes. Une dependance optionnelle ne doit pas rendre TOUTE l'image
# inconstructible — sans ce garde-fou, un echec ici priverait les etudiants de
# JupyterLab entier pour un noyau d'appoint. Le noyau est alors simplement
# absent, ce que `jupyter kernelspec list` montre au premier coup d'oeil.
RUN (mamba create -y -n sage -c conda-forge sage \
     && mamba install -y -n sage -c conda-forge ipykernel \
     && mamba run -n sage python -m ipykernel install --prefix="${CONDA_DIR}" \
          --name sage --display-name "SageMath") \
    || echo "SageMath indisponible sur cette plateforme — noyau non installe." \
 ; mamba clean --all -f -y \
 && fix-permissions "${CONDA_DIR}" \
 && fix-permissions "/home/${NB_USER}"

# Ollama est joignable sur la boucle locale INTERNE au conteneur, republiee
# depuis une socket Unix par guest-ollama-bridge.py (voir le Quadlet).
ENV OLLAMA_BASE_URL=http://127.0.0.1:11434

# ── Environnement « ai » ────────────────────────────────────────────
# Reproduit l'environnement de l'enseignant pour que les etudiants disposent
# du meme noyau : torch, scikit-learn, xgboost, lightgbm et la famille
# langchain.
#
# torch est installe APRES les autres paquets : place avant, il etait ecrase
# par une dependance qui en retirait une autre version de PyPI. L'ordre est
# donc ce qui garantit la version voulue.
#
# Build CUDA, et non CPU : le GPU de la machine est passe au conteneur (voir
# AddDevice dans le Quadlet). La version est alignee sur celle de
# l'enseignant, pour que les supports de cours se comportent a l'identique.
#
# Environnement SEPARE, comme Sage : ces paquets epinglent des versions de
# numpy et pandas differentes de celles du notebook.
COPY services/guest-ai-requirements.txt /tmp/ai-requirements.txt
RUN (mamba create -y -n ai -c conda-forge python=3.12 ipykernel \
     && mamba run -n ai pip install --no-cache-dir -r /tmp/ai-requirements.txt \
     && mamba run -n ai pip install --no-cache-dir --force-reinstall \
          --index-url https://download.pytorch.org/whl/cu124 \
          torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 \
     && mamba run -n ai python -m ipykernel install --prefix="${CONDA_DIR}" \
          --name ai --display-name "Python (ai)") \
    || echo "Environnement ai indisponible — noyau non installe." \
 ; rm -f /tmp/ai-requirements.txt \
 ; mamba clean --all -f -y \
 && fix-permissions "${CONDA_DIR}" \
 && fix-permissions "/home/${NB_USER}"
