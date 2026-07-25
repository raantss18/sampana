#!/usr/bin/env python3
"""Sampana — relais TCP -> socket Unix, execute DANS le conteneur invite.

Le conteneur invite tourne en `--network none` : c'est ce qui empeche le code
d'un etudiant (ou d'un scanner Internet) de miner ou de relayer une attaque
depuis l'IP de la machine. Mais cela lui interdit aussi de joindre Ollama.

Le compromis retenu ouvre exactement un trou, et un seul :

    conteneur                     hote
    127.0.0.1:11434  --->  /sock/ollama.sock  --->  127.0.0.1:11434 (Ollama)
    (ce relais)            (montee en volume)       (relais cote hote)

L'invite atteint donc le modele, et rien d'autre : ni Internet, ni les autres
services de la machine. Le conteneur reste sans interface reseau ; seule la
boucle locale interne, qui ne mene nulle part, est utilisee.

Stdlib pure : l'image ne contient pas socat et on ne veut rien y installer.
"""
from __future__ import annotations

import os
import socket
import socketserver
import threading

SOCK = os.environ.get("OLLAMA_UNIX_SOCK", "/sock/ollama.sock")
PORT = int(os.environ.get("OLLAMA_BRIDGE_PORT", "11434"))


def pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        # Fermer en ecriture seulement : l'autre sens peut avoir encore des
        # donnees en vol, notamment sur les reponses en flux d'Ollama.
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            up.connect(SOCK)
        except OSError:
            self.request.close()
            return
        with up:
            a = threading.Thread(target=pump, args=(self.request, up), daemon=True)
            b = threading.Thread(target=pump, args=(up, self.request), daemon=True)
            a.start()
            b.start()
            a.join()
            b.join()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    Server(("127.0.0.1", PORT), Handler).serve_forever()
