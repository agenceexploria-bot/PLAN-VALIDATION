# Plan de validation Vertical

Application Streamlit qui transforme un plan de production fabricant (PDF
vectoriel, anglais/italien/turc) en **Plan de validation Vertical**
(PowerPoint puis PDF, à la charte de l'entreprise, entièrement éditable).

Réécriture déterministe (sans agent Claude) du skill Claude Code
`plan-validation-vertical` (`~/.claude/skills/plan-validation-vertical/`) :
même logique métier et mêmes pièges déjà debuggés, portés en code Python +
interactions Streamlit (boutons, formulaires, aperçus).

## Avant de commencer : ressources indispensables

- **Les deux gabarits officiels**, déjà en place dans `templates/` :
  `LD82040.pptx` (monte-charge non accompagné) et `LD64397.pptx`
  (accompagné). Ne jamais les modifier à la main — l'application travaille
  toujours sur une copie.
- **Microsoft PowerPoint installé** sur le poste qui fait tourner l'app,
  *recommandé* : le rendu de vérification (étape 4) et l'export PDF
  (étape 5) le pilotent via COM (`pywin32`) en priorité — c'est la
  référence visuelle la plus fidèle.
- **LibreOffice**, *optionnel* — moteur de secours automatique si
  PowerPoint est absent ou en échec (licence non activée, poste non
  Windows...) : `core/render.py` bascule dessus sans intervention
  (rendu PNG et export PDF, via `soffice --headless`). **Pas un paquet
  pip** : à installer à part
  ([libreoffice.org/download](https://www.libreoffice.org/download/download/)),
  détecté automatiquement dans le `PATH` (`soffice`/`soffice.exe`) ou aux
  emplacements d'installation standards. L'app affiche toujours quel
  moteur a produit le rendu affiché, et réutilise le même moteur pour
  l'export PDF que celui validé au contrôle visuel.
- Sans PowerPoint NI LibreOffice, tout le pipeline fonctionne jusqu'au PPTX
  téléchargeable (étape 5) — seuls le contrôle visuel réel et l'export PDF
  sont indisponibles sur ce poste (l'app le signale clairement plutôt que de
  planter) ; le PPTX reste ouvrable et vérifiable dans PowerPoint/LibreOffice
  ailleurs.

## Installation

```
pip install -r requirements.txt
streamlit run app.py
```

## Pipeline (5 écrans)

1. **Upload & inventaire** — dépôt du PDF, aperçu de chaque page, rôle
   attribué par l'utilisateur (page de garde/vue 3D, planche dessin, page
   specs, ignorée). Aucun découpage par défaut présumé fiable : à corriger
   au cas par cas.
2. **Métadonnées** — n° d'affaire (LDxxxxx), client, dessinateur, indice,
   type d'équipement ; checklist revue d'affaire optionnelle (.xlsm/.xlsx).
3. **Traduction & assemblage** — extraction programmatique (pymupdf) des
   cotes et libellés, traduction via le glossaire Vertical, tableau specs
   reconstruit **éditable** (la reconstruction automatique est un
   best-effort : à corriger si la mise en page fabricant est tabulaire),
   puis montage du PPTX (copie d'un vrai plan de validation existant,
   jamais un template vide).
4. **Vérification** — portail obligatoire : contrôle des cotes (3 sources :
   PDF fabricant / PPTX généré / checklist), rendu réel PowerPoint à
   valider visuellement par l'utilisateur, contrôle de complétude. Le
   passage à l'étape 5 exige une validation humaine explicite.
5. **Téléchargement** — PPTX immédiatement (document de travail), export
   PDF débloqué uniquement après validation de l'étape 4.

## Structure du projet

```
plan-validation-app/
├── app.py                  # UI Streamlit (5 écrans), aucune logique métier
├── core/
│   ├── extract.py          # étape 2 : rendu 300 dpi, cotes, rédaction (pymupdf)
│   ├── cover.py             # extraction de la vue 3D fournisseur sans rognage
│   ├── translate.py         # étape 3 : glossaire, phrases multi-mots, cotes fusionnées
│   ├── assemble.py          # étape 4 : montage PPTX (copie d'un plan existant)
│   ├── verify.py             # étape 5 : contrôle des cotes + complétude
│   ├── render.py              # rendu réel + export PDF (PowerPoint COM, secours LibreOffice)
│   └── checklist.py            # lecture .xlsm, détection "modèle vierge"
├── data/
│   └── glossaire.py             # glossaire FR Vertical (transcrit du skill)
├── templates/
│   ├── LD82040.pptx / LD64397.pptx   # gabarits officiels (fournis par l'entreprise)
│   └── contact_nadia.png / contact_jeremie.png  # photos contacts projet
├── tests/
│   ├── fixtures/             # vrai plan fabricant de test + vraie checklist vierge
│   ├── mcp/                    # tests du serveur MCP (cf. mcp_server/README.md)
│   └── test_*.py              # non-régression (pytest)
├── mcp_server/                # serveur MCP (pour Dust) — service séparé, réutilise core/
│   └── README.md               # documentation et déploiement dédiés
└── requirements.txt
```

## Différences volontaires par rapport au skill Claude Code d'origine

- **Purge du cartouche par libellé, pas par valeur littérale.** Le
  `build_pptx.py` du skill remplaçait des valeurs connues à l'avance
  (`"LD82040"` → nouveau numéro). Cette app repère chaque champ par son
  libellé fixe (`Client :`, `Dessinateur :`...), ce qui purge aussi les
  résidus **cachés** d'un ancien dossier sans avoir à les connaître d'avance
  — approche validée sur `LD82040.pptx`, qui contient un « Client : DIMMER
  SARL » résiduel dans une cellule fusionnée masquée.
- **Rendu PowerPoint COM en priorité, LibreOffice en secours automatique.**
  Le cahier des charges initial demandait LibreOffice pour rester portable ;
  PowerPoint COM est resté le moteur prioritaire (référence visuelle la
  plus fidèle) tant qu'il est disponible, mais `core/render.py` bascule
  désormais automatiquement sur LibreOffice headless si l'automatisation
  PowerPoint échoue ou est absente (licence non activée, poste non
  Windows...) — le besoin de portabilité du cahier des charges initial est
  donc couvert, sans sacrifier la fidélité du rendu quand PowerPoint
  fonctionne.

## Limites connues (honnêtes, pas de faux "c'est fait")

- **Vue 3D de la page de specs : le texte du fabricant N'EST PAS traduit
  (lacune connue, pas un choix).** La spécification produit prévoit une image
  3D « traduite en place », mais cette traduction n'est pas implémentée
  (`callouts` reste vide dans `app.py`). Depuis la règle « on n'efface jamais
  sans reposer », la vue 3D est reprise telle quelle, sans rédaction : tout
  texte fournisseur qui s'y trouve (par exemple, sur le PDF de test, le bloc
  des tolérances : « DETAY », « ÖLÇEK 1:5 », « SERBEST ÖLÇÜ TOLERANSLARI »…)
  reste dans sa langue d'origine. À contrôler visuellement à l'étape 4.
  Point ouvert, à traiter avec la troncature de cette vue sur la droite.
- **Bloc de tolérances des planches** : pour tenir dans ses petites cellules,
  le français y est composé dans une police très réduite (~4 pt).
- **Reconstruction automatique du tableau specs imparfaite.** Un libellé qui
  se termine par « : » est rattaché à la valeur alignée à droite sur la même
  rangée du même bloc, mais une mise en page réellement tabulaire (libellés et
  valeurs dans des blocs de texte séparés) n'est pas reconstruite, et des
  lignes parasites (annotations « détail / échelle », libellé coupé sur deux
  lignes comme « TOP PLATFORM: ANTI SLIP TEAR METAL ») s'y glissent : l'étape 3 affiche un tableau
  éditable précisément pour cette raison — à corriger au cas par cas plutôt
  que de faire confiance à l'automatique.
- **Étiquettes en phrase hors glossaire quand le fabricant scinde une
  expression sur deux blocs de texte séparés** (ex. rencontré sur le PDF de
  test : "ANMA" et "BOYUTLARI" dans deux blocs distincts) : reste flaggé
  "hors glossaire" (texte inchangé, jamais une mauvaise traduction), listé
  dans le rapport de vérification pour validation par Marin.
- **Bloc de petites cellules (tolérances ISO 2768 en marge)** : les libellés
  verticaux sont composés avec la police réduite pour tenir dans leur cellule
  source (`fit_bbox`) ; sur une planche très dense, le français plus long que
  l'original peut encore frôler un voisin — à contrôler au rendu.
- **PowerPoint COM peut laisser un processus `POWERPNT.EXE` résiduel** après
  usage intensif (quirk connu de l'automatisation COM, pas spécifique à
  cette app) : à fermer via le Gestionnaire des tâches si les exports
  commencent à échouer.
- **`core/render.py` requiert d'être appelé depuis un thread où COM est
  initialisable** (géré automatiquement en interne) — trouvé en testant
  l'app via `streamlit.testing.v1.AppTest`, qui exécute le script exactement
  comme Streamlit le fait réellement (thread dédié, pas le thread principal).

## Glossaire : choix à valider par Marin

Le glossaire (`data/glossaire.py`) ne publie jamais d'alternative « a/b » sur un
document. Quand une source proposait deux termes, **le premier est retenu** et
l'autre est consigné ici — ce sont des décisions de terminologie métier, à
relire et à corriger si besoin (un test vérifie que ce tableau reste en phase
avec `ALTERNATIVES_ECARTEES`) :

| Terme source | Retenu | Alternative écartée |
|---|---|---|
| `ölçü` | cote | mesure |
| `kesit` | coupe | section |
| `ağırlık` | poids | masse |
| `kontrol` | contrôlé par | vérifié par |
| `onay` | approbation | visa |
| `anchor` | ancre | cheville |
| `disegno` / `drawing` | dessin | plan |

Entrées **retirées** car elles traduisaient à tort un mot courant (la langue
d'un mot isolé n'est pas détectée) : `not` (anglais « NOT », « DO NOT SCALE »),
`data` (anglais « DATA »), `piano` et `kat` (ambigus hors contexte). Conséquence
assumée : un « NOT » ou « KAT » turc légitime n'est plus traduit ; il apparaît
alors dans les termes hors glossaire du rapport, à traduire à la main.
Ajout : `outside` → « déporté (extérieur) » (valeur de « POWER PACK »).

Non tranchés, à décider par Marin — valeurs avec une précision entre
parenthèses qui pourrait être une alternative : `çizen` → « dessiné par
(dessinateur) », `loads on wall` → « charges sur le mur (efforts sur paroi) »,
`parça listesi` → « nomenclature (liste de pièces) ».

## Lancer les tests

```
pip install pytest
pytest tests/ -v
```

Les fixtures (`tests/fixtures/`) sont un vrai plan fabricant (DHYA.2,
turc + anglais, 2 pages) et une vraie checklist revue d'affaire (vierge,
comme c'est très souvent le cas en pratique) — pas des PDF reconstitués.

## Déploiement sur Render

L'app se déploie en Docker (nécessaire pour installer LibreOffice, qui
n'est pas un paquet pip — cf. `Dockerfile`) sur le palier **gratuit** de
[Render](https://render.com/).

> Le pipeline est aussi exposé comme serveur MCP (pour un agent Dust),
> service Render séparé — cf. `mcp_server/README.md`, pas documenté ici.

### Connecter le dépôt

1. Pousser ce dépôt sur GitHub (Render se connecte à un dépôt Git, pas à un
   upload de fichiers).
2. Dans le [dashboard Render](https://dashboard.render.com/) : **New +** →
   **Blueprint**, puis sélectionner ce dépôt. Render lit `render.yaml` à la
   racine et propose de créer le service web Docker qui y est décrit.
3. À la création, Render demande la valeur de `APP_PASSWORD` (déclarée
   `sync: false` dans `render.yaml` — jamais commitée dans le dépôt) :
   choisir un mot de passe et le saisir à ce moment-là. Pour le changer
   plus tard : **Dashboard → service → Environment**.
4. Sans passer par un Blueprint : créer manuellement un **Web Service**,
   environnement **Docker**, puis ajouter la variable d'environnement
   `APP_PASSWORD` dans **Environment** avant le premier déploiement.

`APP_PASSWORD` est **obligatoire** : si elle est absente ou vide (y compris
en exécution locale, ex. `streamlit run app.py` sans variable définie), l'app
refuse de démarrer plutôt que de laisser un accès libre par défaut.

### Limites du palier gratuit — à connaître avant de s'en servir en prod

- **Mise en veille après ~15 minutes d'inactivité** : le service s'arrête
  complètement, pas juste ralenti.
- **Réveil lent (30 à 50 secondes)** au premier accès suivant une mise en
  veille — la première requête d'un utilisateur reste en chargement
  pendant ce temps, sans indication du navigateur que c'est normal.
- **512 Mo de RAM** (0.1 CPU) — à surveiller si un rendu LibreOffice
  (conversion + export PNG page par page) tombe en même temps qu'une
  extraction PDF 300 dpi ou une autre session active : sur ce palier, un
  pic de charge peut faire redémarrer le service plutôt que de simplement
  ralentir.
- **Disque éphémère** : tout ce qui est écrit sur disque (dossiers de
  travail temporaires par session, cf. `tempfile.mkdtemp()` dans `app.py`)
  disparaît à chaque redémarrage/veille — c'est voulu, rien dans l'app ne
  compte sur une écriture qui persisterait d'une session à l'autre ou
  entre deux utilisateurs. Ces dossiers temporaires ne sont cependant
  jamais explicitement nettoyés en cours de vie du conteneur : sur une
  instance qui reste éveillée longtemps avec beaucoup de sessions, ils
  peuvent s'accumuler sur le disque local (impact RAM si `/tmp` est en
  tmpfs) — sans conséquence pratique vu la fréquence de mise en veille du
  palier gratuit, mais à garder en tête si l'app migre un jour vers un
  palier payant "always-on".

### Tester l'image en local avant de déployer

```
docker build -t plan-validation-app .
docker run --rm -p 8501:8501 -e APP_PASSWORD=test plan-validation-app
```

Puis ouvrir http://localhost:8501 — l'écran de connexion doit apparaître
avant tout accès au pipeline.
