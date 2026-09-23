# Serveur MCP — Plan de Validation Vertical

Expose le pipeline « Plan de validation Vertical » (`core/`, partagé avec
l'app Streamlit à la racine du dépôt) comme un serveur [MCP](https://modelcontextprotocol.io/)
(Model Context Protocol), pour qu'un agent Dust (ex. « Assistant Bureau
d'études ») puisse l'utiliser comme outil. Service Render **distinct** de
l'app Streamlit (process, port, jeton d'accès différents) — même dépôt,
même code `core/`, jamais le même déploiement.

## Règles absolues (rappel — non négociables, héritées du pipeline)

1. Les dessins ne sont **jamais redessinés** : extraits en image, annotés
   par-dessus.
2. **Aucune valeur numérique** n'est retapée de mémoire — toujours extraite
   programmatiquement.
3. Le PDF final n'est généré **qu'après validation explicite** de
   l'utilisateur du rendu visuel (`verifier_rendu`, PUIS `exporter_pdf` avec
   `valide=True`).

## Les 6 outils

| Outil | Étape | Rôle |
|---|---|---|
| `inventaire_pdf` | 1 | Nombre de pages, taille, aperçu basse résolution — pour classer les pages ; met le PDF en cache et retourne `pdf_id` |
| `extraire_page` | 2 | Rendu + mots détectés d'UNE page (`role="planche"\|"garde"\|"specs"`), référencée par `pdf_id` |
| `traduire_mots` | 3 | Glossaire Vertical appliqué aux mots d'une page déjà extraite |
| `assembler_pptx` | 4 | Montage du PPTX (copie d'un plan existant, jamais un template vide) |
| `verifier_rendu` | 5 | Rendu par slide (LibreOffice) + rapport de vérification — portail obligatoire |
| `exporter_pdf` | 6 | Export PDF final, refuse sans `valide=True` |

Chaque outil a une description détaillée dans son propre docstring
(`mcp_server/tools/*.py`) — c'est ce que l'agent Dust lit pour savoir quand
et comment l'utiliser. Ordre d'appel attendu : `inventaire_pdf` →
`extraire_page` (par page retenue) → `traduire_mots` → `assembler_pptx` →
`verifier_rendu` (montrer les images à l'utilisateur, obtenir son accord) →
`exporter_pdf`.

## Le PDF fabricant en entrée : téléversé une seule fois hors canal MCP, référencé par `pdf_id`

Deux problèmes trouvés l'un après l'autre en conditions réelles avec un
agent Dust :

1. Renvoyer le PDF complet en base64 à **chaque** appel (`inventaire_pdf`,
   puis `extraire_page` une fois par page) faisait hésiter l'agent sur le
   volume dès quelques pages — il se mettait à improviser des
   contournements dangereux (rastérisation basse résolution, OCR local,
   découpage manuel du PDF) plutôt que d'appeler l'outil normalement.
2. Plus grave, confirmé ensuite directement par l'agent Dust lui-même :
   même l'envoi **unique** du PDF en base64 s'est avéré **impossible** —
   rien dans son environnement ne lui permet de produire un blob base64 à
   partir d'un fichier joint en conversation et de l'injecter dans un
   argument d'outil MCP. L'agent a fini par improviser un PPTX entièrement
   hors pipeline plutôt que d'utiliser nos outils.

D'où un point d'entrée HTTP **direct**, hors du canal MCP (donc pas soumis
à sa limite ~1 Mio) :

```
POST {base_url}/pdfs
Authorization: Bearer <PDF_UPLOAD_TOKEN>   (jeton DISTINCT de MCP_AUTH_TOKEN, cf. ci-dessous)
Content-Type: multipart/form-data ; champ "pdf" = le fichier

→ 200 {"pdf_id": "...", "expire_dans_s": 1800}
```

C'est une commande shell (`curl -F pdf=@fichier.pdf ...`) que l'agent Dust
exécute dans son bac à sable — pas un argument d'outil MCP à construire à la
main. Le `pdf_id` retourné se passe ensuite tel quel à `inventaire_pdf` PUIS
à chaque `extraire_page` (`mcp_server/pdf_cache.py`, 30 min, prolongées à
chaque lecture). `pdf_base64` reste accepté en repli sur ces deux outils
(petits fichiers, tests directs — cf. `mcp_server/util.py::resoudre_pdf`),
mais `pdf_id` est le chemin **normal** pour un agent Dust réel. Fournir
exactement un des deux ; si `pdf_id` est inconnu ou expiré, l'outil refuse
explicitement (`ValueError`) — il suffit de retéléverser le PDF.

⚠️ Décisions assumées :
- Ceci réintroduit un état serveur entre deux appels MCP (le principe
  « aucun état conservé entre appels » ne s'applique plus au PDF d'entrée,
  seulement aux dossiers de travail par appel, cf. `mcp_server/util.py`) —
  accepté car les deux problèmes observés sont plus coûteux que la
  simplicité du « sans état ».
- `POST /pdfs`, contrairement au téléchargement de sortie (`fichiers.py`,
  volontairement exempté du Bearer), est protégé par un jeton Bearer — mais
  un jeton **DISTINCT** de `MCP_AUTH_TOKEN` (`PDF_UPLOAD_TOKEN`, cf.
  `mcp_server/auth.py`), à portée réduite à ce seul point d'entrée. Raison :
  `PDF_UPLOAD_TOKEN` est destiné à apparaître en clair dans les instructions
  d'un agent Dust (aucun mécanisme de secret injectable trouvé côté Dust au
  moment d'écrire ceci), sa fuite est donc assumée comme éventualité
  réaliste — un jeton à portée réduite limite les dégâts à « peut téléverser
  des PDF », jamais à « peut appeler `exporter_pdf` ». `MCP_AUTH_TOKEN`,
  lui, n'apparaît jamais dans un texte destiné à être collé où que ce soit,
  et continue de protéger `/mcp` (tous les outils) seul.
- Contrairement au registre de téléchargement (`fichiers.py`, usage
  UNIQUE), le cache PDF est **réutilisable** (une lecture par page) et son
  expiration **glisse** à chaque lecture.

## Fichiers binaires en sortie : des URLs, jamais du contenu inline

Le SDK MCP officiel (streamable-http, `mcp>=2.0`) plafonne à **1 Mio** la
taille d'une réponse d'outil côté client, sans réglage possible côté
serveur — dépassé par un PPTX réel (3-5 Mo) ou plusieurs images de
vérification dans une même réponse (constaté en testant le vrai protocole,
pas seulement en Python : `tests/mcp/test_server_http.py`). Chaque outil qui
produit un fichier (image, PPTX, PDF) renvoie donc une **URL de
téléchargement à usage unique** (`..._url` + `..._sha256`), jamais le
contenu en base64.

Flux pour l'agent : `GET` l'URL → ré-encoder les octets reçus en base64 →
fournir ce base64 en entrée de l'outil suivant qui en a besoin (ex.
`extraire_page.image_url` → `assembler_pptx.planches[].image_base64`). Les
*entrées* des outils restent en base64 classique, sans limite particulière
(bornées par `TAILLE_MAX_REQUETE_OCTETS`, cf. `server.py`) — à l'exception
du PDF fabricant, transmis une seule fois (cf. section précédente,
`pdf_id`).

Sécurité du lien (`mcp_server/fichiers.py`) — comme une URL S3 pré-signée :
jeton aléatoire imprévisible (256 bits), à usage unique (supprimé dès le
premier téléchargement), 5 minutes de durée de vie s'il n'est jamais
récupéré. **Volontairement accessible sans le jeton Bearer** du serveur MCP
(ci-dessous) : ce lien est destiné à être cliqué directement par
l'utilisateur final dans son navigateur (relayé par Dust), qui ne doit
jamais recevoir le jeton Bearer serveur-à-serveur.

## Variables d'environnement

| Variable | Obligatoire | Rôle |
|---|---|---|
| `MCP_AUTH_TOKEN` | **Oui** | Jeton Bearer serveur-à-serveur, protège `/mcp` (tous les outils). Le serveur refuse de démarrer si absent/vide. Ne doit JAMAIS apparaître dans un texte destiné à être collé où que ce soit (ex. instructions d'agent Dust). |
| `PDF_UPLOAD_TOKEN` | **Oui** | Jeton Bearer **distinct** de `MCP_AUTH_TOKEN`, protège UNIQUEMENT `POST /pdfs`. Le serveur refuse de démarrer si absent/vide. Destiné, lui, à apparaître en clair dans les instructions de l'agent Dust (cf. section « Le PDF fabricant en entrée ») — portée réduite exprès pour limiter les dégâts d'une fuite. |
| `MCP_PUBLIC_BASE_URL` | Non | URL publique du serveur, pour construire les liens de téléchargement. Sur Render, `RENDER_EXTERNAL_URL` (fournie automatiquement) sert de repli — inutile de la définir là. Ailleurs, ou pour la surcharger, la définir explicitement (ex. `https://plan-validation-mcp.exemple.com`). |
| `PORT` | Non | Port d'écoute (8000 par défaut ; Render le fournit automatiquement). |

## Lancer en local

```bash
pip install -r mcp_server/requirements.txt
MCP_AUTH_TOKEN=un-secret-de-test PDF_UPLOAD_TOKEN=un-autre-secret-de-test python -m mcp_server.server
# écoute sur http://127.0.0.1:8000/mcp
```

⚠️ Sans PowerPoint installé (ce serveur n'utilise jamais PowerPoint/COM,
LibreOffice uniquement — mais `core/render.py` bascule automatiquement),
`verifier_rendu` et `exporter_pdf` échouent explicitement s'il n'y a pas de
`soffice` (LibreOffice) sur le `PATH` en local.

## Tests

```bash
pytest tests/mcp/
```

- `test_fichiers.py` — le mécanisme de publication par URL (isolation entre
  deux fichiers, usage unique, expiration), sans dépendre du pipeline.
- `test_tools_local.py` — les 6 outils appelés directement comme fonctions
  Python, pipeline complet sur `tests/fixtures/DHYA2_test.pdf`.
- `test_server_http.py` — le **vrai protocole MCP** (process serveur séparé
  + vrai client MCP) : jeton Bearer exigé, liste des outils, appel réel
  d'outil, téléchargement réel d'un fichier publié, upload réel via un
  process `curl` externe (pas le client MCP Python) sur `POST /pdfs`.
- `test_deploiement_mcp.py` — contrôles statiques sur `Dockerfile` /
  `requirements.txt` (fonts-liberation, versions épinglées).

Ces deux derniers fichiers nécessitent la fixture `tests/fixtures/DHYA2_test.pdf`
(déjà présente, partagée avec les tests de l'app Streamlit) — ils sont
sautés silencieusement si elle est absente.

## Docker

```bash
docker build -f mcp_server/Dockerfile -t plan-validation-mcp .
docker run --rm -p 8000:8000 \
  -e MCP_AUTH_TOKEN=un-secret-de-test \
  -e PDF_UPLOAD_TOKEN=un-autre-secret-de-test \
  -e MCP_PUBLIC_BASE_URL=http://127.0.0.1:8000 \
  plan-validation-mcp
```

Le contexte de build est la **racine du dépôt** (pas `mcp_server/`) : le
Dockerfile y copie `core/`, `data/` et `templates/`, partagés avec l'app
Streamlit.

⚠️ **Non vérifié en conditions réelles** (pas de Docker disponible sur le
poste où ce serveur a été écrit) — build et exécution du conteneur à
confirmer avant le premier déploiement, cf. « Limites connues » ci-dessous.

## Déploiement sur Render

Comme l'app Streamlit, via le `render.yaml` à la racine du dépôt (Blueprint) :
le service `plan-validation-mcp` y est déjà décrit (`dockerfilePath:
mcp_server/Dockerfile`, `dockerContext: .`). À la création d'un NOUVEAU
service à partir du Blueprint, Render demande la valeur de `MCP_AUTH_TOKEN`
ET `PDF_UPLOAD_TOKEN` (jamais commitées, deux jetons DISTINCTS — cf. section
« Le PDF fabricant en entrée ») — choisir deux jetons longs et aléatoires
(ex. `openssl rand -hex 32`, une fois chacun). `MCP_PUBLIC_BASE_URL` n'a pas
besoin d'être définie : Render fournit `RENDER_EXTERNAL_URL` automatiquement,
utilisée en repli.

⚠️ **Sur un service DÉJÀ déployé** (ce qui est le cas de
`plan-validation-mcp` au moment où `PDF_UPLOAD_TOKEN` a été introduit) :
Render ne redemande PAS les variables du Blueprint après la création
initiale — ajouter `PDF_UPLOAD_TOKEN` **manuellement** dans le dashboard
Render (service → **Environment**) AVANT de déployer un commit qui
l'exige, sous peine de boucle de crash au démarrage (`jeton_upload_attendu`
lève `RuntimeError` si absent).

Sans Blueprint : créer manuellement un **Web Service**, environnement
**Docker**, Dockerfile Path = `mcp_server/Dockerfile`, Docker Build Context
Directory = `.` (racine), puis ajouter `MCP_AUTH_TOKEN` ET `PDF_UPLOAD_TOKEN`
dans **Environment** avant le premier déploiement.

Mêmes limites de palier gratuit que l'app Streamlit (mise en veille après
~15 min d'inactivité, 512 Mo de RAM) — cf. README racine, section
« Limites du palier gratuit ».

## Connecter à Dust

Dans la configuration d'un agent Dust, ajouter un serveur MCP personnalisé :

- **URL** : `https://<nom-du-service>.onrender.com/mcp`
- **Authentification** : Bearer, jeton = la valeur de `MCP_AUTH_TOKEN`
  (**jamais** `PDF_UPLOAD_TOKEN` ici — ce dernier n'ouvre PAS `/mcp`, cf.
  section « Le PDF fabricant en entrée »)

Le serveur (`mcp_server/server.py::_INSTRUCTIONS`) explique déjà à l'agent,
via le protocole MCP lui-même, qu'il doit téléverser le PDF par `curl` avant
d'appeler `inventaire_pdf` — avec un placeholder `<PDF_UPLOAD_TOKEN>` qu'il
ne peut pas résoudre seul. Si l'agent a besoin d'une consigne plus explicite
(ex. il continue de tenter du base64 malgré tout), coller ceci dans ses
propres instructions Dust, **avec la valeur réelle de `PDF_UPLOAD_TOKEN` en
clair** (jamais celle de `MCP_AUTH_TOKEN` — cf. avertissement ci-dessous) :

> Pour traiter un PDF fabricant joint à la conversation, NE l'encode JAMAIS
> toi-même en base64. Exécute d'abord, dans ton environnement d'exécution :
> `curl -X POST https://<nom-du-service>.onrender.com/pdfs -H "Authorization:
> Bearer <PDF_UPLOAD_TOKEN_EN_CLAIR>" -F pdf=@<chemin_local_du_fichier>.pdf`
> — la réponse contient `pdf_id`. Utilise ensuite ce `pdf_id` (jamais de
> base64) dans `inventaire_pdf` puis dans chaque `extraire_page`.

⚠️ **Pourquoi un jeton dédié plutôt que `MCP_AUTH_TOKEN`** : coller cette
formulation dans les instructions d'un agent Dust rend le jeton visible en
clair par quiconque a accès en édition à cet agent — aucun mécanisme de
secret injectable dans le bac à sable n'a été trouvé côté Dust (deux
recherches ciblées, sans résultat) au moment d'écrire ceci. `PDF_UPLOAD_TOKEN`
est donc un jeton à portée réduite, généré et défini UNIQUEMENT pour cet
usage : sa fuite éventuelle ne donne accès qu'à l'upload de PDF, jamais aux
autres outils (`exporter_pdf` compris), qui restent protégés par
`MCP_AUTH_TOKEN` — jamais collé dans un texte d'agent. Si une preuve d'un
mécanisme de secret Dust apparaît plus tard, réévaluer cette formulation.

⚠️ Point restant, non vérifiable depuis ce dépôt : que le bac à sable de
l'agent Dust puisse bien EXÉCUTER une commande shell (`curl`) — pas une
question d'accès à un secret (résolue ci-dessus), mais de capacité
d'exécution shell tout court. À confirmer par l'essai réel.

## Limites connues (honnêtes)

- **Docker non testé en conditions réelles** sur ce poste (pas de Docker
  disponible) — à vérifier au premier déploiement : build de l'image,
  démarrage du conteneur, `fonts-liberation` effectivement pris en compte
  par LibreOffice, rendu réel via `verifier_rendu`/`exporter_pdf`.
- **`traduire_mots(glossaire_version=...)`** : seule la valeur `"latest"`
  est supportée — `data/glossaire.py` est un dictionnaire Python statique,
  pas versionné.
- **Vue 3D fournisseur non traduite** (callouts) : limite déjà connue du
  pipeline `core/`, pas spécifique au serveur MCP — cf. README racine.
- **Un seul process, sans état partagé entre appels — sauf le PDF fabricant
  en cache** (voir `mcp_server/util.py`) : chaque appel a son propre dossier
  de travail jetable. Le registre de fichiers publiés (`mcp_server/fichiers.py`)
  et le cache du PDF en cours (`mcp_server/pdf_cache.py`, cf. section
  « Le PDF fabricant en entrée » ci-dessus), eux, vivent en mémoire du
  process — un redémarrage du service (déploiement, mais aussi OOM-kill
  Render, déjà observé sur ce projet) invalide tous les liens de
  téléchargement en attente (acceptable : durée de vie 5 min) ET tout
  `pdf_id` en cours (acceptable : il suffit de relancer `inventaire_pdf`).
  Les fichiers laissés sur disque par l'instance précédente sont nettoyés au
  démarrage du process suivant (`fichiers.purger_dossiers_orphelins_au_demarrage`
  et `pdf_cache.purger_dossiers_orphelins_au_demarrage`), pas de fuite
  accumulée entre redémarrages.
- **Téléchargement interrompu = lien définitivement grillé.** Le jeton est
  invalidé et le fichier supprimé du disque dès que le serveur COMMENCE à
  répondre au `GET`, pas une fois la réception confirmée côté client. Une
  connexion coupée en cours de transfert (réseau, onglet fermé) rend donc le
  lien inutilisable pour un nouvel essai — il faut relancer l'outil qui l'a
  produit, pas retélécharger le même lien. Limite acceptée telle quelle : la
  corriger proprement demanderait soit d'affaiblir l'usage unique (fenêtre
  de re-essai, ce que la sécurité du lien exclut), soit un mécanisme de
  confirmation de réception que HTTP ne fournit pas simplement.
