# -*- coding: utf-8 -*-
"""
core/render.py — Étapes 5b (contrôle visuel) et 6 (export PDF) du pipeline.
Adapté de scripts/render_check.py du skill `plan-validation-vertical` :
pilote PowerPoint via COM (pywin32) pour obtenir le rendu RÉEL du PPTX — la
référence visuelle la plus fidèle (cf. references/verification.md : un
aperçu PIL/pymupdf diverge du rendu PowerPoint réel, notamment sur la
largeur des zones de texte et les rotations).

Moteur de secours LibreOffice (`soffice` headless), comme le prévoyait le
cahier des charges initial pour rester portable (Windows non requis, pas de
licence Office nécessaire) : PowerPoint reste prioritaire (référence), mais
si l'automatisation COM échoue pour n'importe quelle raison (licence non
activée, PowerPoint absent, poste non Windows...), on bascule automatiquement
sur LibreOffice — jamais de plantage brut tant qu'un des deux est utilisable.
Le moteur utilisé est toujours renvoyé à l'appelant (`{"moteur": ..., ...}`)
pour affichage explicite côté UI (l'utilisateur qui valide le contrôle
visuel doit savoir si c'est le rendu de référence ou un rendu de secours
potentiellement légèrement différent).

Règle absolue : l'export PDF (étape 6) doit réutiliser le MÊME moteur que
celui qui a produit le rendu de vérification déjà validé par l'utilisateur
(étape 5b) — jamais un moteur différent choisi au hasard entre les deux
étapes, pour ne jamais livrer un PDF qui diffère de ce qui a été validé
visuellement. C'est le rôle du paramètre `moteur` de `rendre_pngs` /
`exporter_pdf` : quand il est fourni, AUCUNE bascule n'est tentée — l'appel
échoue explicitement plutôt que de basculer silencieusement sur l'autre
moteur.

⚠️ Toujours passer un chemin ABSOLU : ni PowerPoint COM ni LibreOffice ne
résolvent les chemins relatifs de façon fiable.

⚠️ Streamlit exécute le script dans un thread dédié (ScriptRunner), pas le
thread principal : COM doit y être explicitement initialisé (`CoInitialize`)
avant tout `Dispatch`, sinon pywin32 échoue avec « CoInitialize n'a pas été
appelé » — trouvé en testant l'app via `streamlit.testing.v1.AppTest`, qui
exécute le script dans un thread comme le fait réellement Streamlit.
"""
from contextlib import contextmanager
from pathlib import Path

PP_PDF = 32  # ppSaveAsPDF

MOTEUR_POWERPOINT = "powerpoint"
MOTEUR_LIBREOFFICE = "libreoffice"

# Cache de détection LibreOffice pour la session (un appel `soffice
# --version`/résolution de chemin coûte un process, à ne pas refaire à
# chaque rendu) : None = pas encore résolu, "" = résolu absent, sinon le
# chemin trouvé.
_LIBREOFFICE_PATH = None


def powerpoint_disponible() -> bool:
    """True si l'automatisation PowerPoint (COM) est utilisable sur ce
    poste (pywin32 installé — ne garantit pas que PowerPoint est installé
    ni sa licence activée, seulement que l'appel COM peut être tenté)."""
    try:
        import win32com.client  # noqa: F401
        return True
    except ImportError:
        return False


def _chercher_libreoffice():
    """Cherche le binaire `soffice` : PATH d'abord, puis emplacements
    d'installation standards Windows/Linux/Mac. Retourne le chemin trouvé,
    ou None."""
    import shutil

    for nom in ("soffice", "soffice.exe"):
        chemin = shutil.which(nom)
        if chemin:
            return chemin
    candidats = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/opt/libreoffice/program/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for c in candidats:
        if Path(c).exists():
            return c
    return None


