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
                    labels.append({
                        "text": res["traduction"], "bbox": w["suffix_bbox"],
                        "vertical": w["rotation_deg"] in (90, 270),
                        "source": res["source"], "statut": res["statut"],
                    })
                    if res["statut"] == "hors_glossaire":
                        hors_glossaire.append(res)
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
                    labels.append({
                        "text": t_gen,
                        "bbox": _bbox_englobante([g["bbox"] for g in groupe]),
                        "vertical": groupe[0]["rotation_deg"] in (90, 270),
                        "source": phrase,
                        "statut": "glossaire_cartouche" if t_cart else "glossaire",
                    })
                    i += taille
                    trouve = True
                    break
            if trouve:
                continue

            res = traduire_mot(w["text"], secours)
            labels.append({
                "text": res["traduction"], "bbox": w["bbox"],
                "vertical": w["rotation_deg"] in (90, 270),
                "source": res["source"], "statut": res["statut"],
            })
            if res["statut"] == "hors_glossaire":
                hors_glossaire.append(res)
            i += 1

    return labels, hors_glossaire


def extraire_tableau_specs(words_data: dict, secours=None):
    """Reconstruit le tableau specs FR (étape 3a) directement à partir d'une
    page « alimente la page specs » du plan fabricant, sans relevé manuel :
    chaque LIGNE du PDF est traitée comme une ligne de tableau (OFFER NO /
    MODEL / PLATFORM SIZE / CAPACITY...). Le libellé est le plus long préfixe
    de la ligne qui correspond à une entrée du glossaire (priorité
    cartouche/specs) ; la valeur est le reste de la ligne, jamais traduite
    (nombres et codes). Une ligne sans libellé reconnu est incluse quand même
    (premier mot traduit via `secours`, statut "hors_glossaire") plutôt que
    silencieusement ignorée — sauf si elle ne contient aucune valeur (bandeau
    décoratif, titre de section).

    Retourne (table_fr, termes_hors_glossaire) où table_fr = liste de
    tuples (libellé_fr, valeur).
    """
    words = words_data["words"]
    table_fr = []
    hors_glossaire = []

    for indices in _construire_lignes(words):
        n = len(indices)
        if n == 0:
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

        valeur = " ".join(words[indices[k]]["text"] for k in range(label_taille, n)).strip()
        if not valeur:
            continue  # libellé seul sans valeur : pas une ligne de tableau (titre, bandeau)

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
