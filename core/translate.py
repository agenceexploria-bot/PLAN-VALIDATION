# -*- coding: utf-8 -*-
"""
core/translate.py — Étape 3 du pipeline : traduction FR via le glossaire
Vertical (dictionnaire d'abord, secours en dernier recours).

Limite connue (héritée du skill original, qui la compensait par la vision
d'un agent) : `core.extract.is_translatable` ne détecte pas la LANGUE, juste
« contient des lettres et n'est pas un code invariant ». Un mot déjà en
français peut donc apparaître ici comme "hors glossaire" (proposition =
identique au source). Ce n'est pas une erreur de traduction — le texte n'est
pas altéré — mais du bruit dans la liste à valider ; l'utilisateur le
reconnaît en un coup d'œil dans l'écran de vérification (traduction ==
source).
"""
from data import glossaire


def traduire_mot(mot: str, secours=None) -> dict:
    """Traduit un terme unique. Retourne
    {"source", "traduction", "statut"} avec statut dans :
      - "invariant"          : valeur numérique/code, jamais traduit
      - "glossaire_cartouche": trouvé dans le lexique validé cartouche/specs
      - "glossaire"          : trouvé dans le glossaire général
      - "hors_glossaire"     : absent du glossaire — proposition de secours,
                                à valider explicitement par l'utilisateur
    """
    if glossaire.est_invariant(mot):
        return {"source": mot, "traduction": mot, "statut": "invariant"}
    t = glossaire.traduire_cartouche_specs(mot)
    if t is not None:
        return {"source": mot, "traduction": t, "statut": "glossaire_cartouche"}
    t = glossaire.traduire_terme(mot)
    if t is not None:
        return {"source": mot, "traduction": t, "statut": "glossaire"}
    proposition = secours(mot) if secours else mot
    return {"source": mot, "traduction": proposition, "statut": "hors_glossaire"}


def traduire_suffixe(suffixe_en: str, secours=None) -> dict:
    """Traduit le suffixe d'une cote fusionnée, ex. '(FFL)' -> '(FFL)',
    '(PIT)' -> '(fosse)'. Le nombre associé n'est jamais concerné ici (il
    reste dans l'image, voir core/extract.py)."""
    t = glossaire.traduire_suffixe_cote(suffixe_en)
    if t is not None:
        return {"source": suffixe_en, "traduction": t, "statut": "glossaire_cartouche"}
    proposition = secours(suffixe_en) if secours else suffixe_en
    return {"source": suffixe_en, "traduction": proposition, "statut": "hors_glossaire"}


# Le plus long terme du glossaire fait 6 mots (ex. suffixe cartouche/specs
# du type "TOP PLATFORM: ANTI SLIP TEAR METAL"). Essayer plus long ne coûte
# rien (lignes de plan très courtes).
MAX_NGRAM = 6


def _identique(traduction: str, source: str) -> bool:
    """Traduction identique à la source (casse et parenthèses ignorées) : elle
    n'apporte rien à l'écran. Ré-écrire le mot par-dessus lui-même détruirait
    l'original pour rien (B1)."""
    return traduction.strip("() ").casefold() == source.strip("() ").casefold()


def _bbox_englobante(bboxes):
    xs0 = [b[0] for b in bboxes]; ys0 = [b[1] for b in bboxes]
    xs1 = [b[2] for b in bboxes]; ys1 = [b[3] for b in bboxes]
    return [min(xs0), min(ys0), max(xs1), max(ys1)]


def _construire_lignes(words):
    """Regroupe les indices de `words` par (block_no, line_no), triés par
    word_no — reconstitue l'ordre de lecture d'une ligne du PDF."""
    lignes = {}
    for i, w in enumerate(words):
        lignes.setdefault((w["block_no"], w["line_no"]), []).append(i)
    for cle in lignes:
        lignes[cle].sort(key=lambda i: words[i]["word_no"])
    return [lignes[cle] for cle in sorted(lignes)]


