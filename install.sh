#!/usr/bin/env bash
# Sampana — installation / mise a jour du dashboard.
# Idempotent : relancer ce script est sans danger, il converge vers l'etat cible.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

c_ok()   { printf '\033[32m  ok  \033[0m %s\n' "$*"; }
c_info() { printf '\033[36m ---- \033[0m %s\n' "$*"; }
c_warn() { printf '\033[33m warn \033[0m %s\n' "$*"; }
c_err()  { printf '\033[31m FAIL \033[0m %s\n' "$*" >&2; }
step()   { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ── 1. Prerequis ────────────────────────────────────────────────────────
step "Prerequis"

missing=()
for c in caddy ttyd tailscale python3 curl; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
done
if [ ${#missing[@]} -gt 0 ]; then
    c_err "Commandes absentes : ${missing[*]}"
    echo "  Arch / EndeavourOS :  sudo pacman -S --needed ${missing[*]}"
    exit 1
fi
c_ok "caddy, ttyd, tailscale, python3, curl presents"

if ! tailscale status >/dev/null 2>&1; then
    c_err "Tailscale n'est pas connecte. Lance : sudo tailscale up"
    exit 1
fi
c_ok "Tailscale connecte"

# ── 2. Configuration ────────────────────────────────────────────────────
step "Configuration"

for f in sampana.env services.json; do
    if [ ! -f "config/$f" ]; then
        src="config/${f%.json}.example.json"
        [ "$f" = "sampana.env" ] && src="config/sampana.env.example"
        cp "$src" "config/$f"
        c_warn "config/$f cree depuis l'exemple — a adapter avant usage reel"
    fi
done

# Deduit le hostname Tailscale si l'utilisateur ne l'a pas renseigne.
if grep -q '^SAMPANA_HOST=ma-machine' config/sampana.env; then
    detected="$(tailscale status --json | python3 -c \
        'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
    sed -i "s|^SAMPANA_HOST=.*|SAMPANA_HOST=$detected|" config/sampana.env
    c_ok "SAMPANA_HOST detecte : $detected"
fi

# shellcheck disable=SC1091
set -a; source config/sampana.env; set +a
c_ok "hote=$SAMPANA_HOST  caddy=$CADDY_PORT  sante=$HEALTH_PORT"

# ── 3. Generation ───────────────────────────────────────────────────────
step "Generation des fichiers"
python3 bin/render.py
c_ok "Caddyfile, manifeste web et commandes serve generes"

# Les en-tetes d'encadrement changent d'une version d'application a l'autre :
# on mesure plutot que de faire confiance a la declaration. Non bloquant.
if ! python3 bin/check-embed.py >/tmp/sampana-embed.$$ 2>&1; then
    c_warn "Des declarations \`embed\` ne correspondent plus a la realite :"
    grep -E 'A CORRIGER|"embed"' /tmp/sampana-embed.$$ | sed 's/^/       /'
    c_warn "Un service encadre a tort affiche un cadre vide dans le dashboard."
fi
rm -f /tmp/sampana-embed.$$

# ── 4. Fichiers web ─────────────────────────────────────────────────────
step "Publication du dashboard"

sudo install -d -o caddy -g caddy "$WEB_ROOT"
# `-f` : web/ contient desormais un sous-repertoire (guest/), qu'`install`
# refuserait de copier comme un fichier ordinaire.
for f in web/*; do
    [ -f "$f" ] || continue
    sudo install -o caddy -g caddy -m 644 "$f" "$WEB_ROOT/$(basename "$f")"
done
sudo install -o caddy -g caddy -m 644 build/services.web.json "$WEB_ROOT/services.json"

if [ -d web/guest ]; then
    sudo install -d -o caddy -g caddy "$WEB_ROOT/guest"
    for f in web/guest/*; do
        sudo install -o caddy -g caddy -m 644 "$f" "$WEB_ROOT/guest/$(basename "$f")"
    done
    [ -f build/guest.web.json ] && \
        sudo install -o caddy -g caddy -m 644 build/guest.web.json "$WEB_ROOT/guest/guest.web.json"
fi
c_ok "$WEB_ROOT mis a jour"

# ── 5. Caddy ────────────────────────────────────────────────────────────
step "Caddy"

sudo install -d /etc/caddy
[ -f /etc/caddy/Caddyfile ] && [ ! -f /etc/caddy/Caddyfile.pre-sampana ] && \
    sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-sampana && \
    c_info "Caddyfile precedent sauvegarde en /etc/caddy/Caddyfile.pre-sampana"

# Valider AVANT d'installer : sinon un fichier invalide resterait en place et
# Caddy refuserait de demarrer au prochain reboot.
if ! caddy validate --config build/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    c_err "Caddyfile genere invalide, rien n'a ete installe :"
    caddy validate --config build/Caddyfile --adapter caddyfile 2>&1 \
        | grep -v '"level":"info"' | tail -5
    exit 1
fi
sudo install -m 644 build/Caddyfile /etc/caddy/Caddyfile
sudo systemctl enable caddy >/dev/null 2>&1
sudo systemctl restart caddy
c_ok "Caddy valide, actif et active au demarrage"

# Autorite de certification locale.
#
# Certains outils exigent un «contexte securise» : sans HTTPS le navigateur leur
# refuse des API entieres et ils ne demarrent pas du tout. Via Tailscale le TLS
# est deja assure, mais en salle — reseau local, partage de connexion — c'est
# Caddy qui signe, avec une autorite qu'aucun magasin ne connait encore.
#
# On l'installe donc ici : dans le magasin systeme, puis dans ceux des
# navigateurs, qui ont chacun le leur et ignorent celui du systeme. Sans cette
# etape, l'outil s'ouvre sur un avertissement de securite a chaque visite.
CA_ROOT=/var/lib/caddy/pki/authorities/local/root.crt
if sudo test -f "$CA_ROOT"; then
    sudo install -m 644 "$CA_ROOT" \
        /etc/ca-certificates/trust-source/anchors/sampana-caddy-local.crt \
        2>/dev/null && sudo update-ca-trust 2>/dev/null || true

    if command -v certutil >/dev/null; then
        sudo cp "$CA_ROOT" "$HOME/sampana-autorite-locale.crt"
        sudo chown "$(id -un):$(id -gn)" "$HOME/sampana-autorite-locale.crt"
        for db in "$HOME/.pki/nssdb" "$HOME"/.mozilla/firefox/*.default*; do
            [ -d "$db" ] || continue
            certutil -d "sql:$db" -D -n "Sampana Caddy Local" 2>/dev/null || true
            certutil -d "sql:$db" -A -t "CT,C,C" -n "Sampana Caddy Local" \
                -i "$HOME/sampana-autorite-locale.crt" 2>/dev/null || true
        done
        c_ok "Autorite locale installee (systeme + navigateurs)"
        c_info "Pour tablette/telephone : ~/sampana-autorite-locale.crt"
    else
        c_warn "certutil absent (paquet nss) : les navigateurs avertiront"
    fi
fi

# ── 6. Service de sante ─────────────────────────────────────────────────
step "Service de sante"

install -d "$HOME/.local/share/sampana" "$HOME/.config/systemd/user"
install -m 755 services/health.py "$HOME/.local/share/sampana/health.py"
install -m 644 build/health.targets.json "$HOME/.local/share/sampana/targets.json"

cat > "$HOME/.config/systemd/user/sampana-health.service" <<EOF
[Unit]
Description=Sampana - sonde de sante des services
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/.local/share/sampana/health.py %h/.local/share/sampana/targets.json $HEALTH_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now sampana-health >/dev/null 2>&1
systemctl --user restart sampana-health
c_ok "sampana-health actif sur 127.0.0.1:$HEALTH_PORT"

# ── 6b. Mot de passe maitre ─────────────────────────────────────────────
step "Mot de passe maitre"

AUTH_CONF="$HOME/.config/sampana/auth.json"
install -d -m 700 "$(dirname "$AUTH_CONF")"

if [ "${AUTH_ENABLED:-1}" = "1" ]; then
    if [ ! -f "$AUTH_CONF" ]; then
        if [ -n "${SAMPANA_PASSWORD:-}" ]; then
            pw="$SAMPANA_PASSWORD"
            c_info "Mot de passe pris depuis la variable SAMPANA_PASSWORD"
        elif [ -t 0 ]; then
            read -r -s -p "  Choisis le mot de passe maitre : " pw; echo
            read -r -s -p "  Confirme                       : " pw2; echo
            [ "$pw" = "$pw2" ] || { c_err "Les mots de passe different."; exit 1; }
            [ ${#pw} -ge 8 ] || { c_err "8 caracteres minimum."; exit 1; }
        else
            pw="$(openssl rand -base64 12 | tr -d '/+=' | head -c 16)"
            c_warn "Mot de passe maitre genere : $pw"
        fi
        umask 077
        python3 services/auth.py --init "$pw" > "$AUTH_CONF"
        unset pw pw2
        c_ok "Empreinte scrypt et cle de session enregistrees dans $AUTH_CONF"
    else
        c_info "Mot de passe maitre existant conserve ($AUTH_CONF)"
        c_info "Pour le changer : rm $AUTH_CONF && ./install.sh"
    fi
    chmod 600 "$AUTH_CONF"

    install -m 755 services/auth.py "$HOME/.local/share/sampana/auth.py"
    cat > "$HOME/.config/systemd/user/sampana-auth.service" <<EOF
[Unit]
Description=Sampana - authentification unique
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/.local/share/sampana/auth.py %h/.config/sampana/auth.json $AUTH_PORT $SAMPANA_HOST
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now sampana-auth >/dev/null 2>&1
    systemctl --user restart sampana-auth
    c_ok "sampana-auth actif sur 127.0.0.1:$AUTH_PORT"
else
    systemctl --user disable --now sampana-auth 2>/dev/null || true
    c_warn "Authentification DESACTIVEE (AUTH_ENABLED=0) — tout le tailnet a acces"
fi

# ── 7. Terminal web ─────────────────────────────────────────────────────
step "Terminal web (ttyd)"

# ttyd n'a plus de mot de passe propre : c'est Caddy qui protege l'acces avec
# le mot de passe maitre. Un second prompt serait redondant.
rm -f "$HOME/.config/ttyd-credential"

cat > "$HOME/.config/systemd/user/ttyd.service" <<EOF
[Unit]
Description=ttyd - terminal web (servi sous /terminal par Caddy)
After=network.target

[Service]
Type=simple
# -W : autorise la saisie (lecture seule par defaut depuis ttyd 1.7)
# -b : ttyd genere ses URL sous /terminal, pour le reverse proxy
# Pas de -c : l'authentification est assuree en amont par Caddy avec le mot de
# passe maitre. ttyd n'ecoute que sur la boucle locale.
ExecStart=/usr/bin/ttyd -i 127.0.0.1 -p $TTYD_PORT -b /terminal -W /bin/bash
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now ttyd >/dev/null 2>&1
systemctl --user restart ttyd
c_ok "ttyd actif sur 127.0.0.1:$TTYD_PORT"

# ── 7a. Application depuis le tableau de bord ───────────────────────────
step "Ajout d'outils depuis le tableau de bord"

# Le helper vit HORS du depot, appartient a root et n'est pas modifiable par
# l'utilisateur : sinon la regle sudo ne vaudrait rien, il suffirait d'en
# reecrire le contenu pour executer n'importe quoi en root.
sudo install -d -o root -g root -m 755 /usr/local/lib/sampana
sed "s|@REPO@|$ROOT|g; s|@WEB_ROOT@|$WEB_ROOT|g; s|@USER@|$USER|g" bin/apply.sh \
    | sudo tee /usr/local/lib/sampana/apply.sh >/dev/null
sudo chown root:root /usr/local/lib/sampana/apply.sh
sudo chmod 755 /usr/local/lib/sampana/apply.sh

# Regle etroite : ce seul chemin, sans argument possible. Le tableau de bord
# n'y gagne rien de plus qu'un shell ttyd, que le mot de passe maitre ouvre
# deja — mais la regle reste volontairement minimale.
printf '%s ALL=(root) NOPASSWD: /usr/local/lib/sampana/apply.sh\n' "$USER" \
    | sudo tee /etc/sudoers.d/sampana >/dev/null
sudo chmod 440 /etc/sudoers.d/sampana
if ! sudo visudo -c -f /etc/sudoers.d/sampana >/dev/null 2>&1; then
    sudo rm -f /etc/sudoers.d/sampana
    c_err "Regle sudo invalide, retiree. L'ajout d'outils restera manuel."
else
    c_ok "Ajout d'outils actif (sudo restreint a /usr/local/lib/sampana/apply.sh)"
fi

# ── 7b. Mode invite ─────────────────────────────────────────────────────
if [ "${GUEST_ENABLED:-0}" = "1" ]; then
    step "Mode invite"

    GUEST_CONF="$HOME/.config/sampana/guest.json"
    GUEST_STATE="$HOME/.config/sampana/guest-state.json"
    SHARE="${GUEST_SHARE_DIR:-/srv/sampana-partage}"

    # Repertoire d'echange de la socket. Le conteneur invite n'a aucun reseau :
    # il ne peut pas ecouter sur un port, il expose une socket Unix que Caddy
    # vient chercher ici. Le setgid fait heriter le groupe caddy, ce qui permet
    # un mode 0660 plutot qu'un 0666 ouvert a tous.
    sudo tee /etc/tmpfiles.d/sampana-guest.conf >/dev/null <<EOF
d /run/sampana-guest 2750 $USER caddy -
EOF
    sudo systemd-tmpfiles --create /etc/tmpfiles.d/sampana-guest.conf
    c_ok "/run/sampana-guest pret"

    # Dossier partage. Hors de /home : Caddy tourne sous son propre utilisateur
    # et ne peut pas traverser un home en mode 0710.
    sudo install -d -o "$USER" -g "$USER" -m 755 "$SHARE" "$SHARE/templates"
    [ -e "$HOME/sampana-partage" ] || ln -s "$SHARE" "$HOME/sampana-partage"
    c_ok "Dossier partage : $SHARE (lien depuis ~/sampana-partage)"

    install -m 755 services/guest.py "$HOME/.local/share/sampana/guest.py"
    install -m 755 services/guest-ollama-bridge.py \
        "$HOME/.local/share/sampana/guest-ollama-bridge.py"

    if [ ! -f "$GUEST_CONF" ]; then
        umask 077
        # Un code provisoire : chaque ouverture de seance le remplace par un
        # code neuf, genere et affiche par le tableau de bord.
        python3 services/guest.py --init "sampana-invite" "${GUEST_TTL:-7200}" > "$GUEST_CONF"
        c_ok "Configuration invite creee (le code est renouvele a chaque seance)"
    else
        c_info "Configuration invite existante conservee"
    fi
    chmod 600 "$GUEST_CONF"

    cat > "$HOME/.config/systemd/user/sampana-guest.service" <<EOF
[Unit]
Description=Sampana — portail invite
After=network.target

[Service]
Type=simple
Environment=SAMPANA_SHARE_DIR=$SHARE
Environment=SAMPANA_SERVICES=$ROOT/config/services.json
# Paires «port public:port local» : Tailscale ne publie que 443, 8443 et 10000,
# alors que le site invite ecoute sur $GUEST_PORT.
ExecStart=/usr/bin/python3 %h/.local/share/sampana/guest.py $GUEST_CONF \\
  $GUEST_GATE_PORT $SAMPANA_HOST $GUEST_STATE \\
  ${GUEST_FUNNEL_PORT}:${GUEST_PORT},8443:8444 $GUEST_PORT \\
  ${GUEST_LATEX_EMAIL:-} ${GUEST_LATEX_PASSWORD:-}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

    # Passerelle Ollama : le conteneur invite n'ayant aucun reseau, c'est le
    # seul chemin par lequel jupyter-ai peut atteindre un modele.
    if command -v socat >/dev/null 2>&1; then
        cat > "$HOME/.config/systemd/user/sampana-guest-ollama.service" <<'EOF'
[Unit]
Description=Sampana — passerelle Ollama pour le conteneur invite
After=ollama.service
Wants=ollama.service

[Service]
Type=simple
ExecStartPre=/usr/bin/rm -f /run/sampana-guest/ollama.sock
ExecStart=/usr/bin/socat UNIX-LISTEN:/run/sampana-guest/ollama.sock,fork,mode=0660 TCP:127.0.0.1:11434
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
    else
        c_warn "socat absent : pas d'assistant IA dans le JupyterLab invite"
    fi

    # Carte graphique. Sans nvidia-container-toolkit, le Quadlet reclame un
    # peripherique CDI qui n'existe pas et le conteneur refuse de demarrer :
    # on retire alors la ligne plutot que de casser tout le mode invite.
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
        if ! command -v nvidia-ctk >/dev/null 2>&1; then
            c_warn "GPU detecte mais nvidia-container-toolkit absent."
            c_warn "  Arch : sudo pacman -S nvidia-container-toolkit"
        elif [ ! -f /etc/cdi/nvidia.yaml ]; then
            sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml >/dev/null 2>&1 \
                && c_ok "Peripheriques CDI generes" \
                || c_warn "Generation CDI echouee — le GPU restera hors du conteneur"
        else
            c_ok "GPU disponible pour le JupyterLab invite"
        fi
    fi

    # Conteneurs (Quadlet). L'image invitee embarque jupyter-ai : elle doit etre
    # construite ici, le conteneur ne pouvant rien installer sans reseau.
    if command -v podman >/dev/null 2>&1; then
        install -d "$HOME/.config/containers/systemd"
        for q in services/quadlet/*.container; do
            [ -f "$q" ] || continue
            sed "s|@SHARE@|$SHARE|g; s|@HOME@|$HOME|g" "$q" \
                > "$HOME/.config/containers/systemd/$(basename "$q")"
            # Pas de GPU utilisable : on retire la declaration, sans quoi le
            # conteneur echouerait au demarrage sur un peripherique absent.
            if [ ! -f /etc/cdi/nvidia.yaml ]; then
                sed -i '/^AddDevice=nvidia/d' \
                    "$HOME/.config/containers/systemd/$(basename "$q")"
            fi
        done
        if ! podman image exists localhost/sampana/guest-jupyter:latest; then
            c_info "Construction de l'image JupyterLab invitee (plusieurs minutes)…"
            podman build -f services/guest-jupyter.Containerfile \
                -t sampana/guest-jupyter:latest . >/dev/null
        fi
        # Excalidraw est modifie a la construction : adresse de collaboration
        # rendue relative, et polices rapatriees dans l'image. Sans cette
        # etape, une installation neuve tirerait l'image officielle, dont les
        # polices viennent d'un CDN — le tableau blanc s'afficherait alors avec
        # des caracteres de repli des que la salle est sans Internet.
        if ! podman image exists localhost/sampana/excalidraw:latest; then
            c_info "Construction de l'image Excalidraw (polices hors ligne)…"
            podman build -f services/excalidraw.Containerfile \
                -t sampana/excalidraw:latest . >/dev/null
        fi
        c_ok "Conteneurs invites declares"
    else
        c_warn "podman absent : le mode invite ne pourra pas isoler JupyterLab"
    fi

    # Purge du compte LaTeX Lab partage.
    if [ -n "${GUEST_LATEX_EMAIL:-}" ]; then
        install -m 755 services/purge-guest-latex.sh \
            "$HOME/.local/share/sampana/purge-guest-latex.sh"
        install -m 755 services/set-latex-password.sh \
            "$HOME/.local/share/sampana/set-latex-password.sh"
        # Les scripts .mjs sont recopies dans le conteneur Overleaf a chaque
        # execution : une mise a jour d'Overleaf le recree et emporterait tout
        # ce qui y a ete depose.
        install -m 644 services/purge-guest-latex.mjs services/set-latex-password.mjs \
            "$HOME/.local/share/sampana/"
        cat > "$HOME/.config/systemd/user/sampana-purge-guest.service" <<EOF
[Unit]
Description=Sampana — purge des projets LaTeX Lab invite
After=docker.service

[Service]
Type=oneshot
ExecStart=%h/.local/share/sampana/purge-guest-latex.sh $GUEST_LATEX_EMAIL
EOF
        cat > "$HOME/.config/systemd/user/sampana-purge-guest.timer" <<'EOF'
[Unit]
Description=Sampana — purge quotidienne du compte LaTeX Lab invite

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
    fi

    systemctl --user daemon-reload
    systemctl --user enable --now sampana-guest >/dev/null 2>&1
    systemctl --user restart sampana-guest
    [ -f "$HOME/.config/systemd/user/sampana-guest-ollama.service" ] && {
        systemctl --user enable --now sampana-guest-ollama >/dev/null 2>&1
        systemctl --user restart sampana-guest-ollama
    }
    [ -f "$HOME/.config/systemd/user/sampana-purge-guest.timer" ] && \
        systemctl --user enable --now sampana-purge-guest.timer >/dev/null 2>&1
    systemctl --user start sampana-guest-jupyter sampana-guest-webui 2>/dev/null || true

    c_ok "Mode invite installe — il demarre FERME, ouvre-le depuis le dashboard"
fi

# ── 8. Demarrage sans session graphique ─────────────────────────────────
step "Persistance au demarrage"

if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    sudo loginctl enable-linger "$USER"
    c_ok "Linger active : les unites utilisateur demarreront au boot sans login"
else
    c_ok "Linger deja actif"
fi

# ── 9. Exposition Tailscale ─────────────────────────────────────────────
step "Exposition Tailscale"

# `tailscale cert` exige des fichiers reguliers : /dev/null est refuse.
_certdir="$(mktemp -d)"
trap 'rm -rf "$_certdir"' EXIT
if ! tailscale cert --cert-file "$_certdir/c" --key-file "$_certdir/k" \
        "$SAMPANA_HOST" >/dev/null 2>&1; then
    c_err "Impossible d'obtenir un certificat TLS pour $SAMPANA_HOST."
    echo "  Active les certificats HTTPS : https://login.tailscale.com/admin/dns"
    echo "  (section « HTTPS Certificates » -> Enable), puis relance ce script."
    exit 1
fi
c_ok "Certificats HTTPS disponibles"

bash build/serve.sh
c_ok "Mappings tailscale serve appliques"

# ── 10. Verification ────────────────────────────────────────────────────
step "Verification"

fail=0
check() {
    local label="$1" url="$2" want="${3:-200}"
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' -L -m 25 "$url" || echo 000)"
    if [[ " $want " == *" $code "* ]]; then
        c_ok "$(printf '%-14s' "$label") $code"
    else
        c_err "$(printf '%-14s' "$label") $code (attendu : $want)"
        fail=1
    fi
}

if [ "${AUTH_ENABLED:-1}" = "1" ]; then
    # Sans session, tout doit rediriger vers la page de connexion : c'est la
    # preuve que le mot de passe maitre s'applique reellement.
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 25 "https://$SAMPANA_HOST/" || echo 000)"
    if [ "$code" = "302" ]; then
        c_ok "$(printf '%-14s' 'protection') 302 vers la page de connexion"
    else
        c_err "$(printf '%-14s' 'protection') $code (302 attendu — l'auth ne s'applique pas)"
        fail=1
    fi
    # La page de connexion, elle, doit rester accessible sans session.
    check "connexion" "https://$SAMPANA_HOST/auth/login"
    # Liste derivee de la configuration, jamais ecrite en dur : un port
    # deplace laissait sinon la verification interroger l'ancien et echouer
    # sans que rien ne soit casse. Seuls les services du tableau de bord sont
    # concernes — ceux du mode invite ne sont, eux, PAS proteges par le mot de
    # passe maitre, c'est leur raison d'etre.
    for p in $(python3 -c '
import json
cfg = json.load(open("build/services.web.json"))
print(" ".join(str(s["port"]) for g in cfg["groups"] for s in g["services"]
                if s.get("route") == "port"))'); do
        c="$(curl -s -o /dev/null -w '%{http_code}' -m 25 "https://$SAMPANA_HOST:$p/" || echo 000)"
        [ "$c" = "302" ] || { c_err "port $p non protege (HTTP $c)"; fail=1; }
    done
    [ "$fail" -eq 0 ] && c_ok "$(printf '%-14s' 'ports dedies') tous proteges"
else
    check "dashboard" "https://$SAMPANA_HOST/"
    check "shell"     "https://$SAMPANA_HOST/app.html?s=terminal"
fi

# Le detail de sante est lu directement sur la boucle locale : via l'URL
# publique il serait, a juste titre, derriere l'authentification.
python3 - "$HEALTH_PORT" <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/", timeout=25) as r:
    d = json.load(r)
print(f"\n  {d['up']}/{d['total']} services en ligne")
for s in d["services"]:
    mark = "ok  " if s["status"] == "up" else "----" if s["status"] == "degraded" else "DOWN"
    detail = f"{s.get('ms', '?')} ms" if s["status"] == "up" else s.get("error") or f"HTTP {s.get('code')}"
    print(f"    [{mark}] {s['label']:<16} {detail}")
PY

echo
if [ "$fail" -eq 0 ]; then
    printf '\033[32m✓ Sampana est en ligne : https://%s\033[0m\n' "$SAMPANA_HOST"
else
    c_err "Certaines verifications ont echoue (voir ci-dessus)."
    exit 1
fi
