# -*- coding: utf-8 -*-
"""
mcp_server/util.py — utilitaires communs aux outils MCP : décodage/encodage
base64, limite de taille des PDF (même règle que l'app Streamlit,
LIMITE_PDF_MO), résolution du PDF d'entrée (base64 direct OU pdf_id mis en
cache, cf. `resoudre_pdf`), et dossier de travail par appel. `core/etat.py`
n'est pas réutilisable ici : il est conçu autour de `st.session_state` (un
dossier par SESSION Streamlit) alors qu'un outil MCP crée son propre dossier
jetable et le détruit avant de renvoyer sa réponse — jamais partagé avec
l'appel suivant. Le PDF fabricant lui-même fait exception à ce principe
(cf. mcp_server/pdf_cache.py) : `resoudre_pdf` est le point d'entrée commun
qui l'assume.
"""
import base64
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from . import pdf_cache

# Même limite que app.py::LIMITE_PDF_MO (app Streamlit) — un PDF fabricant
# plus gros risque de saturer la mémoire du conteneur avant même l'extraction.
LIMITE_PDF_MO = 50


def verifier_taille_pdf(contenu: bytes) -> None:
    """Lève ValueError si `contenu` dépasse LIMITE_PDF_MO. Factorisé pour
    être appliqué aussi bien au PDF décodé depuis base64 (`decoder_pdf`) qu'à
    un PDF reçu par upload HTTP direct (octets déjà en clair, cf.
    mcp_server/server.py::televerser_pdf)."""
    taille_mo = len(contenu) / 1e6
    if taille_mo > LIMITE_PDF_MO:
        raise ValueError(
            f"PDF trop volumineux ({taille_mo:.0f} Mo, limite {LIMITE_PDF_MO} Mo) — "
            "compressez-le ou scindez-le avant de le fournir."
        )


def decoder_pdf(pdf_base64: str) -> bytes:
    """Décode un PDF reçu en base64 et vérifie sa taille AVANT tout
    traitement. Lève ValueError (jamais un plantage silencieux) si le PDF
    dépasse LIMITE_PDF_MO ou si le base64 est invalide."""
    try:
        contenu = base64.b64decode(pdf_base64, validate=True)
    except Exception as e:
        raise ValueError(f"pdf_base64 invalide (décodage base64 impossible) : {e}") from e
    verifier_taille_pdf(contenu)
    return contenu


def resoudre_pdf(pdf_base64: str | None, pdf_id: str | None) -> tuple[bytes, str]:
    """Résout le PDF d'entrée d'un outil qui accepte soit `pdf_id` (chemin
    NORMAL pour un agent Dust réel : PDF déjà en cache, obtenu via un POST
    multipart sur `{pdf_cache.PREFIXE_ROUTE}` ou renvoyé par un appel
    précédent d'`inventaire_pdf`), soit `pdf_base64` en repli (petits
    fichiers, tests directs — cf. mcp_server/pdf_cache.py : un agent Dust
    réel n'a souvent AUCUN moyen de produire ce base64 lui-même, constaté en
    conditions réelles). Exactement un des deux doit être fourni.

    Retourne (contenu, pdf_id) : `pdf_id` est TOUJOURS renvoyable tel quel à
    l'appelant — celui fourni (durée de vie prolongée) si `pdf_id` était
    donné, sinon un nouveau fraîchement mis en cache à partir du base64."""
    if bool(pdf_base64) == bool(pdf_id):
        raise ValueError("fournir exactement un de pdf_base64 ou pdf_id (pas les deux, pas aucun).")
    if pdf_id:
        contenu = pdf_cache.recuperer(pdf_id)
        if contenu is None:
            raise ValueError(f"pdf_id {pdf_id!r} inconnu ou expiré — retéléversez le PDF.")
        return contenu, pdf_id
    contenu = decoder_pdf(pdf_base64)
    return contenu, pdf_cache.mettre_en_cache(contenu)["pdf_id"]


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
