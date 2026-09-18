# -*- coding: utf-8 -*-
"""
core/verify.py — Étape 5 du pipeline : portail de vérification, obligatoire
avant tout export PDF (cf. references/verification.md du skill). Adapté de
scripts/verify_dims.py, complété par les contrôles de complétude.

Ce module ne décide rien : il liste des écarts et des points à confirmer.
L'arbitrage revient toujours à l'utilisateur (cf. SKILL.md : « pas
d'exception, même si tous les contrôles sont PASS »). Le contrôle VISUEL
(rendu réel) est traité séparément par core/render.py + une validation
humaine explicite dans l'UI Streamlit — ce module ne fait que les contrôles
programmatiques (cotes + complétude).
"""
import re
from collections import Counter
from pathlib import Path

NUM = re.compile(r"\d+(?:[.,]\d+)?")
# Nombres "bruit" ignorés par défaut (années, pages, téléphone, codes postaux)
NOISE = re.compile(r"^(19|20)\d{2}$|^\d{1,2}$")

# Le numéro d'affaire (LDxxxxx) est TOUJOURS une valeur du dossier, jamais une
# cote fabricant : sans ce nettoyage, ses chiffres (ex. "004" dans "LDTEST004")
# remonteraient à tort comme une valeur "inventée" — trouvé en testant l'app
# de bout en bout (rapport de vérification sur un cas réel).
NUMERO_AFFAIRE = re.compile(r"(?i)LD\w*\d+")

# Cellules 100 % fixes du gabarit Vertical (adresse, libellés de cartouche) :
# jamais une cote fabricant, à exclure du comptage plutôt que de les laisser
# remonter comme écarts "BLOQUANT" à chaque génération. Trouvé de la même
# façon (adresse "260 rue des Barronnières / 01700 - BEYNOST" remontée comme
# valeur "inventée").
CARTOUCHE_FIXE = re.compile(
    r"(?i)barronni[eè]res|beynost|^\s*(dessinateur|date de cr[ée]ation|"
    r"plan de validation indice|client|page)\s*:"
)


def numbers_in(text: str) -> Counter:
    texte = NUMERO_AFFAIRE.sub("", text)
    vals = Counter()
    for m in NUM.finditer(texte):
        v = m.group().replace(",", ".")
        if not NOISE.match(m.group()):
            vals[v] += 1
    return vals


def nombres_source(words_par_page: dict) -> Counter:
    """`words_par_page` = {page_n: words_data} (core.extract.extraire_pdf)."""
    vals = Counter()
    for words_data in words_par_page.values():
        for w in words_data["words"]:
            vals += numbers_in(w["text"])
    return vals


def nombres_deck(pptx_path: Path) -> Counter:
    """Nombres présents dans le texte du PPTX généré — hors cartouche et
    adresse (100 % fixes, jamais une cote fabricant, cf. `CARTOUCHE_FIXE`)."""
    from pptx import Presentation
    vals = Counter()
    for slide in Presentation(pptx_path).slides:
        for shape in slide.shapes:
            if shape.has_text_frame and not CARTOUCHE_FIXE.search(shape.text_frame.text):
                vals += numbers_in(shape.text_frame.text)
            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        if not CARTOUCHE_FIXE.search(cell.text):
                            vals += numbers_in(cell.text)
    return vals