def construire_planche(n: int, words_data: dict):
    """Entrée `planches[]` de `assemble.assembler` pour la page `n` : image
    rédigée, étiquettes FR et LARGEUR RÉELLE de la page source en points PDF
    (`page_w_pt`). Les bbox des étiquettes sont dans le repère de cette page :
    sans sa largeur, `assemble.add_overlay` retomberait sur la constante A3 et
    positionnerait mal les étiquettes sur tout autre format. Retourne
    (planche, termes_hors_glossaire)."""
    labels, hors_glossaire = traduire_labels_planche(words_data)
    planche = {
        "image": f"page_{n}_redacted.png", "page_n": n, "labels": labels,
        "page_w_pt": words_data["page_size_pts"][0],
    }
    return planche, hors_glossaire


def traduire_labels_planche(words_data: dict, secours=None):
    """Construit les étiquettes FR à superposer sur une planche dessin
    (étape 3c), à partir de page_N_words.json (core.extract.extraire_page).

    Le glossaire contient des EXPRESSIONS de plusieurs mots (ex. "with
    operator on board", "serbest ölçü toleransları") : une correspondance
    mot à mot les raterait systématiquement. On reconstitue donc d'abord les
    lignes du PDF (block_no/line_no/word_no fournis par pymupdf), puis on
    essaie sur chaque ligne les n-grammes de mots contigus du plus long au
    plus court contre le glossaire, avant de retomber sur une traduction mot
    à mot pour ce qui ne correspond à aucune expression connue. Une cote
    fusionnée à un suffixe (ex. "9700(FFL)") reste toujours traitée à part :
    bbox = suffix_bbox UNIQUEMENT, le nombre restant visible dans l'image
    (règle absolue n°2). La rotation (`vertical`) est déduite de
    `rotation_deg` (0/90/270), calculée en amont depuis la direction
    d'écriture du PDF — jamais estimée à l'œil.

    Règle B1 — un mot traduit = un effacement + une pose : ne sortent en
    `labels` QUE les mots réellement remplacés par un texte différent, et ce
    sont exactement les zones que `core.extract` rédige dans l'image (même
    fonction, mêmes bbox). Un mot hors glossaire, ou dont la traduction est
    identique à la source, n'est NI rédigé NI réétiqueté : il reste tel quel
    dans l'image ; le hors-glossaire est seulement signalé au rapport.

    Retourne (labels, termes_hors_glossaire).
    """
    words = words_data["words"]
    labels = []
    hors_glossaire = []

    for indices in _construire_lignes(words):
        i, n = 0, len(indices)
        while i < n:
            w = words[indices[i]]

            if w["num"]:
                if w["suffix_bbox"]:
                    res = traduire_suffixe(w["suffix_en"], secours)
                    if res["statut"] == "hors_glossaire":
                        hors_glossaire.append(res)
                    elif not _identique(res["traduction"], res["source"]):
                        labels.append({
                            "text": res["traduction"], "bbox": w["suffix_bbox"],
                            "vertical": w["rotation_deg"] in (90, 270),
                            "rotation_deg": w["rotation_deg"],
                            # Le suffixe vient APRÈS le nombre : l'étiquette part
                            # du début du suffixe et s'éloigne du nombre (le FR,
                            # souvent plus long, ne doit pas le recouvrir).
                            "ancre": "debut",
                            "source": res["source"], "statut": res["statut"],
                        })
                i += 1
                continue

            if not w["translatable"]:
                i += 1
                continue

            # Essaie les n-grammes du plus long au plus court : un nombre ou
            # un mot non traduisible dans le groupe casse la phrase (jamais
            # fusionné avec l'expression voisine).
            trouve = False
            portee_max = min(MAX_NGRAM, n - i)
            for taille in range(portee_max, 1, -1):
                groupe = [words[indices[i + k]] for k in range(taille)]
                if any(g["num"] or not g["translatable"] for g in groupe):
                    continue
                phrase = " ".join(g["text"] for g in groupe)
                if glossaire.est_invariant(phrase):
                    continue
                t_cart = glossaire.traduire_cartouche_specs(phrase)
                t_gen = t_cart or glossaire.traduire_terme(phrase)
                if t_gen is not None:
                    if not _identique(t_gen, phrase):
                        labels.append({
                            "text": t_gen,
                            "bbox": _bbox_englobante([g["bbox"] for g in groupe]),
                            "vertical": groupe[0]["rotation_deg"] in (90, 270),
                            "rotation_deg": groupe[0]["rotation_deg"],
                            "fit_bbox": groupe[0]["rotation_deg"] in (90, 270),
                            "source": phrase,
                            "statut": "glossaire_cartouche" if t_cart else "glossaire",
                        })
                    i += taille
                    trouve = True
                    break
            if trouve:
                continue

            res = traduire_mot(w["text"], secours)
            if res["statut"] == "hors_glossaire":
                hors_glossaire.append(res)
            elif not _identique(res["traduction"], res["source"]):
                labels.append({
                    "text": res["traduction"], "bbox": w["bbox"],
                    "vertical": w["rotation_deg"] in (90, 270),
                    "rotation_deg": w["rotation_deg"],
                    "fit_bbox": w["rotation_deg"] in (90, 270),
                    "source": res["source"], "statut": res["statut"],
                })
            i += 1

    return labels, hors_glossaire


