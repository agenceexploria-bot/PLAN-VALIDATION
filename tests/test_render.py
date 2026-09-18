# -*- coding: utf-8 -*-
"""
Tests de non-régression pour core/render.py — bascule PowerPoint ->
LibreOffice et respect de la règle « même moteur pour le rendu de
vérification et pour l'export PDF ».

Les moteurs réels (COM, `soffice`) ne sont JAMAIS invoqués ici : les
fonctions privées par moteur (`_rendre_pngs_powerpoint`, etc.) et les
détections (`powerpoint_disponible`, `libreoffice_disponible`) sont
mockées via `monkeypatch`, pour que ces tests passent aussi bien sur une
machine sans PowerPoint ni LibreOffice installés (CI) que sur un poste de
dev équipé — jamais de `skipif` qui masquerait silencieusement le chemin
LibreOffice.
"""
from pathlib import Path

import pytest

from core import render


@pytest.fixture(autouse=True)
def _isole_cache_libreoffice(monkeypatch):
    """Empêche les tests de dépendre du (ou de polluer le) cache de
    détection LibreOffice de la machine qui exécute réellement les tests."""
    monkeypatch.setattr(render, "_LIBREOFFICE_PATH", None, raising=False)
    yield


# ---------------------------------------------------------------------------
# Détection + cache (règle : un seul appel système par session)
# ---------------------------------------------------------------------------

def test_libreoffice_disponible_ne_plante_jamais():
    """Peu importe si LibreOffice est réellement installé ou non sur la
    machine qui exécute les tests : la détection ne doit jamais lever."""
    assert isinstance(render.libreoffice_disponible(), bool)


def test_libreoffice_disponible_met_en_cache(monkeypatch):
    """La détection ne doit interroger le système qu'une fois par session
    (bug audit potentiel : un appel process à chaque rendu serait coûteux)."""
    appels = []

    def _chercher():
        appels.append(1)
        return "/fake/soffice"

    monkeypatch.setattr(render, "_chercher_libreoffice", _chercher)
    assert render.libreoffice_disponible() is True
    assert render.libreoffice_disponible() is True
    assert len(appels) == 1


def test_disponible_vrai_si_au_moins_un_moteur(monkeypatch):
    monkeypatch.setattr(render, "powerpoint_disponible", lambda: False)
    monkeypatch.setattr(render, "libreoffice_disponible", lambda: True)
    assert render.disponible() is True

    monkeypatch.setattr(render, "libreoffice_disponible", lambda: False)
    assert render.disponible() is False


# ---------------------------------------------------------------------------
# rendre_pngs — bascule automatique (moteur=None, 1re tentative)
# ---------------------------------------------------------------------------

def test_rendre_pngs_utilise_powerpoint_si_disponible(monkeypatch, tmp_path):
    """PowerPoint est le moteur prioritaire : s'il réussit, LibreOffice ne
    doit même pas être sollicité."""
    appels_libreoffice = []
    monkeypatch.setattr(render, "powerpoint_disponible", lambda: True)
    monkeypatch.setattr(render, "libreoffice_disponible", lambda: True)
    monkeypatch.setattr(render, "_rendre_pngs_powerpoint", lambda p, o, s: [o / "slide_1.png"])
    monkeypatch.setattr(render, "_rendre_pngs_libreoffice",
                         lambda p, o, s: appels_libreoffice.append(1) or [])

    resultat = render.rendre_pngs(tmp_path / "x.pptx", tmp_path / "out")
    assert resultat["moteur"] == render.MOTEUR_POWERPOINT
    assert resultat["pngs"] == [tmp_path / "out" / "slide_1.png"]
    assert appels_libreoffice == []


def test_rendre_pngs_bascule_sur_libreoffice_si_powerpoint_echoue(monkeypatch, tmp_path):
    """Bug/demande audit : si l'appel COM échoue (n'importe quelle erreur,
    pas seulement la licence déjà gérée), bascule automatique sur
    LibreOffice plutôt que de remonter l'échec PowerPoint tel quel."""
    def _ppt_echoue(p, o, s):
        raise RuntimeError("PowerPoint : erreur COM simulée")

    monkeypatch.setattr(render, "powerpoint_disponible", lambda: True)
    monkeypatch.setattr(render, "libreoffice_disponible", lambda: True)
    monkeypatch.setattr(render, "_rendre_pngs_powerpoint", _ppt_echoue)
    monkeypatch.setattr(render, "_rendre_pngs_libreoffice", lambda p, o, s: [o / "slide_1.png"])

    resultat = render.rendre_pngs(tmp_path / "x.pptx", tmp_path / "out")
    assert resultat["moteur"] == render.MOTEUR_LIBREOFFICE
    assert resultat["pngs"] == [tmp_path / "out" / "slide_1.png"]


