# -*- coding: utf-8 -*-
"""
core/memlog.py — Point de mesure mémoire pour diagnostiquer l'OOM constaté
sur le palier gratuit Render (512 Mo RAM, cf. audit de session). Appelé à
la fin de chaque étape majeure du pipeline (extraction, traduction,
assemblage, rendu) : logge le PIC mémoire résidente du process depuis son
démarrage — pas un simple instantané au moment de l'appel, qui sous-
estimerait un pic transitoire déjà redescendu (le GC libère les objets
Python, mais l'OS ne récupère pas forcément la RAM tout de suite, ni
l'inverse : mesurer seulement l'instantané raterait un pic entre deux
mesures).

`ru_maxrss` (Linux — la cible réelle de déploiement, cf. Dockerfile) est
monotone croissant depuis le démarrage du process : c'est la mesure la plus
fiable pour ce diagnostic. `peak_wset` (Windows, poste de dev) en est
l'équivalent pour tourner ce diagnostic en local.

Utilise un simple `print(..., flush=True)` plutôt que le module `logging` :
toujours visible dans les logs Render tel quel, sans dépendre d'une config
`logging` particulière côté hébergeur.
"""
import sys
from pathlib import Path


def pic_memoire_mo() -> float:
    """Pic mémoire résidente du process depuis son démarrage, en Mo."""
    try:
        import resource
        pic = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux : ru_maxrss en Kio. macOS : en octets (pas la cible de
        # déploiement, cf. Dockerfile, mais autant ne pas fausser la mesure
        # sur un poste de dev Mac).
        return pic / (1024 * 1024) if sys.platform == "darwin" else pic / 1024
    except ImportError:
        # Windows : pas de module `resource`.
        import psutil
        return psutil.Process().memory_info().peak_wset / (1024 * 1024)


def logger_etape(nom_etape: str) -> None:
    """Point de mesure à appeler à la fin d'une étape majeure du pipeline.
    Message volontairement en ASCII pur (pas d'accents) : certains
    agrégateurs de logs mal configurés en UTF-8 les remplacent par des
    caractères illisibles, ce qui gênerait la lecture rapide dans les logs
    Render en plein diagnostic."""
    print(f"[MEMOIRE] apres etape '{nom_etape}': pic resident = {pic_memoire_mo():.1f} Mo", flush=True)


# ── Mémoire du CONTENEUR (cgroup) ───────────────────────────────────────────
# Render gratuit n'affiche pas la mémoire (Metrics réservé aux paliers
# payants) : on la lit dans le cgroup du conteneur, qui compte TOUS ses
# process — sous-process `soffice` compris, invisibles pour `ru_maxrss`.
# « pic » est cumulé depuis le démarrage du conteneur, pas par appel.
RACINE_CGROUP = Path("/sys/fs/cgroup")
# (courant, pic, limite) : cgroup v2, puis v1 (fichiers à la racine ou sous
# le contrôleur `memory/`, selon le montage).
FICHIERS_CGROUP = (
    ("memory.current", "memory.peak", "memory.max"),
    ("memory.usage_in_bytes", "memory.max_usage_in_bytes", "memory.limit_in_bytes"),
    ("memory/memory.usage_in_bytes", "memory/memory.max_usage_in_bytes", "memory/memory.limit_in_bytes"),
)
# cgroup v1 sans limite : valeur « infinie » proche de 2^63.
SEUIL_SANS_LIMITE = 1 << 60


def _lire_mo(chemin: Path) -> str:
    try:
        brut = chemin.read_text().strip()
        if brut == "max" or int(brut) >= SEUIL_SANS_LIMITE:
            return "illimitee"
        return f"{int(brut) / (1024 * 1024):.1f}"
    except (OSError, ValueError):
        return "indisponible"


def ligne_memoire_conteneur(outil: str, racine: Path = RACINE_CGROUP) -> str:
    """`[mem] <outil> courant=<Mo> pic=<Mo> limite=<Mo>` ; chaque valeur
    illisible vaut « indisponible » (Windows, autre environnement, noyau
    sans memory.peak). Aucun contenu de fichier ni identifiant."""
    for fichiers in FICHIERS_CGROUP:
        if (racine / fichiers[0]).exists():
            courant, pic, limite = (_lire_mo(racine / f) for f in fichiers)
            break
    else:
        courant = pic = limite = "indisponible"
    return f"[mem] {outil} courant={courant} pic={pic} limite={limite}"


def logger_memoire_conteneur(outil: str, racine: Path = RACINE_CGROUP) -> None:
    """À appeler après chaque travail lourd. Ne lève JAMAIS : la mesure ne
    doit pas pouvoir casser un outil."""
    try:
        print(ligne_memoire_conteneur(outil, racine), flush=True)
    except Exception:
        pass
