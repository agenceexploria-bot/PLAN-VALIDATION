# -*- coding: utf-8 -*-
"""Tests de core/nettoyage.py : purge des dossiers de session périmés."""
import os
import time

from core import nettoyage


def _dossier(racine, nom, age_heures, avec_fichier=True):
    d = racine / nom
    d.mkdir()
    if avec_fichier:
        f = d / "plan.pptx"
        f.write_bytes(b"x")
        t = time.time() - age_heures * 3600
        os.utime(f, (t, t))
    t = time.time() - age_heures * 3600
    os.utime(d, (t, t))
    return d


def test_supprime_seulement_les_dossiers_perimes_au_bon_prefixe(tmp_path):
    vieux = _dossier(tmp_path, "plan_validation_vieux", age_heures=5)
    recent = _dossier(tmp_path, "plan_validation_recent", age_heures=1)
    autre = _dossier(tmp_path, "autre_dossier", age_heures=50)

    assert nettoyage.purger_dossiers_perimes(ttl_heures=3, racine=tmp_path) == 1

    assert not vieux.exists()
    assert recent.exists()
    assert autre.exists()  # hors préfixe : jamais touché


def test_fichier_recent_dans_un_vieux_dossier_le_garde(tmp_path):
    """Une session active rafraîchit ses fichiers sans forcément modifier le
    mtime du dossier : la fraîcheur se juge sur le contenu."""
    d = _dossier(tmp_path, "plan_validation_actif", age_heures=10)
    (d / "render").mkdir()
    (d / "render" / "slide1.png").write_bytes(b"x")  # mtime = maintenant

    assert nettoyage.purger_dossiers_perimes(ttl_heures=3, racine=tmp_path) == 0
    assert d.exists()


def test_racine_inexistante_ne_leve_pas(tmp_path):
    assert nettoyage.purger_dossiers_perimes(racine=tmp_path / "absent") == 0
