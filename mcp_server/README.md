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
| `extraire_page` | 2 | Rendu + mots détectés d'UNE page (`role="planche"\|"garde"\|"specs"`), référencée par `pdf_id` ; met sa réponse en cache et retourne `extraction_id` |
| `traduire_mots` | 3 | Glossaire Vertical appliqué aux mots d'une page déjà extraite, référencée par `extraction_id` |
| `assembler_pptx` | 4 | Montage du PPTX (copie d'un plan existant, jamais un template vide) ; planches et vue 3D référencées par `extraction_id` ; met le PPTX en cache et retourne `pptx_id` |
| `verifier_rendu` | 5 | Rendu par slide (LibreOffice) + rapport de vérification — portail obligatoire ; PPTX référencé par `pptx_id`, `words_par_page` par `extraction_id` |
| `exporter_pdf` | 6 | Export PDF final, refuse sans `valide=True` ; PPTX référencé par `pptx_id` |

Chaque outil a une description détaillée dans son propre docstring
(`mcp_server/tools/*.py`) — c'est ce que l'agent Dust lit pour savoir quand
et comment l'utiliser. Ordre d'appel attendu : `inventaire_pdf` →
`extraire_page` (UNE fois par page retenue, obtenir `extraction_id` —
roles `garde` et `specs` identiques à l'extraction : image + vue 3D
recadrée) → `traduire_mots(extraction_id=...)` (role `planche` sur les
planches, `specs` sur la page specs) →
`assembler_pptx(planches=[{extraction_id}, ...], specs={extraction_id})`
(la vue 3D de la page specs est reprise automatiquement quand `view3d` est
absent, et signalée dans `a_signaler_a_l_utilisateur`, à relayer avant
`verifier_rendu` ; `view3d={extraction_id}` seulement pour une vue 3D sur
une autre page) (obtenir `pptx_id`) → `verifier_rendu(pptx_id=..., words_par_page={page:
extraction_id, ...})` (TOUTES les pages extraites, page specs comprise) (montrer les images à l'utilisateur, obtenir son
accord) → `exporter_pdf(pptx_id=..., valide=True)`.

**Principe général, appliqué à TOUTES les entrées** (cf. sections
suivantes) : aucun blob (PDF, image, PPTX, JSON de mots) ne transite par
l'agent d'un outil à l'autre. Chaque outil qui produit une donnée
volumineuse la garde côté serveur et renvoie un identifiant ; l'outil
suivant reçoit cet identifiant. Le contenu brut (base64/JSON) reste accepté
en repli partout, pour les petits fichiers et les tests directs.

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

## Les mots extraits d'une page : mis en cache, référencés par `extraction_id`

Même famille de problème que le PDF, trouvée juste après en conditions
réelles : `traduire_mots` rejetait les données d'une page dense **sans
message exploitable**. En cause : l'agent Dust tentait de RETRANSMETTRE le
`words_data` complet (sortie d'`extraire_page`) en argument de
`traduire_mots`, butait sur la taille du JSON, et commençait à le
reconstruire manuellement plutôt que de le relayer tel quel — exactement la
même dérive que celle vue sur le PDF (contournements dangereux plutôt qu'un
appel d'outil normal).

`extraire_page` met donc sa réponse complète en cache côté serveur
(`mcp_server/extraction_cache.py`, 30 min glissantes) et retourne un
`extraction_id` EN PLUS du contenu inline (`words`, `page_size_pts` —
conservés : ce n'est pas leur RÉCEPTION qui posait problème, seulement leur
RETRANSMISSION). `traduire_mots` et `verifier_rendu` (`words_par_page` —
qui AGRÈGE plusieurs pages, pire cas encore) acceptent cet `extraction_id`
à la place du JSON complet — cf. `mcp_server/util.py::resoudre_words_data`
et `resoudre_entree_words_par_page`. `words_data` reste accepté en repli
sur les deux (petits fichiers, tests directs).

Corrigé au passage (bug indépendant de la cause de fond, présent AVANT ce
mécanisme) : un `words_data` malformé renvoie désormais toujours une erreur
explicite nommant le champ en cause (`util.valider_words_data`) — jamais un
`KeyError`/`TypeError` opaque, jamais un échec silencieux. `verifier_rendu`
résout aussi `words_par_page` AVANT le rendu PowerPoint/LibreOffice
(coûteux et parfois fragile), pas après : un `extraction_id` invalide
échoue immédiatement plutôt que de gaspiller un rendu complet.

⚠️ Décision assumée, identique à celle du PDF : cache en mémoire (pas sur
disque, contrairement à `pdf_cache.py` — `words_data` est du JSON structuré,
sans commune mesure avec un PDF de 50 Mo), réutilisable, expiration
glissante. Rien à purger au démarrage (rien n'est écrit sur disque) — un
redémarrage du process vide simplement le registre, comme pour le reste de
l'état en mémoire (registre de téléchargement, cache PDF).

## Images et PPTX : mis en cache, référencés par `extraction_id` / `pptx_id`

Troisième occurrence de la même famille, en conditions réelles :
`assembler_pptx` exigeait l'image de planche en base64 — une planche A3 à
300 dpi (4961×3508 px) représentait ~165k jetons selon l'agent Dust
lui-même, pour ~48k disponibles. Plutôt qu'un correctif de plus au cas par
cas, audit de toutes les entrées des 6 outils :

| Outil | Entrée volumineuse | Référence acceptée |
|---|---|---|
| `inventaire_pdf`, `extraire_page` | `pdf_base64` | `pdf_id` (déjà) |
| `traduire_mots` | `words_data` | `extraction_id` (déjà) |
| `assembler_pptx` | `planches[].image_base64`, `view3d.image_base64` | `extraction_id` de la page (**nouveau**) |
| `assembler_pptx` | `planches[].labels` (sortie de `traduire_mots`, agrégée sur N planches) | repris automatiquement via le même `extraction_id` (**nouveau**) |
| `assembler_pptx` | `planches[].page_w_pt`, `page_n`, `view3d.image_page_w_pt` | repris via `extraction_id` — plus aucune valeur retapée par l'agent (**nouveau**) |
| `assembler_pptx` | `specs.table` (sortie de `traduire_mots(role="specs")`) | repris via l'`extraction_id` de la page specs ; `specs` est désormais **OBLIGATOIRE** (**nouveau**, cf. ci-dessous) |
| `verifier_rendu`, `exporter_pdf` | `pptx_base64` (PPTX de plusieurs Mo — pas encore vu échouer, mais pire cas que la planche) | `pptx_id` (**nouveau**) |
| `verifier_rendu` | `words_par_page` | `extraction_id` par page (déjà) |

Restent inline, volontairement : `meta`, `view3d.callouts` et
`hors_glossaire` (optionnels) — de l'ordre du Ko. `specs.table` reste
accepté inline en repli.

**`specs` obligatoire** : au premier test réel sur Render, la page specs
(slide 2) est sortie quasi blanche — ni vue 3D ni tableau FR — sans aucune
erreur : `core/assemble.py` saute silencieusement un tableau absent. Les
arguments réellement envoyés par l'agent n'étant pas journalisés, on ne
sait pas s'il avait omis `specs` ou envoyé un tableau vide ; dans les deux
cas `assembler_pptx` refuse désormais explicitement (message indiquant
d'appeler `traduire_mots(role='specs')` puis de passer
`specs={"extraction_id": ...}`), et le contrôle de complétude de
`verifier_rendu` signale une page specs vide.

Mécanisme (`mcp_server/cache_disque.py`) : même principe que `pdf_cache`
— SUR DISQUE (quelques Mo par image/PPTX, palier Render à 512 Mo),
identifiant imprévisible, réutilisable, 30 min glissantes, purge des
dossiers orphelins au démarrage. `extraire_page` y range l'image de la
planche (`role="planche"`) et la vue 3D (`role="garde"` ou `"specs"`), rattachées à
l'`extraction_id` ; `traduire_mots(extraction_id=...)` y rattache ses
labels ; lire l'extraction prolonge aussi ses images.
`assembler_pptx` range le PPTX produit et retourne `pptx_id`.

⚠️ Décision : **`extraction_id`, pas l'`image_url`** d'`extraire_page`,
comme référence d'image. Les URLs de téléchargement sont à usage unique et
expirent en 5 min (modèle de sécurité des liens destinés à l'utilisateur
final) : un agent qui a déjà ouvert l'image pour la montrer, ou un
pipeline plus long que 5 min, aurait cassé l'assemblage. Les URLs restent
renvoyées, uniquement pour MONTRER un fichier à l'utilisateur.

`planches[].labels` fourni explicitement reste accepté pour SURCHARGER les
labels de `traduire_mots`. Appeler `assembler_pptx` sur une planche sans
avoir appelé `traduire_mots` dessus est refusé explicitement.

## Erreurs d'outil : lisibles par l'agent

Bug trouvé en analysant l'échec d'`assembler_pptx` : le SDK `mcp` 2.x ne
transmet au client QUE le texte d'une `ToolError`. Toute autre exception
— dont les `ValueError` que nos outils lèvent pour une entrée refusée —
arrivait réduite à « Error executing tool <nom> ». Le correctif précédent
sur `traduire_mots` (message nommant le champ en cause) n'atteignait donc
jamais l'agent Dust : seuls les tests en appel Python direct le voyaient.
`server.py::_erreurs_explicites` convertit désormais toute `ValueError` en
`ToolError` à l'enregistrement des outils (vérifié sur le vrai protocole,
`tests/mcp/test_server_http.py`). Même traitement pour un échec du moteur
de rendu dans `verifier_rendu` / `exporter_pdf` (`util.echec_rendu_explicite`,
LibreOffice seul sous Docker/Render) : `ToolError` portant le détail du
moteur, et précisant à l'agent que ses arguments ne sont pas en cause (à
signaler à l'utilisateur, pas à réessayer tel quel). Les autres exceptions
(vrais plantages) restent masquées côté client, comme le veut le SDK.

`assembler_pptx` valide en plus toute son entrée AVANT le montage et nomme
le champ en cause (`planches[1].extraction_id`, `planches[0].labels[3].bbox`,
`view3d.image_base64 : base64 invalide`...), au lieu d'un `KeyError` /
`binascii.Error` opaque.

## Fichiers binaires en sortie : des URLs, jamais du contenu inline

Le SDK MCP officiel (streamable-http, `mcp>=2.0`) plafonne à **1 Mio** la
taille d'une réponse d'outil côté client, sans réglage possible côté
serveur — dépassé par un PPTX réel (3-5 Mo) ou plusieurs images de
vérification dans une même réponse (constaté en testant le vrai protocole,
pas seulement en Python : `tests/mcp/test_server_http.py`). Chaque outil qui
produit un fichier (image, PPTX, PDF) renvoie donc une **URL de
téléchargement à usage unique** (`..._url` + `..._sha256`), jamais le
contenu en base64.

Ces URLs servent uniquement à MONTRER un fichier à l'utilisateur (images
de vérification, PPTX, PDF final). L'agent ne doit jamais les télécharger
pour ré-encoder le contenu en base64 et le passer à l'outil suivant :
celui-ci reçoit un identifiant (`extraction_id`, `pptx_id`, cf. section
précédente).

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
  process `curl` externe (pas le client MCP Python) sur `POST /pdfs`,
  messages d'erreur lisibles côté client, et **scénario agent Dust
  complet** (`test_scenario_agent_dust_references_uniquement`) : PDF A3,
  extraction à 300 dpi, chaque argument d'appel mesuré (< 8 Ko), image de
  planche en pleine résolution (4961×3508) dans le PPTX produit.
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

- **Polices du rendu dans le conteneur : correctif pas encore vérifié.**
  Image construite et déployée sur Render (commit `fe752d4`), pipeline
  complet réussi avec un vrai agent Dust — mais le PDF produit avait des
  espaces parasites au milieu des mots et des cotes cassées : `fonts-liberation`
  est bien présente ET utilisée (LiberationSans embarquée dans le PDF), mais
  les gabarits sont en Calibri, absente du conteneur. Correctif :
  `fonts-crosextra-carlito` (équivalent métrique de Calibri). Non
  reproductible sous Windows (Calibri installée) et pas de Docker sur ce
  poste : à confirmer au prochain déploiement (le PDF doit embarquer
  « Carlito », texte propre).
- **`traduire_mots(glossaire_version=...)`** : seule la valeur `"latest"`
  est supportée — `data/glossaire.py` est un dictionnaire Python statique,
  pas versionné.
- **Vue 3D fournisseur non traduite** (callouts) : limite déjà connue du
  pipeline `core/`, pas spécifique au serveur MCP — cf. README racine.
- **Un seul process, sans état partagé entre appels — sauf le PDF fabricant,
  les données de mots extraits, leurs images et le PPTX assemblé en cache**
  (voir `mcp_server/util.py`, `mcp_server/cache_disque.py` — un redémarrage
  invalide aussi tout `pptx_id` en cours : relancer `assembler_pptx`) :
  chaque appel a son propre dossier de travail jetable. Le registre de
  fichiers publiés (`mcp_server/fichiers.py`), le cache du PDF en cours
  (`mcp_server/pdf_cache.py`) et le cache des extractions en cours
  (`mcp_server/extraction_cache.py`, cf. sections ci-dessus), eux, vivent en
  mémoire du process — un redémarrage du service (déploiement, mais aussi
  OOM-kill Render, déjà observé sur ce projet) invalide tous les liens de
  téléchargement en attente (acceptable : durée de vie 5 min), tout `pdf_id`
  en cours (acceptable : il suffit de relancer `inventaire_pdf`) ET tout
  `extraction_id` en cours (acceptable : il suffit de relancer
  `extraire_page`). Les fichiers laissés sur disque par l'instance
  précédente sont nettoyés au démarrage du process suivant
  (`fichiers.purger_dossiers_orphelins_au_demarrage` et
  `pdf_cache.purger_dossiers_orphelins_au_demarrage` — rien d'équivalent
  n'est nécessaire pour `extraction_cache`, qui n'écrit jamais sur disque),
  pas de fuite accumulée entre redémarrages.
- **Téléchargement interrompu = lien définitivement grillé.** Le jeton est
  invalidé et le fichier supprimé du disque dès que le serveur COMMENCE à
  répondre au `GET`, pas une fois la réception confirmée côté client. Une
  connexion coupée en cours de transfert (réseau, onglet fermé) rend donc le
  lien inutilisable pour un nouvel essai — il faut relancer l'outil qui l'a
  produit, pas retélécharger le même lien. Limite acceptée telle quelle : la
  corriger proprement demanderait soit d'affaiblir l'usage unique (fenêtre
  de re-essai, ce que la sécurité du lien exclut), soit un mécanisme de
  confirmation de réception que HTTP ne fournit pas simplement.