def libreoffice_disponible() -> bool:
    """True si LibreOffice (`soffice`) est utilisable sur ce poste. Résultat
    mis en cache pour la session (cf. docstring de module)."""
    global _LIBREOFFICE_PATH
    if _LIBREOFFICE_PATH is None:
        _LIBREOFFICE_PATH = _chercher_libreoffice() or ""
    return bool(_LIBREOFFICE_PATH)


def _chemin_libreoffice() -> str:
    if not libreoffice_disponible():
        raise RuntimeError(
            "LibreOffice (`soffice`) n'a pas été détecté sur ce poste — ni "
            "dans le PATH, ni aux emplacements d'installation standards. "
            "Installez-le (cf. README, section Installation) pour utiliser "
            "ce moteur de secours."
        )
    return _LIBREOFFICE_PATH


def disponible() -> bool:
    """True si au moins un moteur de rendu (PowerPoint ou LibreOffice) est
    utilisable sur ce poste."""
    return powerpoint_disponible() or libreoffice_disponible()


# ---------------------------------------------------------------------------
# Moteur PowerPoint (COM) — référence visuelle
# ---------------------------------------------------------------------------

@contextmanager
def _presentation_ouverte(pptx_path: Path):
    """Ouvre le PPTX dans PowerPoint (COM), avec initialisation COM correcte
    pour le thread courant (obligatoire hors thread principal). Ferme la
    présentation et quitte PowerPoint en sortie, dans tous les cas."""
    import pythoncom
    import pywintypes
    import win32com.client

    pptx_path = Path(pptx_path).resolve()
    pythoncom.CoInitialize()
    ppt = None
    try:
        # Toute la mise en route (Dispatch, Visible, Open) est couverte par le
        # même bloc : `ppt.Visible = True` peut échouer AVANT même d'atteindre
        # Presentations.Open (constaté en conditions réelles : une instance
        # PowerPoint bloquée — ex. activation produit en échec — refuse tout
        # appel COM, y compris Quit()). Sans ce bloc englobant, l'erreur COM
        # brute (pywintypes.com_error, pas un RuntimeError) remontait jusqu'à
        # l'UI Streamlit, qui ne l'interceptait pas (bug audit).
        try:
            ppt = win32com.client.Dispatch("PowerPoint.Application")
            ppt.Visible = True
            pres = ppt.Presentations.Open(str(pptx_path), WithWindow=False)
        except pywintypes.com_error as e:
            if ppt is not None:
                try:
                    ppt.Quit()
                except Exception:
                    pass  # instance déjà bloquée (ex. activation produit) : rien à faire de plus
            raise RuntimeError(
                f"Impossible de piloter PowerPoint pour {pptx_path.name} "
                f"(erreur COM : {e}). Causes fréquentes à vérifier : (1) "
                "PowerPoint n'a jamais été ouvert manuellement sur ce poste "
                "(à faire une fois, pour valider les boîtes de dialogue "
                "initiales) ; (2) un processus POWERPNT.EXE résiduel bloque "
                "l'automatisation (à fermer via le Gestionnaire des tâches, "
                "puis réessayer) ; (3) l'activation de la licence Microsoft "
                "Office/PowerPoint a échoué sur ce poste (ouvrez PowerPoint "
                "manuellement : si le titre de la fenêtre indique « Échec de "
                "l'activation du produit », reconnectez le compte Office "
                "avant de réessayer)."
            ) from e
        try:
            yield pres
        finally:
            # PowerPoint invalide parfois la référence COM de `pres` après un
            # SaveAs (notamment vers PDF) avant qu'on ait pu la fermer
            # nous-mêmes : Close()/Quit() peuvent alors lever une erreur alors
            # que le travail (export PNG/PDF) a déjà réussi. Trouvé en
            # testant l'export PDF de bout en bout : le fichier était déjà
            # écrit sur disque malgré l'exception. On ne fait donc jamais
            # échouer l'opération sur un problème de nettoyage.
            try:
                pres.Close()
            except Exception:
                pass
            try:
                ppt.Quit()
            except Exception:
                pass
    finally:
        pythoncom.CoUninitialize()


