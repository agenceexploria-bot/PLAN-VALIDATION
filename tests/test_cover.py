# -*- coding: utf-8 -*-
"""
Extraction de la vue 3D fournisseur (core/cover.py) : elle doit apparaître
COMPLÈTE, telle que donnée par le fabricant. Seule exception assumée, le
tableau de caractéristiques anglais — il est reproduit en français juste à
côté sur la slide specs.

Régression réelle couverte ici (vue sur Render, plan DHYA2) : le masque du
tableau était une bande partant du HAUT de la page et filant jusqu'au bord
droit, sur l'hypothèse « zone hors-3D par construction ». Faux sur un plan
réel : la moitié droite du cercle de détail A, son libellé « DETAY A /
ÖLÇEK 1:10 » et les traits du cadre partagent cette colonne et étaient
effacés avec le tableau.
"""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from core import cover, extract

FIXTURE_PDF = Path(__file__).resolve().parent / "fixtures" / "DHYA2_test.pdf"
pytestmark = pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="fixture PDF absente")

DPI = 300
ECHELLE = DPI / 72.0
# Page vue 3D + specs de la fixture (1-based).
PAGE_SPECS = 1
# Libellé du détail A, mesuré sur la fixture : dans la colonne du tableau
# mais AU-DESSUS de lui — c'est précisément ce que l'ancien masque effaçait.
LIBELLE_DETAIL_A = [863.9, 232.6, 917.6, 250.7]
# Emprise du tableau anglais MESURÉE sur la fixture (traits de cellules
# compris : bord gauche 852,5 pt, haut 348,0, bas 799,2 ; à droite il est
# collé au trait du cadre, à 1180,6). Volontairement indépendante de ce que
# calcule `auto_masks` : c'est la référence CONTRE laquelle on le juge, sinon
# le test validerait le masque par lui-même quoi qu'il efface.
RECT_TABLEAU_TOLERE = [848.0, 343.0, 1181.0, 805.0]


@pytest.fixture(scope="module")
def page_specs(tmp_path_factory):
    """Rendu 300 dpi de la page vue 3D + specs, et ses mots."""
    import pymupdf

    out = tmp_path_factory.mktemp("cover")
    with pymupdf.open(FIXTURE_PDF) as doc:
        words_data = extract.extraire_page(doc, PAGE_SPECS - 1, out, dpi=DPI, rediger=False)
    return out / f"page_{PAGE_SPECS}_redacted.png", words_data


def _masques(page_specs):
    chemin, words_data = page_specs
    with Image.open(chemin) as im:
        largeur, hauteur = im.size
    return cover.auto_masks(words_data, largeur / ECHELLE, hauteur / ECHELLE)


def test_rien_n_est_efface_hors_du_rectangle_du_tableau(page_specs):
    """Le cœur de l'exigence produit : hors du rectangle du tableau (marge
    comprise), l'image masquée doit être identique au rendu source — pas un
    pixel de la 3D fournisseur ne doit disparaître."""
    chemin, _ = page_specs
    source = Image.open(chemin).convert("RGB")
    masquee = source.copy()
    dessin = ImageDraw.Draw(masquee)
    masques = _masques(page_specs)
    for x0, y0, x1, y1 in masques:
        dessin.rectangle(
            [int(x0 * ECHELLE), int(y0 * ECHELLE), int(x1 * ECHELLE), int(y1 * ECHELLE)],
            fill="white")

    avant = np.asarray(source.convert("L"), dtype=np.int16)
    apres = np.asarray(masquee.convert("L"), dtype=np.int16)
    modifies = np.abs(avant - apres) > 8

    tx0, ty0, tx1, ty1 = RECT_TABLEAU_TOLERE
    tableau = np.zeros_like(modifies)
    tableau[int(ty0 * ECHELLE):int(ty1 * ECHELLE) + 1,
            int(tx0 * ECHELLE):int(tx1 * ECHELLE) + 1] = True

    debordement = (modifies & ~tableau).sum()
    assert debordement == 0, (
        f"{debordement} px effacés HORS de l'emprise du tableau "
        f"{RECT_TABLEAU_TOLERE} — le masque mord sur la vue 3D fournisseur")


def test_le_masque_ne_part_plus_du_haut_de_la_page(page_specs):
    """La bande pleine hauteur est la cause racine : le masque doit commencer
    au tableau, pas à y=0."""
    masque = _masques(page_specs)[0]
    _, y0, _, _ = masque
    assert y0 > 300, f"le masque part encore du haut de la page (y0={y0})"


def test_le_libelle_du_detail_A_survit_et_reste_dans_la_vue_extraite(page_specs, tmp_path):
    """Le libellé « DETAY A / ÖLÇEK 1:10 » est dans la colonne du tableau mais
    au-dessus de lui : ni masqué, ni laissé hors du recadrage."""
    chemin, words_data = page_specs
    x0, y0, x1, y1 = LIBELLE_DETAIL_A
    for mx0, my0, mx1, my1 in _masques(page_specs):
        chevauche = not (x1 < mx0 or x0 > mx1 or y1 < my0 or y0 > my1)
        assert not chevauche, f"le libellé du détail A est recouvert par le masque {[mx0, my0, mx1, my1]}"

    info = cover.extraire_vue_3d(chemin, tmp_path / "vue3d.png",
                                 words_data=words_data, dpi=DPI)
    bx0, by0, bx1, by1 = info["bbox_pt"]
    assert bx0 <= x0 and bx1 >= x1 and by0 <= y0 and by1 >= y1, (
        f"le libellé du détail A {LIBELLE_DETAIL_A} tombe hors de la vue extraite "
        f"{info['bbox_pt']}")


def test_le_tableau_anglais_est_bien_entierement_efface(page_specs):
    """L'autre moitié de l'exigence : le tableau fabricant, lui, doit
    disparaître en entier — traits de cellules compris, pas seulement son
    texte (sa dernière cellule descend nettement sous son dernier mot)."""
    chemin, _ = page_specs
    with Image.open(chemin) as im:
        masquee = im.convert("RGB")
        dessin = ImageDraw.Draw(masquee)
        masques = _masques(page_specs)
        for x0, y0, x1, y1 in masques:
            dessin.rectangle(
                [int(x0 * ECHELLE), int(y0 * ECHELLE), int(x1 * ECHELLE), int(y1 * ECHELLE)],
                fill="white")
        encre = np.asarray(masquee.convert("L")) < 200

    mx0, my0, mx1, my1 = masques[0]
    reste = encre[int(my0 * ECHELLE):int(my1 * ECHELLE) + 1,
                  int(mx0 * ECHELLE):int(mx1 * ECHELLE) + 1]
    assert reste.sum() == 0, f"{reste.sum()} px du tableau anglais subsistent"


def test_page_sans_tableau_ni_cartouche_n_est_pas_masquee():
    """Cas « page specs sans vue 3D reconnaissable » : aucun mot-clé trouvé,
    donc aucun masque — le comportement ne doit pas changer (une page sans
    tableau ne doit pas être blanchie par accident)."""
    words_data = {"page_size_pts": [1190.6, 841.9],
                  "words": [{"text": "SCHEMA", "bbox": [10, 10, 60, 24]}]}
    assert cover.auto_masks(words_data, 1190.6, 841.9) == []
