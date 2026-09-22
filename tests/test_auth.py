# -*- coding: utf-8 -*-
"""
Tests d'authentification (app.py, `_authentifie`) — LOT E « durcissement
déploiement » :
  1. l'app doit refuser de démarrer si APP_PASSWORD est absent ou vide
     (exposée publiquement sur Render, jamais d'accès libre par défaut) ;
  2. la comparaison du mot de passe doit fonctionner avec des accents — bug
     trouvé : `secrets.compare_digest` refuse les `str` non-ASCII
     (TypeError), il faut comparer des octets UTF-8.
"""
from pathlib import Path

from streamlit.testing.v1 import AppTest

RACINE = Path(__file__).resolve().parent.parent

SCRIPT = f'''
import runpy
runpy.run_path(r"{RACINE / 'app.py'}")
'''


def _app():
    return AppTest.from_string(SCRIPT, default_timeout=60)


def test_refuse_de_demarrer_si_app_password_absent(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    at = _app()
    at.run()
    assert not at.exception
    assert at.error  # message d'erreur affiché
    # Aucun élément de l'app (titre, upload...) ne doit être rendu au-delà.
    assert not at.session_state.get("workdir")


def test_refuse_de_demarrer_si_app_password_vide(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "   ")
    at = _app()
    at.run()
    assert not at.exception
    assert at.error


def test_mot_de_passe_avec_accents_ne_plante_pas(monkeypatch):
    """Bug trouvé : un APP_PASSWORD contenant un caractère accentué faisait
    planter `secrets.compare_digest` (TypeError sur des `str` non-ASCII)."""
    monkeypatch.setenv("APP_PASSWORD", "Vérifié123")
    at = _app()
    at.run()
    assert not at.exception

    at.text_input(key="mdp_saisi").set_value("Vérifié123")
    connexion = [b for b in at.button if b.label == "Se connecter"][0]
    connexion.click()
    at.run()
    assert not at.exception
    assert at.session_state.get("authentifie") is True


def test_mauvais_mot_de_passe_avec_accents_refuse_proprement(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "Vérifié123")
    at = _app()
    at.run()
    at.text_input(key="mdp_saisi").set_value("Faux mot de passé")
    connexion = [b for b in at.button if b.label == "Se connecter"][0]
    connexion.click()
    at.run()
    assert not at.exception
    assert not at.session_state.get("authentifie")
    assert any("incorrect" in e.value.lower() for e in at.error)