def traduire_valeur(mots, secours=None):
    """Traduit la VALEUR d'une ligne du tableau specs (`mots` = mots de la
    valeur, dans l'ordre de lecture) par le glossaire : expressions de plusieurs
    mots d'abord (« ANTHRACITE GREY » -> « gris anthracite »), puis mot à mot.
    Nombres, unités, codes et désignations (tout mot non traduisible) sont
    recopiés TELS QUELS. Un mot inconnu reste inchangé (rien n'est inventé) et
    est signalé en hors-glossaire. Retourne (texte, termes_hors_glossaire)."""
    sortie, hors_glossaire = [], []
    i, n = 0, len(mots)
    while i < n:
        w = mots[i]
        if w["num"] or not w["translatable"]:
            sortie.append(w["text"])
            i += 1
            continue
        for taille in range(min(MAX_NGRAM, n - i), 1, -1):
            groupe = mots[i:i + taille]
            if any(g["num"] or not g["translatable"] for g in groupe):
                continue
            phrase = " ".join(g["text"] for g in groupe)
            traduction = glossaire.traduire_cartouche_specs(phrase) or glossaire.traduire_terme(phrase)
            if traduction is not None:
                sortie.append(traduction)
                i += taille
                break
        else:
            res = traduire_mot(w["text"], secours)
            if res["statut"] == "hors_glossaire":
                hors_glossaire.append(res)
                sortie.append(w["text"])
            else:
                sortie.append(res["traduction"])
            i += 1
    return " ".join(sortie), hors_glossaire


def _valeur_sur_la_meme_rangee(words, indices):
    """Mots situés à DROITE de la ligne `indices`, sur la même rangée, dans le
    même bloc du PDF, mais sur une autre ligne (mise en page « libellé : » puis
    valeur alignée à droite). Retourne les mots triés de gauche à droite."""
    ligne = [words[i] for i in indices]
    bloc = ligne[0]["block_no"]
    y0 = min(w["bbox"][1] for w in ligne)
    y1 = max(w["bbox"][3] for w in ligne)
    x_droit = max(w["bbox"][2] for w in ligne)
    propres = set(indices)
    candidats = [
        w for k, w in enumerate(words)
        if k not in propres and w["block_no"] == bloc
        and w["bbox"][0] >= x_droit - 1 and y0 <= (w["bbox"][1] + w["bbox"][3]) / 2 <= y1
    ]
    return sorted(candidats, key=lambda w: w["bbox"][0])


def _ligne_suivante_dans_la_colonne(words, lignes, valeur):
    """Ligne (liste d'indices) qui prolonge `valeur` juste en dessous, dans la
    même colonne, quel que soit son bloc (« ANTI SLIP TEAR » puis « METAL »,
    bloc distinct sur le plan réel). None s'il n'y en a pas."""
    x0 = min(w["bbox"][0] for w in valeur)
    x1 = max(w["bbox"][2] for w in valeur)
    y0 = min(w["bbox"][1] for w in valeur)
    y1 = max(w["bbox"][3] for w in valeur)
    h = y1 - y0
    pris = {id(w) for w in valeur}
    candidates = [
        ind for ind in lignes
        if not any(id(words[i]) in pris for i in ind)
        and all(x0 - h <= words[i]["bbox"][0] and words[i]["bbox"][2] <= x1 + h for i in ind)
        and y0 < min(words[i]["bbox"][1] for i in ind) <= y1 + h
    ]
    return min(candidates, key=lambda ind: min(words[i]["bbox"][1] for i in ind), default=None)


