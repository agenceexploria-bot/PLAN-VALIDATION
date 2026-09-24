# -*- coding: utf-8 -*-
"""
Durcissement du déploiement (LOT E) — contrôles statiques sur les fichiers de
déploiement, pas de comportement applicatif : évite que Dockerfile /
requirements.txt dérivent silencieusement des garanties attendues en prod
(taille d'upload bornée AVANT chargement mémoire, rendu LibreOffice fidèle,
versions reproductibles).
"""
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOCKERFILE = (RACINE / "Dockerfile").read_text(encoding="utf-8")
REQUIREMENTS = (RACINE / "requirements.txt").read_text(encoding="utf-8")

# Doit rester aligné avec app.py::LIMITE_PDF_MO — le serveur Streamlit doit
# refuser un upload trop volumineux AVANT même que app.py ne s'exécute
# (sinon le fichier a déjà été reçu en mémoire quand notre propre contrôle
# Python s'exécute, ce qui annule l'intérêt du garde-fou côté RAM).
LIMITE_PDF_MO = 50


def test_taille_upload_bornee_avant_chargement_memoire():
    assert re.search(rf"--server\.maxUploadSize[=\s]{LIMITE_PDF_MO}\b", DOCKERFILE), (
        "Dockerfile : --server.maxUploadSize doit être fixé à la même limite "
        "que app.py::LIMITE_PDF_MO, pour que Streamlit refuse un upload trop "
        "gros avant qu'il ne soit chargé en mémoire."
    )


def test_carlito_installee_pour_calibri():
    """Les gabarits Vertical sont en Calibri (police du thème). Sans Carlito
    (métriquement compatible Calibri, substituée automatiquement par
    LibreOffice), le rendu Render sortait avec des espaces parasites au milieu
    des mots et des étiquettes de cotes cassées sur plusieurs lignes."""
    assert "fonts-crosextra-carlito" in DOCKERFILE


def test_fonts_liberation_installees_pour_un_rendu_libreoffice_fidele():
    assert "fonts-liberation" in DOCKERFILE, (
        "Dockerfile : fonts-liberation manquant — sans les polices "
        "métriquement compatibles Arial/Times/Courier, le rendu LibreOffice "
        "(moteur de secours sur Render) diverge du rendu PowerPoint de "
        "référence (interlignes, largeurs de texte)."
    )


def test_versions_dependances_epinglees():
    """Un build reproductible ne doit pas dépendre de « la dernière version
    disponible au moment du build » — chaque paquet réellement utilisé par
    core/ doit être épinglé à une version exacte."""
    paquets_critiques = [
        "streamlit", "pymupdf", "python-pptx", "Pillow", "openpyxl",
    ]
    for ligne in REQUIREMENTS.splitlines():
        ligne = ligne.split("#", 1)[0].strip()
        if not ligne or ligne.startswith("#"):
            continue
        nom = re.split(r"[=;<>\s]", ligne, 1)[0]
        for paquet in list(paquets_critiques):
            if nom.lower() == paquet.lower():
                assert "==" in ligne, f"{paquet} n'est pas épinglé à une version exacte : {ligne!r}"
                paquets_critiques.remove(paquet)
    assert not paquets_critiques, f"paquet(s) absent(s) de requirements.txt : {paquets_critiques}"
