# -*- coding: utf-8 -*-
"""
Durcissement du déploiement du serveur MCP (mcp_server/Dockerfile,
mcp_server/requirements.txt) — mêmes contrôles statiques que
tests/test_deploiement.py (app Streamlit), pour ne pas laisser diverger
silencieusement les deux images.
"""
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = (RACINE / "mcp_server" / "Dockerfile").read_text(encoding="utf-8")
REQUIREMENTS = (RACINE / "mcp_server" / "requirements.txt").read_text(encoding="utf-8")


def test_fonts_liberation_installees():
    assert "fonts-liberation" in DOCKERFILE, (
        "sans fonts-liberation, le rendu LibreOffice (seul moteur ici) diverge "
        "de la référence PowerPoint (interlignes, largeurs de texte)."
    )


def test_ne_copie_pas_app_streamlit_ni_tests():
    assert "COPY app.py" not in DOCKERFILE
    assert "COPY tests" not in DOCKERFILE


def test_versions_dependances_epinglees():
    paquets_critiques = ["mcp", "starlette", "uvicorn", "pymupdf", "python-pptx", "Pillow"]
    for ligne in REQUIREMENTS.splitlines():
        ligne = ligne.split("#", 1)[0].strip()
        if not ligne:
            continue
        nom = re.split(r"[=;<>\s]", ligne, 1)[0]
        for paquet in list(paquets_critiques):
            if nom.lower() == paquet.lower():
                assert "==" in ligne, f"{paquet} n'est pas épinglé à une version exacte : {ligne!r}"
                paquets_critiques.remove(paquet)
    assert not paquets_critiques, f"paquet(s) absent(s) de mcp_server/requirements.txt : {paquets_critiques}"
