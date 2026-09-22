# -*- coding: utf-8 -*-
"""
Tests de l'intégrité de l'état de session (app.py) — audit §1 « Critique » :
  1. un PDF déposé en remplacement doit réellement remplacer le précédent ;
  2. aucune validation (visuel / rendu / PDF) ne survit à un nouvel
     assemblage : pas de PDF sans validation du PPTX COURANT.

Exécutés dans l'application réelle via `streamlit.testing.v1.AppTest`
(le script tourne comme dans Streamlit). `st.file_uploader` n'étant pas
géré par AppTest, il est remplacé par un faux qui rend les octets posés
dans `st.session_state["_pdf_test"]`.
"""
import os
from pathlib import Path

import fitz
from PIL import Image
from streamlit.testing.v1 import AppTest

from core import assemble

RACINE = Path(__file__).resolve().parent.parent
FIXTURE_PDF = RACINE / "tests" / "fixtures" / "DHYA2_test.pdf"

SCRIPT = f'''
import runpy
import streamlit as st

class _Fichier:
    def __init__(self, octets):
        self._octets = octets
        self.size = len(octets)
    def getvalue(self):
        return self._octets

_vrai = st.file_uploader
def _faux(label, *args, **kwargs):
    if "PDF" in label:
        octets = st.session_state.get("_pdf_test")
        return _Fichier(octets) if octets else None
    return _vrai(label, *args, **kwargs)
st.file_uploader = _faux

runpy.run_path(r"{RACINE / 'app.py'}")
'''


def _app():
    # Ces tests portent sur l'intégrité de l'état de session, pas sur l'écran
    # de connexion (couvert par tests/test_auth.py) : on s'authentifie
    # directement, APP_PASSWORD étant désormais obligatoire (LOT E).
    os.environ.setdefault("APP_PASSWORD", "mot-de-passe-de-test")
    at = AppTest.from_string(SCRIPT, default_timeout=120)
    at.session_state["authentifie"] = True
    return at


def _pdf_une_page() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "UN AUTRE PLAN")
    octets = doc.tobytes()
    doc.close()
    return octets


def _bouton(at, libelle):
    trouves = [b for b in at.button if b.label == libelle]
    return trouves[0] if trouves else None


def _libelles_boutons(at):
    return [b.label for b in at.button]


# ---------------------------------------------------------------------------
# A1 — PDF remplacé sans effet
# ---------------------------------------------------------------------------

def test_nouveau_pdf_remplace_reellement_le_precedent():
    at = _app()
    at.session_state["_pdf_test"] = FIXTURE_PDF.read_bytes()
    at.run()
    assert not at.exception
    assert len(at.session_state.apercus) == 2  # la fixture a 2 pages

    nouveau = _pdf_une_page()
    at.session_state["_pdf_test"] = nouveau
    at.run()
    assert not at.exception

    # Le fichier de travail est bien le NOUVEAU PDF...
    assert at.session_state.pdf_path.read_bytes() == nouveau
    # ...et les aperçus / rôles suivent le nouveau document (1 page), pas l'ancien.
    assert len(at.session_state.apercus) == 1
    assert len(at.session_state.roles) == 1


def test_nouveau_pdf_ne_garde_pas_le_choix_de_roles_de_l_ancien():
    at = _app()
    at.session_state["_pdf_test"] = FIXTURE_PDF.read_bytes()
    at.run()
    # L'utilisateur reclasse la page 2 en « Ignorée »
    at.selectbox(key="role_1").select("Ignorée (administrative fabricant)")
    at.run()
    assert at.session_state.roles[1].startswith("Ignorée")

    # Nouveau PDF de 2 pages : les rôles repartent du défaut (garde + planche)
    doc = fitz.open()
    doc.new_page(); doc.new_page()
    at.session_state["_pdf_test"] = doc.tobytes()
    doc.close()
    at.run()
    assert at.session_state.roles[0].startswith("Page de garde")
    assert at.session_state.roles[1].startswith("Planche dessin")


# ---------------------------------------------------------------------------
# A1 — validation périmée réutilisée
# ---------------------------------------------------------------------------

META = {
    "numero": "LDTEST200", "client": "CLIENT TEST", "dessinateur": "XX",
    "indice": "R00", "date": "01/01/2026", "equipement": "MONTE-CHARGE",
    "type_equipement": "Non accompagné",
}


def _etat_apres_assemblage(tmp_path, etape):
    """État de session d'un dossier arrivé à l'étape `etape`, avec un PPTX
    « ancien » déjà validé visuellement et un PDF déjà exporté."""
    ancien_pptx = tmp_path / "Plan de validation LDTEST200-R00.pptx"
    assemble.assembler({
        "base": assemble.base_pour_type("non accompagné"), "out": ancien_pptx,
        "workdir": tmp_path, "meta": META, "specs": {"table": [("Modèle", "ANCIEN")]},
        "view3d": None, "planches": [],
    })
    ancien_pdf = ancien_pptx.with_suffix(".pdf")
    ancien_pdf.write_bytes(b"%PDF-1.4 ancien")
    render_dir = tmp_path / "render"
    render_dir.mkdir()
    ancien_png = render_dir / "slide_1.png"
    Image.new("RGB", (40, 30), "white").save(ancien_png)  # vrai PNG (st.image le relit)
    return {
        "workdir": tmp_path, "etape": etape,
        "pdf_path": tmp_path / "plan_fabricant.pdf", "apercus": [b"x"],
        "roles": ["Planche dessin (cotée)"], "meta": dict(META),
        "traite": True, "words_par_page": {}, "planches": [],
        "view3d": None, "specs_table": [("Modèle", "NOUVEAU")],
        "champs_commerciaux_ajoutes": [], "pages_scannees": [],
        "hors_glossaire": [], "pages_ignorees": [],
        "pptx_path": ancien_pptx, "png_paths": [ancien_png],
        "visuel_valide": True, "render_engine": "powerpoint",
        "pdf_path_final": ancien_pdf,
    }


