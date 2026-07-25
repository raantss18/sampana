// Sampana — fixe le mot de passe du compte LaTeX Lab partage.
//
// create-user.mjs d'Overleaf n'emet qu'une URL d'activation, inutilisable sans
// navigateur. On passe donc par l'API interne d'authentification.
//
// A executer DANS le conteneur, depuis /overleaf/services/web :
//   ./bin/run-script scripts/sampana-set-latex-password.mjs <email> <mot-de-passe>
import { db } from '../app/src/infrastructure/mongodb.mjs'
import AuthenticationManager from '../app/src/Features/Authentication/AuthenticationManager.mjs'

const [email, password] = process.argv.slice(2)
if (!email || !password) {
  console.error('Usage : sampana-set-latex-password.mjs <email> <mot-de-passe>')
  process.exit(1)
}

const user = await db.users.findOne({ email })
if (!user) {
  console.error('Compte introuvable :', email)
  process.exit(1)
}

// Overleaf refuse tout mot de passe contenant un fragment de l'adresse du
// compte : l'erreur remontee est `contains_email`.
await new Promise((resolve, reject) =>
  AuthenticationManager.setUserPassword(user, password, e => (e ? reject(e) : resolve()))
)
console.log('Mot de passe LaTeX Lab renouvele pour', email)
process.exit(0)
