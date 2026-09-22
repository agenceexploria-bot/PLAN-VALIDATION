# -*- coding: utf-8 -*-
"""
mcp_server/util.py — utilitaires communs aux outils MCP : décodage/encodage
base64, limite de taille des PDF (même règle que l'app Streamlit,
LIMITE_PDF_MO), et dossier de travail par appel. `core/etat.py` n'est pas
réutilisable ici : il est conçu autour de `st.session_state` (un dossier par
SESSION Streamlit) alors qu'un serveur MCP ne garde aucun état entre deux
appels — chaque outil crée son propre dossier jetable et le détruit avant de
renvoyer sa réponse, jamais partagé avec l'appel suivant (même d'un même
client), pour rester simple et sans fuite mémoire entre utilisateurs.
"""
import base64
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

# Même limite que app.py::LIMITE_PDF_MO (app Streamlit) — un PDF fabricant
# plus gros risque de saturer la mémoire du conteneur avant même l'extraction.
LIMITE_PDF_MO = 50


def decoder_pdf(pdf_base64: str) -> bytes:
    """Décode un PDF reçu en base64 et vérifie sa taille AVANT tout
    traitement. Lève ValueError (jamais un plantage silencieux) si le PDF
    dépasse LIMITE_PDF_MO ou si le base64 est invalide."""
    try:
        contenu = base64.b64decode(pdf_base64, validate=True)
    except Exception as e:
        raise ValueError(f"pdf_base64 invalide (décodage base64 impossible) : {e}") from e
    taille_mo = len(contenu) / 1e6
    if taille_mo > LIMITE_PDF_MO:
        raise ValueError(
            f"PDF trop volumineux ({taille_mo:.0f} Mo, limite {LIMITE_PDF_MO} Mo) — "
            "compressez-le ou scindez-le avant de le fournir."
        )
    return contenu


def encoder_fichier(chemin: Path) -> str:
    """Encode le contenu d'un fichier en base64 (ASCII)."""
    return base64.b64encode(Path(chemin).read_bytes()).decode("ascii")


@contextmanager
def workdir_temporaire():
    """Dossier de travail jetable pour la durée d'UN appel d'outil — jamais
    réutilisé d'un appel à l'autre (contrairement à l'app Streamlit, qui garde
    un workdir par session utilisateur) : chaque appel MCP est indépendant,
    sans état conservé côté serveur au-delà de sa propre exécution."""
    wd = Path(tempfile.mkdtemp(prefix="mcp_plan_validation_"))
    try:
        yield wd
    finally:
        shutil.rmtree(wd, ignore_errors=True)