def test_nouvel_assemblage_invalide_toute_validation_precedente(tmp_path):
    at = _app()
    for cle, val in _etat_apres_assemblage(tmp_path, etape=3).items():
        at.session_state[cle] = val
    at.run()
    assert not at.exception

    _bouton(at, "Assembler le PPTX →").click()
    at.run()
    assert not at.exception

    assert at.session_state.etape == 4
    assert at.session_state.visuel_valide is False
    assert at.session_state.png_paths is None
    assert at.session_state.pdf_path_final is None
    assert at.session_state.render_engine is None
    # L'utilisateur ne peut pas « continuer » sur la foi de l'ancienne validation
    assert _bouton(at, "Continuer →").disabled
    # Les fichiers périmés ne traînent plus sur le disque
    assert not (tmp_path / "Plan de validation LDTEST200-R00.pdf").exists()
    assert not (tmp_path / "render" / "slide_1.png").exists()


def test_etape_5_refuse_l_export_pdf_si_la_validation_ne_porte_pas_sur_le_pptx_courant(tmp_path):
    """Filet de sécurité indépendant du reset : même si `visuel_valide` est
    resté True par un chemin non prévu, le PDF n'est proposé que si la
    validation a été faite sur CE PPTX (empreinte identique)."""
    at = _app()
    etat = _etat_apres_assemblage(tmp_path, etape=5)
    etat["pdf_path_final"] = None
    for cle, val in etat.items():
        at.session_state[cle] = val
    at.run()
    assert not at.exception
    assert "Générer le PDF" not in _libelles_boutons(at)


def test_telechargement_pptx_possible_sans_moteur_de_rendu(tmp_path, monkeypatch):
    """LOT E — durcissement déploiement : sans PowerPoint NI LibreOffice sur
    le serveur, l'utilisateur doit quand même pouvoir télécharger le PPTX
    (document de travail, à vérifier lui-même dans son propre logiciel) —
    seul l'export PDF (qui exige une validation visuelle réelle faite ICI)
    doit rester bloqué."""
    from core import render as render_mod

    monkeypatch.setattr(render_mod, "disponible", lambda: False)

    meta = {"numero": "LDTEST500", "client": "CLIENT TEST", "dessinateur": "XX",
            "indice": "R00", "date": "01/01/2026", "equipement": "MONTE-CHARGE",
            "type_equipement": "Non accompagné"}
    pptx_path = tmp_path / "Plan de validation LDTEST500-R00.pptx"
    assemble.assembler({
        "base": assemble.base_pour_type("non accompagné"), "out": pptx_path,
        "workdir": tmp_path, "meta": meta, "specs": {}, "planches": [],
    })

    at = _app()
    for cle, val in {
        "workdir": tmp_path, "etape": 4,
        "pdf_path": tmp_path / "plan_fabricant.pdf", "apercus": [b"x"],
        "roles": ["Planche dessin (cotée)"], "meta": meta,
        "traite": True, "words_par_page": {}, "planches": [],
        "view3d": None, "specs_table": [],
        "champs_commerciaux_ajoutes": [], "pages_scannees": [],
        "hors_glossaire": [], "pages_ignorees": [],
        "pptx_path": pptx_path, "png_paths": None,
        "visuel_valide": False, "render_engine": None,
        "pdf_path_final": None,
    }.items():
        at.session_state[cle] = val
    at.run()
    assert not at.exception

    # Sans moteur de rendu, le bouton reste protégé par une prise de
    # conscience explicite (pas d'accès inconditionnel) — mais reste
    # atteignable, contrairement à avant.
    at.checkbox(key="chk_accord").set_value(True)
    at.run()
    continuer = _bouton(at, "Continuer →")
    assert continuer is not None
    assert not continuer.disabled, "sans moteur de rendu, le PPTX doit rester téléchargeable"
    continuer.click()
    at.run()
    assert not at.exception
    assert at.session_state.etape == 5

    assert "Télécharger le PPTX" in [b.label for b in at.download_button]
    assert "Générer le PDF" not in _libelles_boutons(at)


def test_etape_5_propose_l_export_pdf_si_la_validation_porte_sur_le_pptx_courant(tmp_path):
    from core import etat as etat_mod  # noqa: PLC0415 — n'existe qu'après la correction

    at = _app()
    etat = _etat_apres_assemblage(tmp_path, etape=5)
    etat["pdf_path_final"] = None
    etat["visuel_valide_pour"] = etat_mod.empreinte(etat["pptx_path"])
    for cle, val in etat.items():
        at.session_state[cle] = val
    at.run()
    assert not at.exception
    assert "Générer le PDF" in _libelles_boutons(at)
