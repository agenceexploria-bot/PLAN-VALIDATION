# -*- coding: utf-8 -*-
"""Ligne `[mem]` de core/memlog.py, sur de faux fichiers cgroup."""
from core import memlog

MO = 1024 * 1024


def test_cgroup_v2(tmp_path):
    (tmp_path / "memory.current").write_text(f"{300 * MO}\n")
    (tmp_path / "memory.peak").write_text(f"{455 * MO}\n")
    (tmp_path / "memory.max").write_text(f"{512 * MO}\n")
    assert memlog.ligne_memoire_conteneur("verifier_rendu", tmp_path) == \
        "[mem] verifier_rendu courant=300.0 pic=455.0 limite=512.0"


def test_cgroup_v2_sans_limite_ni_peak(tmp_path):
    """Noyau < 5.19 : pas de memory.peak ; conteneur sans limite : « max »."""
    (tmp_path / "memory.current").write_text(f"{100 * MO}\n")
    (tmp_path / "memory.max").write_text("max\n")
    assert memlog.ligne_memoire_conteneur("exporter_pdf", tmp_path) == \
        "[mem] exporter_pdf courant=100.0 pic=indisponible limite=illimitee"


def test_cgroup_v1(tmp_path):
    controleur = tmp_path / "memory"
    controleur.mkdir()
    (controleur / "memory.usage_in_bytes").write_text(f"{200 * MO}")
    (controleur / "memory.max_usage_in_bytes").write_text(f"{400 * MO}")
    (controleur / "memory.limit_in_bytes").write_text("9223372036854771712")
    assert memlog.ligne_memoire_conteneur("extraire_page", tmp_path) == \
        "[mem] extraire_page courant=200.0 pic=400.0 limite=illimitee"


def test_cgroup_absent(tmp_path):
    assert memlog.ligne_memoire_conteneur("inventaire_pdf", tmp_path / "absent") == \
        "[mem] inventaire_pdf courant=indisponible pic=indisponible limite=indisponible"


def test_fichier_illisible_ne_leve_jamais(tmp_path, capsys):
    (tmp_path / "memory.current").mkdir()  # lecture -> OSError
    memlog.logger_memoire_conteneur("verifier_rendu", tmp_path)
    assert capsys.readouterr().out.strip() == \
        "[mem] verifier_rendu courant=indisponible pic=indisponible limite=indisponible"


def test_journalise_apres_chaque_travail_lourd(capsys):
    """Même quand l'outil échoue, et le verrou est bien rendu."""
    from mcp_server import util

    try:
        with util.travail_lourd("exporter_pdf"):
            raise RuntimeError("échec de l'outil")
    except RuntimeError:
        pass
    assert "[mem] exporter_pdf " in capsys.readouterr().out
    assert util.VERROU_TRAVAIL_LOURD.acquire(blocking=False)
    util.VERROU_TRAVAIL_LOURD.release()
