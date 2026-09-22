# -*- coding: utf-8 -*-
"""
C2 — le glossaire ne publie jamais d'alternative « a/b » sur un document, et ne
contient pas de faux amis qui traduiraient à tort un mot courant.

Chaque alternative tranchée est une décision de terminologie métier : elle est
consignée dans `glossaire.ALTERNATIVES_ECARTEES` ET dans le README (section
« Glossaire : choix à valider par Marin »), pour que Marin puisse la revoir.
"""
import re
from pathlib import Path

import pytest

from core import translate
from data import glossaire

README = Path(__file__).resolve().parent.parent / "README.md"

TABLES = [
    glossaire.CARTOUCHE_SPECS, glossaire.SUFFIXES_COTE, glossaire.EQUIPEMENT_STRUCTURE,
    glossaire.ACCES_SECURITE, glossaire.MACHINERIE_ELECTRICITE, glossaire.CHARGES_DIMENSIONS,
    glossaire.FINITION, glossaire.PLAN_TECHNIQUE_TR, glossaire.MONTAGE_GENIE_CIVIL,
]


def test_aucune_traduction_ne_contient_une_alternative_a_slash_b():
    fautives = {k: v for t in TABLES for k, v in t.items() if "/" in v}
    assert not fautives, fautives


@pytest.mark.parametrize("source,attendu", [
    ("ölçü", "cote"), ("kesit", "coupe"), ("ağırlık", "poids"), ("kontrol", "contrôlé par"),
    ("onay", "approbation"), ("drawing", "dessin"), ("disegno", "dessin"), ("anchor", "ancre"),
])
def test_alternative_tranchee_sur_le_premier_terme(source, attendu):
    assert glossaire.traduire_terme(source) == attendu


def test_chaque_alternative_ecartee_est_documentee_dans_le_glossaire():
    assert glossaire.ALTERNATIVES_ECARTEES, "aucune alternative consignée"
    for source, choix in glossaire.ALTERNATIVES_ECARTEES.items():
        assert glossaire.traduire_terme(source) == choix["retenu"], source
        assert choix["ecarte"] and choix["ecarte"] not in choix["retenu"], source


def test_chaque_alternative_ecartee_figure_au_readme():
    """Le README est le registre que Marin relit : il ne doit pas dériver du code."""
    texte = README.read_text(encoding="utf-8")
    assert "Glossaire : choix à valider par Marin" in texte
    for source, choix in glossaire.ALTERNATIVES_ECARTEES.items():
        assert source in texte and choix["ecarte"] in texte, f"« {source} » absent du README"


@pytest.mark.parametrize("faux_ami", ["not", "data", "kat", "piano"])
def test_faux_amis_retires_du_glossaire(faux_ami):
    assert glossaire.traduire_terme(faux_ami) is None
    assert glossaire.traduire_terme(faux_ami.upper()) is None


def _mots(texte):
    from core import extract
    return [{"text": t, "bbox": [10 * k, 0, 10 * k + 9, 10], "translatable": extract.is_translatable(t),
             "num": None, "suffix_en": None, "suffix_bbox": None, "rotation_deg": 0,
             "block_no": 0, "line_no": 0, "word_no": k} for k, t in enumerate(texte.split())]


@pytest.mark.parametrize("phrase", ["DO NOT SCALE", "DATA", "KAT 1", "PIANO 2"])
def test_une_phrase_courante_n_est_plus_mal_traduite(phrase):
    labels, _ = translate.traduire_labels_planche({"words": _mots(phrase)})
    assert labels == [], [(l["source"], l["text"]) for l in labels]


def test_les_termes_turcs_non_ambigus_restent_traduits():
    assert glossaire.traduire_terme("notlar") == "notes"
    assert glossaire.traduire_terme("tarih") == "date"
