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


def auto_masks(words_data: dict, page_w, page_h):
    """Deux rectangles : tableau specs et cartouche, élargis jusqu'aux bords
    droit/bas (zones hors-3D par construction de la mise en page fabricant)."""
    words = words_data["words"]
    masks = []
    tb = cluster_bbox(words, TABLE_KW)
    if tb:
        masks.append([tb[0] - 6, 0, page_w, tb[3] + 10])
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
    # Palette FIXE (jamais adaptative) plutôt que RGB plein, même choix que
    # les planches rédigées — cf. le commentaire détaillé dans
    # core/extract.py::_sauver_png_optimise : la palette adaptative coûte
    # ~100 Mo de RAM en plus par image à cette résolution (histogramme
    # complet), annulant l'optimisation mémoire déjà faite par ailleurs.
    crop.convert("P").save(out, optimize=True)
    return {
        "out": str(out),
        "bbox_pt": [round(v / s, 1) for v in bb],
        "crop_px": list(crop.size),
        "width_pt": round((x1 - x0) / s, 1),
        "height_pt": round((y1 - y0) / s, 1),
        "ratio": round(crop.size[0] / crop.size[1], 3),
    }
