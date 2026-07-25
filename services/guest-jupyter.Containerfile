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
RUN pip install --no-cache-dir \
      "jupyter-ai[all]" \
      langchain-ollama \
 && fix-permissions "${CONDA_DIR}" \
 && fix-permissions "/home/${NB_USER}"

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