def _rendre_pngs_powerpoint(pptx_path: Path, out_dir: Path, slides=None) -> list:
    with _presentation_ouverte(pptx_path) as pres:
        want = slides or range(1, pres.Slides.Count + 1)
        chemins = []
        for i in want:
            p = out_dir / f"slide_{i}.png"
            pres.Slides(i).Export(str(p), "PNG", 1600, 1131)
            chemins.append(p)
        return chemins


def _exporter_pdf_powerpoint(pptx_path: Path) -> Path:
    pdf_path = Path(pptx_path).resolve().with_suffix(".pdf")
    with _presentation_ouverte(pptx_path) as pres:
        pres.SaveAs(str(pdf_path), PP_PDF)
        return pdf_path


# ---------------------------------------------------------------------------
# Moteur LibreOffice (headless) — secours
# ---------------------------------------------------------------------------

def _convertir_pdf_libreoffice(pptx_path: Path, out_dir: Path) -> Path:
    """Convertit le PPTX en PDF via LibreOffice headless. Retourne le
    chemin du PDF produit (même nom que le PPTX, dans `out_dir`)."""
    import subprocess

    pptx_path = Path(pptx_path).resolve()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    soffice = _chemin_libreoffice()
    try:
        resultat = subprocess.run(
            [soffice, "--headless", "--norestore", "--convert-to", "pdf",
             "--outdir", str(out_dir), str(pptx_path)],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"LibreOffice n'a pas répondu dans le délai imparti (120s) pour "
            f"convertir {pptx_path.name}."
        ) from e
    pdf_path = out_dir / (pptx_path.stem + ".pdf")
    if resultat.returncode != 0 or not pdf_path.exists():
        detail = (resultat.stderr or resultat.stdout or "").strip()
        raise RuntimeError(
            f"Échec de la conversion LibreOffice de {pptx_path.name} "
            f"(code {resultat.returncode}). Détail : {detail or 'aucun'}"
        )
    return pdf_path


def _rendre_pngs_libreoffice(pptx_path: Path, out_dir: Path, slides=None) -> list:
    """Rendu PNG par slide via LibreOffice headless : conversion PPTX -> PDF
    (`soffice --convert-to pdf`), puis PDF -> PNG page par page avec
    pymupdf — déjà une dépendance du projet, pas besoin d'un outil externe
    supplémentaire (type poppler/pdftoppm) pour cette seconde étape."""
    import fitz

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = _convertir_pdf_libreoffice(pptx_path, out_dir)
    doc = fitz.open(pdf_path)
    try:
        want = slides or range(1, len(doc) + 1)
        chemins = []
        for i in want:
            p = out_dir / f"slide_{i}.png"
            doc[i - 1].get_pixmap(matrix=fitz.Matrix(2.0, 2.0)).save(p)  # ~144 dpi
            chemins.append(p)
        return chemins
    finally:
        doc.close()


def _exporter_pdf_libreoffice(pptx_path: Path) -> Path:
    """Export PDF via LibreOffice headless — même convention de nom/emplacement
    que le moteur PowerPoint (à côté du PPTX source, même nom, extension .pdf)."""
    pptx_path = Path(pptx_path).resolve()
    return _convertir_pdf_libreoffice(pptx_path, pptx_path.parent)


# ---------------------------------------------------------------------------
# API publique — bascule PowerPoint -> LibreOffice
# ---------------------------------------------------------------------------

def _erreur_aucun_moteur(erreurs: list) -> str:
    if not erreurs:
        return (
            "Aucun moteur de rendu disponible sur ce poste : ni PowerPoint "
            "(COM) ni LibreOffice (`soffice`) n'ont été détectés. Installez "
            "PowerPoint, ou LibreOffice comme alternative gratuite/portable "
            "(cf. README, section Installation)."
        )
    return "Impossible de générer le rendu, tous les moteurs disponibles ont échoué — " + " ; ".join(erreurs)


