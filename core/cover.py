# -*- coding: utf-8 -*-
"""
core/cover.py — Extraction de la vue 3D fournisseur (page 3D dédiée), SANS
jamais rogner le dessin. Adapté de scripts/extract_cover.py du skill
`plan-validation-vertical` (logique inchangée).

Piège évité (cf. SKILL.md « 3D tronquée à l'extraction ») : le plateau bas
d'une structure 3D descend souvent bien plus bas que le tableau specs et
s'étend à côté du cartouche. On masque donc d'abord les éléments NON-3D
(tableau specs + cartouche fournisseur) en blanc, PUIS on détecte l'emprise
réelle et complète de la 3D restante, qu'on recadre avec une petite marge.
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw

# Mots-clés des éléments NON-3D (à masquer). Tolérant à la casse/ponctuation.
TABLE_KW = {
    "OFFER", "NO", "MODEL", "PLATFORM", "SIZE", "PIT", "CLOSE", "HEIGHT", "FFL",
    "STOPS", "STROKE", "SHAFT", "RAMP", "CAPACITY", "LIFT", "SPEED", "TOP",
    "POWER", "PACK", "COLOUR", "COLOR", "ANTI", "SLIP", "TEAR", "METAL", "OUTSIDE",
}
CARTOUCHE_KW = {
    "DRAFT'S", "DRAFTS", "MAN", "APPROVAL", "SCALE", "PART", "NAME", "DATE",
    "REVISION", "NUMBER",
}


def _clean(w):
    return w.strip(".,;:()").upper()


def cluster_bbox(words, keywords):
    """Bbox (pts) englobant les mots dont le texte nettoyé est dans keywords."""
    xs0, ys0, xs1, ys1 = [], [], [], []
    for w in words:
        if _clean(w["text"]) in keywords:
            x0, y0, x1, y1 = w["bbox"]
            xs0.append(x0); ys0.append(y0); xs1.append(x1); ys1.append(y1)
    if not xs0:
        return None
    return [min(xs0), min(ys0), max(xs1), max(ys1)]


# Marges autour des mots du tableau specs fabricant, mesurées sur un plan
# réel (DHYA2, page vue 3D + specs) :
#   - gauche/haut/droite : les traits du tableau serrent ses mots (0,4 pt à
#     gauche, 2,7 pt au-dessus) ; 3 pt suffisent à les couvrir ;
#   - droite : PLAFONNÉE par le trait du cadre, contre lequel le tableau est
#     collé (mots jusqu'à 1176,7 pt, cadre à 1180,6 pt). Au-delà, on
#     interromprait le cadre sur toute la hauteur du tableau ;
#   - bas : la dernière cellule descend nettement sous son texte (10,3 pt
#     mesurés) — une marge serrée y laisserait le trait du tableau visible.
MARGE_TABLEAU_PT = 3
MARGE_BAS_TABLEAU_PT = 14


def bbox_tableau(words, tb):
    """Rectangle réel du tableau specs fabricant : la bbox des MOTS-CLÉS
    (colonne des libellés) étendue aux VALEURS de ses lignes, qui ne
    contiennent aucun mot-clé (« EX.26.396R1 », « 2000X1500 MM », « RAL
    7016 »...) et débordent donc largement à droite de `tb`."""
    x0, y0, x1, y1 = tb
    for w in words:
        bx0, by0, bx1, by1 = w["bbox"]
        if bx0 >= x0 - 2 and y0 <= (by0 + by1) / 2 <= y1:
            x1 = max(x1, bx1)
    return [x0, y0, x1, y1]


def auto_masks(words_data: dict, page_w, page_h):
    """Deux rectangles à masquer : le tableau specs fabricant et le cartouche.

    Le tableau est masqué sur son EMPRISE PROPRE (bbox de ses mots + marge
    minimale). Il l'était auparavant par une bande partant du haut de la page
    et filant jusqu'au bord droit — « zone hors-3D par construction », ce qui
    s'est révélé faux sur un plan réel : tout ce qui partageait cette colonne
    au-dessus du tableau était effacé avec lui (moitié droite du cercle de
    détail A, son libellé « DETAY A / ÖLÇEK 1:10 », haut et bord droit du
    cadre). La vue 3D fournisseur doit apparaître COMPLÈTE : le tableau
    anglais est la seule chose retirée, parce qu'il est reproduit en français
    à côté sur la slide."""
    words = words_data["words"]
    masks = []
    tb = cluster_bbox(words, TABLE_KW)
    if tb:
        x0, y0, x1, y1 = bbox_tableau(words, tb)
        masks.append([
            max(0, x0 - MARGE_TABLEAU_PT),
            max(0, y0 - MARGE_TABLEAU_PT),
            min(page_w, x1 + MARGE_TABLEAU_PT),
            min(page_h, y1 + MARGE_BAS_TABLEAU_PT),
        ])
    ca = cluster_bbox(words, CARTOUCHE_KW)
    if ca:
        masks.append([ca[0] - 6, ca[1] - 6, page_w, page_h])
    return masks


def extraire_vue_3d(image_path: Path, out: Path, words_data: dict = None,
                     masks_pt=None, dpi: int = 300, pad: int = 14) -> dict:
    """Recadre la vue 3D fournisseur sans la rogner. `image_path` est
    page_N_redacted.png (déjà nettoyée du texte). `words_data` (optionnel)
    permet le masquage automatique du tableau specs + cartouche fabricant via
    `auto_masks`. `masks_pt` permet un contrôle manuel (liste de [x0,y0,x1,y1]
    en points PDF), en complément ou à la place.

    Retourne un dict {out, bbox_pt, crop_px, width_pt, height_pt, ratio} —
    `width_pt` sert de `specs.image_page_w_pt` pour positionner les callouts.
    Lève ValueError si plus aucun contenu ne subsiste après masquage.
    """
    im = Image.open(image_path).convert("RGB")
    W, H = im.size
    s = dpi / 72.0
    page_w, page_h = W / s, H / s

    masks_pt = list(masks_pt or [])
    if words_data:
        masks_pt += auto_masks(words_data, page_w, page_h)

    d = ImageDraw.Draw(im)
    for (x0, y0, x1, y1) in masks_pt:
        d.rectangle([int(x0 * s), int(y0 * s), int(x1 * s), int(y1 * s)], fill="white")

    bw = im.convert("L").point(lambda p: 255 if p < 225 else 0)
    bb = bw.getbbox()
    if bb is None:
        raise ValueError(
            "Plus aucun contenu après masquage (masques trop larges ?) — "
            "impossible d'extraire la vue 3D."
        )
    x0 = max(0, bb[0] - pad); y0 = max(0, bb[1] - pad)
    x1 = min(W, bb[2] + pad); y1 = min(H, bb[3] + pad)
    crop = im.crop((x0, y0, x1, y1))
    # Palette FIXE (jamais adaptative : elle analyse l'histogramme complet,
    # ~100 Mo de RAM en plus). Réservé à ce recadrage final (bien plus petit que
    # la page) ; les rendus pleine page, eux, restent en PNG direct (cf.
    # core/extract.py::extraire_page).
    crop.convert("P").save(out, optimize=True)
    return {
        "out": str(out),
        "bbox_pt": [round(v / s, 1) for v in bb],
        "crop_px": list(crop.size),
        "width_pt": round((x1 - x0) / s, 1),
        "height_pt": round((y1 - y0) / s, 1),
        "ratio": round(crop.size[0] / crop.size[1], 3),
    }
