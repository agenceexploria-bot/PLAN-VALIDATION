# -*- coding: utf-8 -*-
"""
core/etat.py — Règles d'invalidation de l'état de session (audit §1).

Deux garanties, extraites de app.py pour être testables hors de l'UI :

  1. Un PDF fabricant déposé en remplacement REMPLACE réellement le précédent.
     Détecté par l'empreinte du CONTENU, jamais par le chemin du fichier de
     travail (identique pour toute la session : l'ancienne comparaison de
     chemins ne se déclenchait donc jamais après le premier dépôt).

  2. Aucune validation ne survit à un nouvel assemblage. Règle stricte : pas
     de PDF sans validation du PPTX COURANT. La validation visuelle est en
     plus liée à l'empreinte du PPTX validé (`visuel_valide_pour`) : même si
     `visuel_valide` restait à True par un chemin imprévu, l'export PDF est
     refusé si l'empreinte ne correspond pas au PPTX courant.

`session` = `st.session_state` (ou n'importe quel dict, pour les tests).
"""
import hashlib
import shutil
from pathlib import Path


def defauts() -> dict:
    """Valeurs initiales de l'état de session (source unique, utilisée par
    `app._init_etat` ET par les réinitialisations ci-dessous)."""
    return {
        "etape": 1,
        "pdf_path": None,
        "pdf_sha": None,
        "apercus": None,
        "pages_sans_texte_apercu": [],
        "roles": None,
        "meta": {},
        "checklist_path": None,
        "traite": False,
        "words_par_page": {},
        "planches": None,
        "view3d": None,
        "specs_table": None,
        "champs_commerciaux_ajoutes": [],
        "pages_scannees": [],
        "hors_glossaire": [],
        "pages_ignorees": [],
        "pptx_path": None,
        "png_paths": None,
        "visuel_valide": False,
        "visuel_valide_pour": None,
        "render_engine": None,
        "rapport_texte": None,
        "pdf_path_final": None,
    }


# Tout ce qui atteste qu'un PPTX a été vérifié / exporté.
CLES_VALIDATION = (
    "png_paths", "visuel_valide", "visuel_valide_pour", "render_engine",
    "pdf_path_final",
)
# Cases à cocher de l'étape 4 : leur état est porté par le widget, il faut
# donc aussi le supprimer pour qu'une ancienne coche ne réapparaisse pas.
WIDGETS_VALIDATION = ("chk_visuel", "chk_accord")

# Tout ce qui dérive du contenu du PDF fabricant.
CLES_AVAL_PDF = (
    "apercus", "pages_sans_texte_apercu", "roles", "traite", "words_par_page",
    "planches", "view3d", "specs_table", "champs_commerciaux_ajoutes",
    "pages_scannees", "hors_glossaire", "pages_ignorees", "pptx_path",
)
PREFIXES_WIDGETS_AVAL = ("role_",)
WIDGETS_AVAL = ("editeur_specs",)


def empreinte(chemin) -> str:
    """SHA-256 du contenu d'un fichier."""
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def _supprimer(chemin) -> None:
    if chemin:
        try:
            Path(chemin).unlink(missing_ok=True)
        except OSError:
            pass


def invalider_validation(session, workdir) -> None:
    """À appeler AVANT chaque nouvel assemblage : oublie toute validation,
    tout rendu et tout PDF exporté issus d'un PPTX précédent, en mémoire ET
    sur disque (sinon un ancien PDF ou d'anciens PNG resteraient à côté du
    nouveau PPTX)."""
    _supprimer(session.get("pdf_path_final"))
    pptx = session.get("pptx_path")
    if pptx:
        _supprimer(Path(pptx).with_suffix(".pdf"))
    shutil.rmtree(Path(workdir) / "render", ignore_errors=True)

    valeurs = defauts()
    for cle in CLES_VALIDATION:
        session[cle] = valeurs[cle]
    for cle in WIDGETS_VALIDATION:
        if cle in session:
            del session[cle]


def invalider_aval_pdf(session, workdir) -> None:
    """À appeler quand le PDF fabricant change : tout ce qui en dérive
    (aperçus, rôles choisis, extraction, tableau specs, PPTX, validation)
    repart de zéro. Les métadonnées du dossier saisies à l'étape 2 sont
    conservées."""
    pptx = session.get("pptx_path")
    invalider_validation(session, workdir)
    _supprimer(pptx)

    valeurs = defauts()
    for cle in CLES_AVAL_PDF:
        session[cle] = valeurs[cle]
    for cle in list(session.keys()):
        if cle in WIDGETS_AVAL or cle.startswith(PREFIXES_WIDGETS_AVAL):
            del session[cle]


def deposer_pdf(session, contenu: bytes, chemin: Path) -> bool:
    """Enregistre le PDF fabricant déposé dans `chemin`. Retourne True si
    c'est un NOUVEAU contenu (fichier réécrit, aval invalidé), False si c'est
    le même que celui déjà en session (rien ne change)."""
    sha = hashlib.sha256(contenu).hexdigest()
    if session.get("pdf_sha") == sha and Path(chemin).exists():
        return False
    chemin = Path(chemin)
    chemin.write_bytes(contenu)
    invalider_aval_pdf(session, chemin.parent)
    session["pdf_path"] = chemin
    session["pdf_sha"] = sha
    return True


def validation_courante(session, pptx_path) -> bool:
    """True seulement si l'utilisateur a validé visuellement CE PPTX (pas un
    précédent) : coche posée ET empreinte enregistrée == empreinte actuelle."""
    if not session.get("visuel_valide"):
        return False
    attendue = session.get("visuel_valide_pour")
    if not attendue or not pptx_path or not Path(pptx_path).exists():
        return False
    return attendue == empreinte(pptx_path)
