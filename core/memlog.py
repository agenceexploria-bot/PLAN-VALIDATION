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
