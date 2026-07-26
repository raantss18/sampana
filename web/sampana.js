
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
  // Hors tailnet on parle en clair, sauf aux services qui exigent un contexte
  // securise : le navigateur leur refuserait sinon les API dont ils vivent.
  const proto = tailnet || svc.localSecure ? 'https' : 'http';
  return `${proto}://${hote}:${port}${svc.openPath}`;
}

/* Echappe une valeur destinee a du HTML construit par concatenation.
 *
 * Les noms d'etudiants et de fichiers arrivent de l'exterieur : un eleve saisit
 * son nom lui-meme, et n'importe qui peut nommer un fichier. Inseres tels quels
 * dans `innerHTML`, ils s'executent. Le cas grave n'est pas le portail invite
 * mais la FICHE DE PRESENCE : elle s'affiche dans la page enseignant, qui porte
 * la session maitre — un nom bien choisi y prendrait la main sur Sampana.
 *
 * A appliquer a toute valeur non ecrite par nous, sans exception : c'est la
 * regle la plus simple a tenir, et la seule qu'on n'oublie pas.
 */
function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