def controle_cotes(words_par_page: dict, pptx_path: Path, checklist_path: Path = None,
                    pages_scannees: list = None) -> dict:
    """Confronte les nombres extraits du PDF fabricant, ceux du PPTX généré,
    et (optionnel) ceux de la checklist revue d'affaire. Ne décide rien :
    liste les écarts pour arbitrage utilisateur.

    `pages_scannees` (numéros 1-based parmi les pages retenues, cf.
    `core.extract.est_pdf_scanne`) : si non vide, la comparaison source/deck
    est structurellement invalide (aucune cote n'a pu être extraite de ces
    pages) — le statut ne doit alors jamais remonter PASS, même si les deux
    compteurs sont vides par coïncidence (bug audit)."""
    src = nombres_source(words_par_page)
    deck = nombres_deck(pptx_path)
    n_ecarts = len(set(src) - set(deck)) + len(set(deck) - set(src))
    if pages_scannees:
        statut_cotes = "NON VÉRIFIABLE (PDF scanné)"
    elif n_ecarts == 0:
        statut_cotes = "PASS"
    else:
        statut_cotes = f"{n_ecarts} écart(s)"
    rapport = {
        "source_absents_du_deck": sorted(set(src) - set(deck)),
        "deck_absents_de_la_source": sorted(set(deck) - set(src)),
        "checklist": None,
        "pages_scannees": sorted(pages_scannees or []),
        "statut_cotes": statut_cotes,
        "nota": (
            "source_absents_du_deck inclut les cotes restées incrustées dans "
            "les images de planches (normal, elles n'ont pas besoin d'être "
            "dans le texte du deck) : à confirmer visuellement avant de "
            "conclure à une perte. deck_absents_de_la_source = toujours "
            "bloquant : remonter à l'origine de chaque valeur."
        ),
    }
    if checklist_path and checklist_path.exists():
        from . import checklist as checklist_mod

        try:
            lignes = checklist_mod.lire_checklist(checklist_path)
        except ValueError as e:
            rapport["checklist"] = {
                "invalide": True,
                "message": (
                    f"Ce fichier ne semble pas être la checklist revue d'affaire "
                    f"attendue — feuille « Technique » introuvable ({e}). Elle "
                    "n'a pas été utilisée comme source de comparaison ; vous "
                    "pouvez continuer sans checklist."
                ),
            }
        else:
            if not lignes or checklist_mod.est_vierge(lignes):
                rapport["checklist"] = {
                    "vierge": True,
                    "message": (
                        "Feuille « Technique » de la checklist trouvée mais vide "
                        "(ou non renseignée) : elle n'a PAS été utilisée comme "
                        "source de comparaison (cf. SKILL.md — souvent le modèle "
                        "vierge)."
                    ),
                }
            else:
                cl_nums = Counter()
                for cols in lignes.values():
                    for val in cols.values():
                        cl_nums += numbers_in(str(val))
                rapport["checklist"] = {
                    "vierge": False,
                    "valeurs_checklist_absentes_du_dossier": sorted(
                        set(cl_nums) - set(src) - set(deck)
                    ),
                    "lignes_technique": lignes,
                }
    return rapport


# ---------------------------------------------------------------------------
# Contrôle de complétude
# ---------------------------------------------------------------------------

CHAMPS_A_COMPLETER = "à compléter"
SUFFIXES_SOCIETE = ["sarl", " sas", "gmbh", " ltd", " inc", " sa "]


def controle_completude(pptx_path: Path, meta: dict, rubriques_specs: dict = None) -> list:
    """Vérifie, sur le PPTX généré :
      - le cartouche ne contient aucun résidu d'un ancien dossier (client,
        n° d'affaire) sur AUCUNE slide, y compris les cellules masquées ;
      - la page de garde a bien les deux contacts standard Vertical ;
      - les rubriques specs fournies sont soit renseignées, soit marquées
        « à confirmer par le fabricant / service commercial ».
    Retourne la liste des messages à présenter dans le rapport (liste vide =
    aucune alerte)."""
    from pptx import Presentation

    alertes = []
    prs = Presentation(pptx_path)

    client_saisi = str(meta.get("client", "")).strip().lower()
    numero_saisi = str(meta.get("numero", "")).strip().lower()

    for i, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            if not shape.has_table:
                continue
            for row in shape.table.rows:
                for cell in row.cells:
                    texte = cell.text.strip()
                    bas = texte.lower()
                    if bas and any(s in bas for s in SUFFIXES_SOCIETE) and client_saisi not in bas:
                        alertes.append(
                            f"Slide {i + 1} — résidu suspect dans le cartouche : "
                            f"« {texte} » ne correspond pas au client saisi "
                            f"(« {meta.get('client', '')} »)."
                        )

    slide_garde = prs.slides[0]
    texte_garde = " ".join(
        sh.text_frame.text for sh in slide_garde.shapes if sh.has_text_frame
    )
    if numero_saisi and numero_saisi not in texte_garde.lower():
        alertes.append(
            f"Page de garde : le numéro d'affaire saisi (« {meta.get('numero', '')} ») "
            "n'apparaît pas sur la garde."
        )
    if "nadia" not in texte_garde.lower():
        alertes.append("Page de garde : contact Nadia absent (règle : toujours les deux).")
    if "jérémie" not in texte_garde.lower() and "jeremie" not in texte_garde.lower():
        alertes.append("Page de garde : contact Jérémie absent (règle : toujours les deux).")

    if rubriques_specs:
        for rubrique, valeur in rubriques_specs.items():
            if not valeur or str(valeur).strip() == "":
                alertes.append(
                    f"Rubrique specs « {rubrique} » vide — devrait être "
                    f"« {CHAMPS_A_COMPLETER} » ou « à confirmer (service commercial) », "
                    "jamais silencieusement vide."
                )

    return alertes