def test_rendre_pngs_bascule_sur_libreoffice_si_powerpoint_absent(monkeypatch, tmp_path):
    """Poste sans PowerPoint du tout (ex. non-Windows) : LibreOffice est
    utilisé directement, sans même tenter PowerPoint."""
    appels_powerpoint = []
    monkeypatch.setattr(render, "powerpoint_disponible", lambda: False)
    monkeypatch.setattr(render, "libreoffice_disponible", lambda: True)
    monkeypatch.setattr(render, "_rendre_pngs_powerpoint",
                         lambda p, o, s: appels_powerpoint.append(1) or [])
    monkeypatch.setattr(render, "_rendre_pngs_libreoffice", lambda p, o, s: [o / "slide_1.png"])

    resultat = render.rendre_pngs(tmp_path / "x.pptx", tmp_path / "out")
    assert resultat["moteur"] == render.MOTEUR_LIBREOFFICE
    assert appels_powerpoint == []


def test_rendre_pngs_erreur_explicite_si_aucun_moteur_disponible(monkeypatch, tmp_path):
    monkeypatch.setattr(render, "powerpoint_disponible", lambda: False)
    monkeypatch.setattr(render, "libreoffice_disponible", lambda: False)

    with pytest.raises(RuntimeError, match="(?i)powerpoint.*libreoffice"):
        render.rendre_pngs(tmp_path / "x.pptx", tmp_path / "out")


def test_rendre_pngs_erreur_combinee_si_les_deux_moteurs_echouent(monkeypatch, tmp_path):
    def _ppt_echoue(p, o, s):
        raise RuntimeError("boom ppt")

    def _lo_echoue(p, o, s):
        raise RuntimeError("boom lo")

    monkeypatch.setattr(render, "powerpoint_disponible", lambda: True)
    monkeypatch.setattr(render, "libreoffice_disponible", lambda: True)
    monkeypatch.setattr(render, "_rendre_pngs_powerpoint", _ppt_echoue)
    monkeypatch.setattr(render, "_rendre_pngs_libreoffice", _lo_echoue)

    with pytest.raises(RuntimeError) as exc:
        render.rendre_pngs(tmp_path / "x.pptx", tmp_path / "out")
    assert "boom ppt" in str(exc.value)
    assert "boom lo" in str(exc.value)


# ---------------------------------------------------------------------------
# moteur imposé — règle « même moteur pour rendu et export PDF »
# ---------------------------------------------------------------------------

def test_exporter_pdf_respecte_le_moteur_impose(monkeypatch, tmp_path):
    """L'export PDF (étape 5) doit réutiliser le même moteur que le rendu
    de vérification déjà validé (étape 4) — jamais un moteur différent
    choisi au hasard, même si l'autre moteur est disponible et
    fonctionnerait (bug audit potentiel)."""
    appels_powerpoint = []
    monkeypatch.setattr(render, "powerpoint_disponible", lambda: True)
    monkeypatch.setattr(render, "libreoffice_disponible", lambda: True)
    monkeypatch.setattr(render, "_exporter_pdf_powerpoint",
                         lambda p: appels_powerpoint.append(1) or Path("nope.pdf"))
    monkeypatch.setattr(render, "_exporter_pdf_libreoffice", lambda p: tmp_path / "x.pdf")

    resultat = render.exporter_pdf(tmp_path / "x.pptx", moteur=render.MOTEUR_LIBREOFFICE)
    assert resultat["moteur"] == render.MOTEUR_LIBREOFFICE
    assert resultat["pdf"] == tmp_path / "x.pdf"
    assert appels_powerpoint == []


def test_exporter_pdf_moteur_impose_qui_echoue_ne_bascule_pas(monkeypatch, tmp_path):
    """Si le moteur imposé échoue, l'export doit échouer explicitement —
    jamais basculer silencieusement sur l'autre moteur (ce qui livrerait un
    PDF différent de ce qui a été visuellement validé)."""
    appels_libreoffice = []

    def _ppt_echoue(p):
        raise RuntimeError("PowerPoint indisponible (licence)")

    monkeypatch.setattr(render, "powerpoint_disponible", lambda: True)
    monkeypatch.setattr(render, "libreoffice_disponible", lambda: True)
    monkeypatch.setattr(render, "_exporter_pdf_powerpoint", _ppt_echoue)
    monkeypatch.setattr(render, "_exporter_pdf_libreoffice",
                         lambda p: appels_libreoffice.append(1) or (tmp_path / "x.pdf"))

    with pytest.raises(RuntimeError, match="(?i)powerpoint"):
        render.exporter_pdf(tmp_path / "x.pptx", moteur=render.MOTEUR_POWERPOINT)
    assert appels_libreoffice == []


def test_exporter_pdf_moteur_libreoffice_impose_mais_indisponible(monkeypatch, tmp_path):
    """Moteur imposé mais non détecté sur ce poste : erreur explicite (pas
    un crash subprocess sur un chemin vide)."""
    monkeypatch.setattr(render, "libreoffice_disponible", lambda: False)

    with pytest.raises(RuntimeError, match="(?i)libreoffice"):
        render.exporter_pdf(tmp_path / "x.pptx", moteur=render.MOTEUR_LIBREOFFICE)


def test_moteur_inconnu_leve_value_error(tmp_path):
    with pytest.raises(ValueError):
        render.rendre_pngs(tmp_path / "x.pptx", tmp_path / "out", moteur="powerpoint-turbo")
