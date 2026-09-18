# -*- coding: utf-8 -*-
"""
Tests de non-régression pour data/glossaire.py et core/translate.py.
"""
from data import glossaire
from core import translate


def test_repli_turc_majuscules_et_minuscules():
    """'SINIF' (sans le ı sans point, export CAO courant) doit être reconnu
    comme 'sınıf' -> 'classe'. Bug trouvé : le repli ASCII ne couvrait que
    les minuscules turques, pas leurs majuscules (Ö/Ü/Ç/Ş/Ğ)."""
    assert glossaire.traduire_terme("SINIF") == "classe"
    assert glossaire.traduire_terme("KALİTE") == "qualité"  # İ majuscule pointé


def test_phrase_multi_mots_toutes_capitales():
    """'SERBEST ÖLÇÜ TOLERANSLARI' (3 mots, capitales, dernier mot en I ASCII
    au lieu du ı turc) doit matcher la clé glossaire malgré les deux écarts
    combinés (casse + diacritique)."""
    assert glossaire.traduire_terme("SERBEST ÖLÇÜ TOLERANSLARI") == "tolérances générales (cotes libres)"


def test_labels_planche_regroupe_les_mots_de_la_meme_ligne():
    """Une expression de plusieurs mots glossaire (ici simulée) doit être
    reconnue comme UNE étiquette, pas mot par mot."""
    words_data = {
        "words": [
            {"text": "WITH", "bbox": [0, 0, 10, 10], "translatable": True,
             "num": None, "suffix_bbox": None, "rotation_deg": 0,
             "block_no": 0, "line_no": 0, "word_no": 0},
            {"text": "OPERATOR", "bbox": [10, 0, 20, 10], "translatable": True,
             "num": None, "suffix_bbox": None, "rotation_deg": 0,
             "block_no": 0, "line_no": 0, "word_no": 1},
            {"text": "ON", "bbox": [20, 0, 30, 10], "translatable": True,
             "num": None, "suffix_bbox": None, "rotation_deg": 0,
             "block_no": 0, "line_no": 0, "word_no": 2},
            {"text": "BOARD", "bbox": [30, 0, 40, 10], "translatable": True,
             "num": None, "suffix_bbox": None, "rotation_deg": 0,
             "block_no": 0, "line_no": 0, "word_no": 3},
        ]
    }
    labels, hors_glossaire = translate.traduire_labels_planche(words_data)
    assert len(labels) == 1
    assert labels[0]["text"] == "avec opérateur à bord"
    assert labels[0]["bbox"] == [0, 0, 40, 10]
    assert hors_glossaire == []


def test_cote_fusionnee_ne_reecrit_jamais_le_nombre():
    """Le nombre d'une cote fusionnée ne doit jamais apparaître dans le texte
    de l'étiquette générée — seul le suffixe est traduit (règle absolue n°2)."""
    words_data = {"words": [{
        "text": "9700(FFL)", "bbox": [0, 0, 10, 10], "translatable": True,
        "num": "9700", "suffix_en": "(FFL)", "suffix_bbox": [8, 0, 10, 10],
        "rotation_deg": 0, "block_no": 0, "line_no": 0, "word_no": 0,
    }]}
    labels, _ = translate.traduire_labels_planche(words_data)
    assert len(labels) == 1
    assert "9700" not in labels[0]["text"]
    assert labels[0]["bbox"] == [8, 0, 10, 10]


def test_completer_champs_commerciaux_toujours_ajoutes():
    table, ajouts = translate.completer_champs_commerciaux([("Modèle", "DHYA.2")])
    labels = [lab for lab, _ in table]
    assert "Portes palières" in labels
    assert "Tension de commande" in labels
    assert set(ajouts) == set(translate.RUBRIQUES_COMMERCIALES)


def test_completer_champs_commerciaux_idempotent():
    """Un champ déjà présent (ex. trouvé sur le plan) n'est pas dupliqué."""
    table, ajouts = translate.completer_champs_commerciaux([("Tension de commande", "24 V")])
    assert table.count(("Tension de commande", "24 V")) == 1
    assert "Tension de commande" not in ajouts
