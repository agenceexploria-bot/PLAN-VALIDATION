# -*- coding: utf-8 -*-
"""
core/nettoyage.py — Purge des dossiers de travail de session périmés.

Chaque session Streamlit crée un dossier temporaire `plan_validation_*`
(PDF fabricant, PPTX, rendus de vérification, PDF final) que rien ne
supprime : Streamlit n'offre pas de hook fiable de fin de session, et
supprimer les fichiers dès le téléchargement casserait le bouton (il
relit le fichier à chaque rerun). Sur le disque éphémère de Render, ces
dossiers s'accumulent donc entre utilisateurs.

Plutôt qu'un hook de fin de session, on balaie au DÉMARRAGE de chaque
nouvelle session les dossiers plus vieux que `ttl_heures` (dernière
modification, fichiers compris : une session active les rafraîchit).
"""
import shutil
import tempfile
import time
from pathlib import Path

PREFIXE = "plan_validation_"


def _derniere_modif(dossier: Path) -> float:
    """Date de dernière modification la plus récente du dossier ou de l'un
    de ses fichiers (le mtime du dossier seul ignore les sous-dossiers)."""
    plus_recent = dossier.stat().st_mtime
    for f in dossier.rglob("*"):
        try:
            plus_recent = max(plus_recent, f.stat().st_mtime)
        except OSError:
            pass  # fichier supprimé entre-temps par une autre session
    return plus_recent


def purger_dossiers_perimes(ttl_heures: float = 3, racine: Path = None, maintenant: float = None) -> int:
    """Supprime les dossiers `plan_validation_*` de `racine` (défaut : le
    répertoire temporaire système) inactifs depuis plus de `ttl_heures`.
    Retourne le nombre de dossiers supprimés. Ne touche jamais un lien
    symbolique ni un élément hors préfixe ; ne lève jamais d'exception
    (un échec de purge ne doit pas empêcher l'app de démarrer)."""
    racine = Path(racine) if racine else Path(tempfile.gettempdir())
    maintenant = time.time() if maintenant is None else maintenant
    seuil = maintenant - ttl_heures * 3600
    supprimes = 0
    try:
        candidats = [p for p in racine.iterdir()
                     if p.name.startswith(PREFIXE) and p.is_dir() and not p.is_symlink()]
    except OSError:
        return 0
    for dossier in candidats:
        try:
            if _derniere_modif(dossier) < seuil:
                shutil.rmtree(dossier)
                supprimes += 1
        except OSError:
            pass
    return supprimes
