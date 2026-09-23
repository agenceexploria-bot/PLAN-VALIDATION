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

## Le PDF fabricant en entrée : transmis une seule fois, référencé par `pdf_id`

Constaté avec un agent Dust réel traitant un vrai plan multi-pages :
renvoyer le PDF complet en base64 à **chaque** appel (`inventaire_pdf`, puis
`extraire_page` une fois par page) faisait hésiter l'agent sur le volume
dès quelques pages — il se mettait à improviser des contournements
dangereux (rastérisation basse résolution, OCR local, découpage manuel du
PDF) plutôt que d'appeler l'outil normalement.

`inventaire_pdf` reçoit donc le PDF en base64 **une seule fois** par
pipeline, le met en cache côté serveur (`mcp_server/pdf_cache.py`, 30 min,
prolongées à chaque lecture) et retourne un `pdf_id` opaque ; `extraire_page`
le référence par ce `pdf_id` au lieu de retransmettre le PDF. Si `pdf_id`
est inconnu ou expiré, `extraire_page` refuse explicitement (`ValueError`)
— il suffit de relancer `inventaire_pdf`.

⚠️ Décision assumée : ceci réintroduit un état serveur entre deux appels
MCP (le principe « aucun état conservé entre appels » ne s'applique plus au
PDF d'entrée, seulement aux dossiers de travail par appel, cf.
`mcp_server/util.py`) — accepté car le problème observé (hésitation de
l'agent face au volume répété) est plus coûteux que la simplicité du «
sans état ». Contrairement au registre de téléchargement (`fichiers.py`,
usage UNIQUE), le cache PDF est **réutilisable** (une lecture par page) et
son expiration **glisse** à chaque lecture.

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
| `MCP_AUTH_TOKEN` | **Oui** | Jeton Bearer serveur-à-serveur (Dust → ce serveur). Le serveur refuse de démarrer si absent/vide. |
| `MCP_PUBLIC_BASE_URL` | Non | URL publique du serveur, pour construire les liens de téléchargement. Sur Render, `RENDER_EXTERNAL_URL` (fournie automatiquement) sert de repli — inutile de la définir là. Ailleurs, ou pour la surcharger, la définir explicitement (ex. `https://plan-validation-mcp.exemple.com`). |
| `PORT` | Non | Port d'écoute (8000 par défaut ; Render le fournit automatiquement). |

## Lancer en local

```bash
pip install -r mcp_server/requirements.txt
MCP_AUTH_TOKEN=un-secret-de-test python -m mcp_server.server
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
  d'outil, téléchargement réel d'un fichier publié.
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
mcp_server/Dockerfile`, `dockerContext: .`). Au moment de la création à
partir du Blueprint, Render demande la valeur de `MCP_AUTH_TOKEN` (jamais
commitée) — choisir un jeton long et aléatoire (ex. `openssl rand -hex 32`).
`MCP_PUBLIC_BASE_URL` n'a pas besoin d'être définie : Render fournit
`RENDER_EXTERNAL_URL` automatiquement, utilisée en repli.

Sans Blueprint : créer manuellement un **Web Service**, environnement
**Docker**, Dockerfile Path = `mcp_server/Dockerfile`, Docker Build Context
Directory = `.` (racine), puis ajouter `MCP_AUTH_TOKEN` dans **Environment**
avant le premier déploiement.

Mêmes limites de palier gratuit que l'app Streamlit (mise en veille après
~15 min d'inactivité, 512 Mo de RAM) — cf. README racine, section
« Limites du palier gratuit ».

## Connecter à Dust

Dans la configuration d'un agent Dust, ajouter un serveur MCP personnalisé :

- **URL** : `https://<nom-du-service>.onrender.com/mcp`
- **Authentification** : Bearer, jeton = la valeur de `MCP_AUTH_TOKEN`

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
