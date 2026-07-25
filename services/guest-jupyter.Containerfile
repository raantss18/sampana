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

# Ollama est joignable sur la boucle locale INTERNE au conteneur, republiee
# depuis une socket Unix par guest-ollama-bridge.py (voir le Quadlet).
ENV OLLAMA_BASE_URL=http://127.0.0.1:11434
