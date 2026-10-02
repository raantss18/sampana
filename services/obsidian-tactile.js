/* Sampana — reglage par defaut du bureau distant sur un ecran tactile.
 *
 * Selkies affiche le bureau a sa taille reelle : sur une tablette, on ne voit
 * qu'un coin de l'ecran. Le reglage qui l'ajuste a la fenetre, `useCssScaling`,
 * existe mais vaut faux par defaut et ne s'active qu'a la main, appareil par
 * appareil. Aucun parametre d'URL ni variable d'environnement ne permet de le
 * changer : la valeur vit dans le stockage local du navigateur.
 *
 * On la pose donc ici, AVANT que l'application ne la lise.
 *
 * Deux precautions :
 *   - uniquement si le pointeur est grossier — un doigt. Sur un ecran
 *     d'ordinateur, l'affichage a taille reelle reste preferable, et rien ne
 *     change.
 *   - uniquement si la cle est absente. Un choix deja fait par l'utilisateur
 *     n'est jamais ecrase, y compris s'il a desactive la mise a l'echelle.
 */
(function () {
  try {
    if (!window.matchMedia || !window.matchMedia('(pointer: coarse)').matches) {
      return;
    }

    /* Le prefixe DOIT etre derive exactement comme dans selkies-core.js, sinon
       la cle ecrite n'est pas celle qui sera lue. La classe de caracteres est
       recopiee telle quelle, quirk compris : `.-_` y est une plage, pas trois
       caracteres isoles. */
    var prefixe = window.location.href.split('#')[0].replace(/[^a-zA-Z0-9.-_]/g, '_');
    var cle = prefixe + '_useCssScaling';

    if (window.localStorage.getItem(cle) === null) {
      window.localStorage.setItem(cle, 'true');
    }
  } catch (e) {
    /* Stockage indisponible (navigation privee stricte) : le bureau s'affiche
       simplement sans mise a l'echelle, comme avant. Rien ne justifie de faire
       echouer le chargement pour un reglage de confort. */
  }
})();