def _libelle_coupe_du_lexique(words, lignes, indices):
    """Expression du lexique cartouche/specs coupée sur plusieurs lignes : un
    libellé « … : », sa valeur alignée à droite sur la même rangée, puis au
    plus une ligne de suite en dessous (ex. « TOP PLATFORM : » / « ANTI SLIP
    TEAR » / « METAL »). Retourne (traduction validée, mots consommés) SEULEMENT
    si l'expression complète figure dans le lexique — rien n'est deviné ;
    None sinon."""
    ligne = [words[i] for i in indices]
    if not ligne[-1]["text"].endswith(":"):
        return None
    valeur = _valeur_sur_la_meme_rangee(words, indices)
    if not valeur:
        return None
    libelle = " ".join(w["text"] for w in ligne).rstrip(": ")
    suite = _ligne_suivante_dans_la_colonne(words, lignes, valeur)
    for mots_valeur in (valeur, valeur + ([words[i] for i in suite] if suite else [])):
        phrase = f"{libelle}: " + " ".join(w["text"] for w in mots_valeur)
        traduction = glossaire.traduire_cartouche_specs(phrase)
        if traduction is not None:
            return traduction, ligne + mots_valeur
    return None


def extraire_tableau_specs(words_data: dict, secours=None):
    """Reconstruit le tableau specs FR (étape 3a) directement à partir d'une
    page « alimente la page specs » du plan fabricant, sans relevé manuel :
    chaque LIGNE du PDF est traitée comme une ligne de tableau (OFFER NO /
    MODEL / PLATFORM SIZE / CAPACITY...). Le libellé est le plus long préfixe
    de la ligne qui correspond à une entrée du glossaire (priorité
    cartouche/specs) ; la valeur est le reste de la ligne, traduite par le
    glossaire (`traduire_valeur`) sauf nombres, unités et codes, recopiés tels
    quels. Un libellé qui se termine par « : » est rattaché à la valeur alignée
    à droite sur la même rangée du même bloc. Une ligne sans libellé reconnu est incluse quand même
    (premier mot traduit via `secours`, statut "hors_glossaire") plutôt que
    silencieusement ignorée — sauf si elle ne contient aucune valeur (bandeau
    décoratif, titre de section). Une expression du lexique coupée sur
    plusieurs lignes (« TOP PLATFORM : ANTI SLIP TEAR METAL », cf.
    `_libelle_coupe_du_lexique`) donne UNE ligne : sa traduction validée en
    libellé, valeur vide.

    Retourne (table_fr, termes_hors_glossaire) où table_fr = liste de
    tuples (libellé_fr, valeur).
    """
    words = words_data["words"]
    table_fr = []
    hors_glossaire = []

    lignes = _construire_lignes(words)
    # Premier passage : les expressions coupées, pour que leurs lignes de
    # valeur ne ressortent pas en lignes de tableau absurdes (« ANTI -> SLIP TEAR »).
    fusions, consommes = {}, set()
    for k, indices in enumerate(lignes):
        fusion = _libelle_coupe_du_lexique(words, lignes, indices)
        if fusion is not None:
            fusions[k] = fusion[0]
            consommes |= {id(w) for w in fusion[1]}

    for k, indices in enumerate(lignes):
        n = len(indices)
        if k in fusions:
            table_fr.append((fusions[k], ""))
            continue
        if n == 0 or all(id(words[i]) in consommes for i in indices):
            continue

        label_taille, label_res = 0, None
        portee_max = min(MAX_NGRAM, n)
        for taille in range(portee_max, 0, -1):
            groupe = [words[indices[k]] for k in range(taille)]
            if any(g["num"] or not g["translatable"] for g in groupe):
                continue
            phrase = " ".join(g["text"] for g in groupe)
            t_cart = glossaire.traduire_cartouche_specs(phrase)
            t_gen = t_cart or glossaire.traduire_terme(phrase)
            if t_gen is not None:
                label_res = {"source": phrase, "traduction": t_gen,
                             "statut": "glossaire_cartouche" if t_cart else "glossaire"}
                label_taille = taille
                break

        if label_res is None:
            premier = words[indices[0]]
            if not premier["translatable"]:
                continue  # ligne qui commence par un nombre : pas une ligne "libellé: valeur"
            label_res = traduire_mot(premier["text"], secours)
            label_taille = 1

        mots_valeur = [words[indices[k]] for k in range(label_taille, n)]
        if [m["text"] for m in mots_valeur] == [":"]:
            # « POWER PACK : » puis la valeur alignée à droite sur une autre ligne
            # du même bloc : on la rattache (sinon la valeur est perdue).
            mots_valeur = _valeur_sur_la_meme_rangee(words, indices) or mots_valeur
        if not mots_valeur:
            continue  # libellé seul sans valeur : pas une ligne de tableau (titre, bandeau)
        valeur, hg_valeur = traduire_valeur(mots_valeur, secours)
        hors_glossaire += hg_valeur

        table_fr.append((label_res["traduction"], valeur))
        if label_res["statut"] == "hors_glossaire":
            hors_glossaire.append(label_res)

    return table_fr, hors_glossaire


