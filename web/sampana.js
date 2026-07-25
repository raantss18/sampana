
/* Un service a port dedie est joignable par deux chemins qui ne se valent pas.
 *
 * Via le tailnet, `tailscale serve` ecoute sur le port publie et termine le
 * TLS : on vise `svc.port`, en https. Hors du tailnet — reseau de la salle,
 * partage de connexion — tailscaled n'intervient pas et ce port ne repond a
 * personne ; il faut viser l'ecoute de Caddy, `svc.localPort`, en clair.
 *
 * Se tromper ne degrade pas l'affichage : le lien ne mene nulle part.
 */
function urlService(svc, hote) {
  if (svc.route === 'path') return svc.path + '/';
  const tailnet = hote.endsWith('.ts.net');
  const port = tailnet ? svc.port : svc.localPort;
  const proto = tailnet ? 'https' : 'http';
  return `${proto}://${hote}:${port}${svc.openPath}`;
}
