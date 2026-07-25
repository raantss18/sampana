import sys, re
garde, jete = [], []
for line in sys.stdin:
    line = line.strip()
    if not line or line.startswith('-e ') or ' @ ' in line:
        jete.append(line); continue
    nom = re.split(r"[=<>!\[]", line, maxsplit=1)[0].lower()
    if nom.startswith('nvidia-') or nom in {
            'torch', 'torchvision', 'torchaudio', 'triton', 'pytorch-triton'}:
        jete.append(line); continue
    garde.append(line)
print("""# Environnement « ai » du JupyterLab invite.
#
# Genere depuis l'environnement de l'enseignant (pip freeze), puis filtre :
#
#  - torch et les paquets nvidia-* sont retires ICI et reinstalles depuis
#    l'index CPU. Le conteneur invite n'a aucun GPU : la build CUDA y pesait
#    ~6 Go de pilotes inutilisables, pour une API identique.
#
#  - les connecteurs langchain vers des services distants (OpenAI, Anthropic,
#    Google, AWS, Cohere, Mistral, NVIDIA) sont CONSERVES pour que les imports
#    des supports de cours passent — mais ils ne pourront rien joindre : le
#    conteneur invite n'a pas de reseau. Seul Ollama repond, via une socket
#    Unix. C'est voulu : c'est ce qui empeche un notebook d'etudiant de miner
#    ou d'attaquer depuis l'IP de la machine.
#
# Regenerer :  ~/.conda/envs/ai/bin/pip freeze | python3 bin/filtre-ai.py""")
print("\n".join(garde))
print(f"{len(garde)} retenus / {len(jete)} ecartes", file=sys.stderr)