# ---------------------------------------------------------------------------
# Rapport consolidé (format imposé, cf. references/verification.md)
# ---------------------------------------------------------------------------

def construire_rapport(nom_fichier: str, rapport_cotes: dict, alertes_completude: list,
                        pages_ignorees: list, termes_hors_glossaire: list,
                        visuel_valide: bool) -> str:
    """Assemble le texte du rapport de vérification, au format imposé par
    references/verification.md. Consultable/téléchargeable tel quel."""
    n_ecarts_cotes = (
        len(rapport_cotes["source_absents_du_deck"])
        + len(rapport_cotes["deck_absents_de_la_source"])
    )
    statut_cotes = rapport_cotes.get(
        "statut_cotes", "PASS" if n_ecarts_cotes == 0 else f"{n_ecarts_cotes} écart(s)"
    )
    lignes = [
        f"RAPPORT DE VÉRIFICATION — {nom_fichier}",
        "",
        f"1. Cotes        : {statut_cotes}",
        f"2. Visuel       : {'validé par l’utilisateur' if visuel_valide else 'en attente'}",
        f"3. Complétude   : {'PASS' if not alertes_completude else f'{len(alertes_completude)} manque(s)'}",
        "",
    ]
    if rapport_cotes.get("pages_scannees"):
        pages_str = ", ".join(str(p) for p in rapport_cotes["pages_scannees"])
        lignes.append(
            f"PDF scanné détecté sur la/les page(s) {pages_str} — aucune couche "
            "texte exploitable : le contrôle de cotes ne peut pas être considéré "
            "comme fiable pour ces pages (vérification manuelle indispensable)."
        )
        lignes.append("")
    if n_ecarts_cotes:
        lignes.append("ÉCARTS DE COTES :")
        for v in rapport_cotes["source_absents_du_deck"]:
            lignes.append(f"  - cote source « {v} » absente du deck (à confirmer visuellement — peut être incrustée dans une image de planche)")
        for v in rapport_cotes["deck_absents_de_la_source"]:
            lignes.append(f"  - valeur « {v} » présente dans le deck mais absente de la source (BLOQUANT — remonter à l'origine)")
        lignes.append("")
    if rapport_cotes.get("checklist"):
        cl = rapport_cotes["checklist"]
        if cl.get("invalide") or cl.get("vierge"):
            lignes.append(f"CHECKLIST : {cl['message']}")
        else:
            absentes = cl.get("valeurs_checklist_absentes_du_dossier", [])
            if absentes:
                lignes.append(f"CHECKLIST — valeurs présentes dans la checklist mais absentes du dossier (à arbitrer) : {', '.join(absentes)}")
        lignes.append("")
    if alertes_completude:
        lignes.append("COMPLÉTUDE :")
        for a in alertes_completude:
            lignes.append(f"  - {a}")
        lignes.append("")
    lignes.append(f"Pages fabricant ignorées : {', '.join(str(p) for p in pages_ignorees) if pages_ignorees else 'aucune'}")
    if termes_hors_glossaire:
        vus = {}
        for t in termes_hors_glossaire:
            vus[t["source"]] = t["traduction"]
        termes_str = "; ".join(f"{s} -> {t}" for s, t in vus.items())
        lignes.append(f"Termes hors glossaire (à valider par Marin) : {termes_str}")
    else:
        lignes.append("Termes hors glossaire : aucun")
    lignes.append("")
    lignes.append(
        "Le rapport est-il validé ? Le PDF n'est exporté qu'après votre accord explicite."
    )
    return "\n".join(lignes)