def traduire_tableau_specs(lignes, secours=None):
    """`lignes` = liste de tuples (libellé_fabricant, valeur) relevés du
    tableau specs fabricant (OFFER NO, MODEL, PLATFORM SIZE...). Reconstruit
    le tableau FR : même lignes/valeurs, libellé traduit, VALEUR JAMAIS
    MODIFIÉE (invariant contractuel). Retourne (table_fr, termes_hors_glossaire)
    où table_fr = liste de tuples (libellé_fr, valeur)."""
    table_fr = []
    hors_glossaire = []
    for libelle, valeur in lignes:
        res = traduire_mot(libelle, secours)
        table_fr.append((res["traduction"], valeur))
        if res["statut"] == "hors_glossaire":
            hors_glossaire.append(res)
    return table_fr, hors_glossaire


# Rubriques commerciales qui ne figurent JAMAIS sur le plan fabricant (elles
# viennent de l'affaire commerciale, pas du dessin — cf. SKILL.md). On les
# ajoute systématiquement au tableau specs si absentes, avec la mention
# imposée, plutôt que de laisser une rubrique silencieusement manquante.
CHAMP_A_CONFIRMER = "à confirmer (service commercial)"
RUBRIQUES_COMMERCIALES = [
    "Portes palières",
    "Tension de commande",
    "Cycles de fonctionnement",
    "Prérequis chantier",
]


def completer_champs_commerciaux(table_fr):
    """Ajoute au tableau specs (liste de tuples (libellé, valeur)) les
    rubriques commerciales absentes, marquées `CHAMP_A_CONFIRMER`. Retourne
    (table_fr_complete, rubriques_ajoutees) — `rubriques_ajoutees` doit être
    listé dans le rapport de vérification (jamais silencieux)."""
    labels_presents = {lab.strip().lower() for lab, _ in table_fr}
    table_fr = list(table_fr)
    ajouts = []
    for rubrique in RUBRIQUES_COMMERCIALES:
        if rubrique.strip().lower() not in labels_presents:
            table_fr.append((rubrique, CHAMP_A_CONFIRMER))
            ajouts.append(rubrique)
    return table_fr, ajouts


def traduire_callouts(callouts, secours=None):
    """`callouts` = liste de dicts {"text": ..., "bbox": [...], ...} relevés
    sur la vue 3D fournisseur (page 3D dédiée). Traduit `text` en conservant
    tous les autres champs (bbox, leader_to, box_w_cm...) inchangés.
    Retourne (callouts_fr, termes_hors_glossaire)."""
    callouts_fr = []
    hors_glossaire = []
    for c in callouts:
        res = traduire_mot(c["text"], secours)
        callouts_fr.append({**c, "text": res["traduction"]})
        if res["statut"] == "hors_glossaire":
            hors_glossaire.append(res)
    return callouts_fr, hors_glossaire
