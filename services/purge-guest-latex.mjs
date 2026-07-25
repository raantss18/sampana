// Sampana — purge des projets du compte LaTeX Lab invite.
//
// Le mode invite est annonce comme non persistant, mais LaTeX Lab (Overleaf CE)
// n'a pas de notion de session ephemere : tout projet appartient a un compte et
// y reste. Ce script retablit la promesse en vidant periodiquement le compte
// partage.
//
// deleteProject fait une suppression douce (le projet part dans une corbeille) ;
// expireDeletedProject l'efface reellement. Les deux sont necessaires, sinon les
// documents des etudiants restent recuperables indefiniment.
//
// A executer DANS le conteneur, depuis /overleaf/services/web :
//   ./bin/run-script scripts/sampana-purge-guest.mjs invite@sampana.local
//
// Sans argument, le script ne fait rien : il ne doit jamais pouvoir viser un
// compte au hasard, et surtout pas le tien.
import { db, ObjectId } from '../app/src/infrastructure/mongodb.mjs'
import ProjectDeleter from '../app/src/Features/Project/ProjectDeleter.mjs'

const email = process.argv[2]
if (!email) {
  console.error('Usage : sampana-purge-guest.mjs <email-du-compte-invite>')
  process.exit(1)
}

const user = await db.users.findOne({ email })
if (!user) {
  console.error('Compte introuvable :', email)
  process.exit(1)
}

const projects = await db.projects
  .find({ owner_ref: new ObjectId(user._id) }, { projection: { _id: 1, name: 1 } })
  .toArray()

console.log(`${projects.length} projet(s) a purger pour ${email}`)

let purges = 0
for (const p of projects) {
  try {
    await ProjectDeleter.promises.deleteProject(p._id, {
      deleterUser: user,
      ipAddress: '127.0.0.1',
    })
    await ProjectDeleter.promises.expireDeletedProject(p._id)
    purges += 1
  } catch (e) {
    console.error(`  echec sur ${p.name} (${p._id}) :`, e.message)
  }
}

console.log(`${purges} projet(s) definitivement supprime(s).`)
process.exit(0)