def rendre_pngs(pptx_path: Path, out_dir: Path, slides=None, moteur: str = None) -> dict:
    """Exporte chaque slide du PPTX en PNG. Sans `moteur` imposé (1re
    tentative, rendu de vérification) : essaie PowerPoint (référence
    visuelle la plus fidèle), bascule automatiquement sur LibreOffice en cas
    d'échec ou d'indisponibilité. Avec `moteur` imposé (ex. export PDF, qui
    doit réutiliser le moteur déjà validé) : ce moteur précis est utilisé,
    sans bascule — l'appel échoue explicitement s'il ne fonctionne pas.

    Retourne {"moteur": "powerpoint"|"libreoffice", "pngs": [Path, ...]}.
    Lève RuntimeError si aucun moteur n'a pu produire le rendu."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if moteur == MOTEUR_POWERPOINT:
        return {"moteur": MOTEUR_POWERPOINT, "pngs": _rendre_pngs_powerpoint(pptx_path, out_dir, slides)}
    if moteur == MOTEUR_LIBREOFFICE:
        return {"moteur": MOTEUR_LIBREOFFICE, "pngs": _rendre_pngs_libreoffice(pptx_path, out_dir, slides)}
    if moteur is not None:
        raise ValueError(f"Moteur de rendu inconnu : {moteur!r}")

    erreurs = []
    if powerpoint_disponible():
        try:
            return {"moteur": MOTEUR_POWERPOINT, "pngs": _rendre_pngs_powerpoint(pptx_path, out_dir, slides)}
        except Exception as e:
            erreurs.append(f"PowerPoint : {e}")
    if libreoffice_disponible():
        try:
            return {"moteur": MOTEUR_LIBREOFFICE, "pngs": _rendre_pngs_libreoffice(pptx_path, out_dir, slides)}
        except Exception as e:
            erreurs.append(f"LibreOffice : {e}")
    raise RuntimeError(_erreur_aucun_moteur(erreurs))


def exporter_pdf(pptx_path: Path, moteur: str = None) -> dict:
    """Export PDF — UNIQUEMENT après accord explicite de l'utilisateur sur
    le rapport de vérification (appelé par l'UI seulement à ce moment-là,
    jamais automatiquement). Mêmes règles de bascule que `rendre_pngs` :
    avec `moteur` imposé (toujours le cas en pratique — cf. règle absolue
    de module, réutiliser le moteur du rendu de vérification déjà validé),
    aucune bascule n'est tentée en cas d'échec.

    Retourne {"moteur": ..., "pdf": Path} — chemin à côté du PPTX, même nom
    (convention `Plan de validation LDxxxxx-Rxx.pdf`)."""
    if moteur == MOTEUR_POWERPOINT:
        return {"moteur": MOTEUR_POWERPOINT, "pdf": _exporter_pdf_powerpoint(pptx_path)}
    if moteur == MOTEUR_LIBREOFFICE:
        return {"moteur": MOTEUR_LIBREOFFICE, "pdf": _exporter_pdf_libreoffice(pptx_path)}
    if moteur is not None:
        raise ValueError(f"Moteur de rendu inconnu : {moteur!r}")

    erreurs = []
    if powerpoint_disponible():
        try:
            return {"moteur": MOTEUR_POWERPOINT, "pdf": _exporter_pdf_powerpoint(pptx_path)}
        except Exception as e:
            erreurs.append(f"PowerPoint : {e}")
    if libreoffice_disponible():
        try:
            return {"moteur": MOTEUR_LIBREOFFICE, "pdf": _exporter_pdf_libreoffice(pptx_path)}
        except Exception as e:
            erreurs.append(f"LibreOffice : {e}")
    raise RuntimeError(_erreur_aucun_moteur(erreurs))
