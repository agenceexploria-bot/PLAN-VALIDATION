# -*- coding: utf-8 -*-
"""
C1 — les VALEURS du tableau specs sont traduites par le glossaire (pas seulement
les libellés) : ANTHRACITE GREY -> gris anthracite, OUTSIDE -> déporté (extérieur).
Les nombres, unités et codes de la valeur ne sont jamais touchés.
"""
import tempfile
from pathlib import Path

import pytest

from core import extract, translate

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "DHYA2_test.pdf"


def _mots(texte, x=0.0, y=0.0, block=0, line=0, largeur_car=6.0):
    """Mots d'une ligne, au format produit par core.extract (mêmes clés)."""
    mots = []
    for k, t in enumerate(texte.split()):
        x0 = x + k * (largeur_car * (len(t) + 1))
        mots.append({
            "text": t, "bbox": [x0, y, x0 + largeur_car * len(t), y + 10],
            "translatable": extract.is_translatable(t), "num": extract.fused_number(t),
            "suffix_en": None, "suffix_bbox": None, "rotation_deg": 0,
            "block_no": block, "line_no": line, "word_no": k,
        })
    return mots


def test_traduire_valeur_applique_le_glossaire_et_garde_codes_et_nombres():
    texte, hg = translate.traduire_valeur(_mots("ANTHRACITE GREY RAL 7016"))
    assert texte == "gris anthracite RAL 7016"
    assert hg == []


def test_traduire_valeur_outside():
    assert translate.traduire_valeur(_mots("OUTSIDE"))[0] == "déporté (extérieur)"


@pytest.mark.parametrize("valeur", ["2000X1500", "1000 kg", "24 V", "3930", "EX.26.396R1", "Ø120 M12"])
def test_traduire_valeur_ne_touche_jamais_nombres_unites_codes(valeur):
    assert translate.traduire_valeur(_mots(valeur))[0] == valeur


def test_traduire_valeur_signale_les_mots_inconnus_sans_les_modifier():
    texte, hg = translate.traduire_valeur(_mots("GREY FOOBAR"))
    assert texte == "GREY FOOBAR"                     # rien d'inventé
    assert {h["source"] for h in hg} == {"GREY", "FOOBAR"}


def test_tableau_specs_traduit_la_valeur_d_une_ligne():
    words = {"words": _mots("COLOUR ANTHRACITE GREY RAL 7016", block=1)}
    table, _ = translate.extraire_tableau_specs(words)
    assert ("Coloris", "gris anthracite RAL 7016") in table


def test_tableau_specs_rattache_la_valeur_de_la_meme_rangee_dans_le_meme_bloc():
    """Mise en page réelle du plan test : « POWER PACK : » et « OUTSIDE » sont deux
    lignes du même bloc, sur la même rangée."""
    words = {"words": _mots("POWER PACK :", x=850, y=715.8, block=12, line=0)
                      + _mots("OUTSIDE", x=1056, y=716.9, block=12, line=1)}
    table, _ = translate.extraire_tableau_specs(words)
    assert ("Groupe hydraulique", "déporté (extérieur)") in table
    assert ("Groupe hydraulique", ":") not in table


def test_tableau_specs_ne_rattache_pas_une_valeur_d_un_autre_bloc():
    words = {"words": _mots("POWER PACK :", x=850, y=715.8, block=12, line=0)
                      + _mots("OUTSIDE", x=1056, y=716.9, block=99, line=0)}
    table, _ = translate.extraire_tableau_specs(words)
    assert ("Groupe hydraulique", ":") in table          # laissé tel quel, à compléter à la main


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="fixture PDF absente")
def test_plan_reel_groupe_hydraulique_deporte():
    wd = Path(tempfile.mkdtemp())
    words = extract.extraire_pdf(FIXTURE_PDF, wd, [1], dpi=72, generer_image=False)[1]
    table, _ = translate.extraire_tableau_specs(words)
    assert ("Groupe hydraulique", "déporté (extérieur)") in table
