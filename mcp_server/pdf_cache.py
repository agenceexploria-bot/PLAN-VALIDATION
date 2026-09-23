# -*- coding: utf-8 -*-
"""
mcp_server/pdf_cache.py — cache serveur du PDF fabricant en cours de
traitement, pour que l'agent (Dust) n'ait PAS à faire transiter le PDF en
base64 par le canal MCP à chaque outil. Deux problèmes distincts, trouvés
l'un après l'autre en conditions réelles avec un agent Dust :

1. (Résolu par ce module dès sa première version) `extraire_page` exigeait
   le PDF complet en base64 à CHAQUE appel, une fois PAR PAGE retenue — au-
   delà de quelques pages, l'agent jugeait le volume trop lourd et
   improvisait des contournements dangereux (rastérisation basse
   résolution, OCR local, découpage manuel) plutôt que d'appeler l'outil
   normalement.
2. (Plus grave, révélé ensuite par un vrai essai utilisateur) même l'envoi
   UNIQUE du PDF complet en base64 à `inventaire_pdf` s'est avéré IMPOSSIBLE
   pour un agent Dust réel : rien, dans son environnement, ne lui permet de
   produire un blob base64 à partir d'un fichier joint en conversation et de
   l'injecter dans un argument d'outil MCP. L'agent a fini par improviser un
   PPTX entièrement hors pipeline (pdfplumber + python-pptx maison) plutôt
   que d'utiliser nos outils.

D'où le point d'entrée HTTP direct `POST {PREFIXE_ROUTE}` (cf.
`mcp_server/server.py::televerser_pdf`) : un agent Dust peut le lister comme
une commande à exécuter dans son bac à sable (ex. `curl -F pdf=@fichier.pdf
...`) plutôt que de manipuler du base64 lui-même. HORS du canal MCP
(streamable-http) donc PAS soumis à sa limite ~1 Mio, et PROTÉGÉ par le même
jeton Bearer que `/mcp` (serveur-à-serveur, jamais exposé à l'utilisateur
final — contrairement à `mcp_server/fichiers.py`).

`inventaire_pdf` (étape 1) accepte soit ce `pdf_id` téléversé, soit
`pdf_base64` en repli (petits fichiers, tests directs) — cf.
`mcp_server/util.py::resoudre_pdf`. `extraire_page` (étape 2, appelée une
fois par page) référence le PDF par `pdf_id` au lieu de le retransmettre.
Décision assumée : ceci réintroduit un état serveur entre deux appels MCP,
ce que `mcp_server/util.py` excluait explicitement jusqu'ici — accepté en
connaissance de cause car les deux problèmes observés sont plus coûteux que
la simplicité du « sans état ».

Contrairement à `mcp_server/fichiers.py` (sortie, jeton à usage UNIQUE) :
une entrée ICI est réutilisable (une lecture par page extraite) et sa durée
de vie est PROLONGÉE à chaque lecture (expiration glissante) — un pipeline
sur un plan de plusieurs dizaines de pages peut s'étaler sur plusieurs
minutes d'échanges avec l'agent, entre `inventaire_pdf` et la dernière page
extraite.
"""
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

DUREE_VIE_SECONDES = 30 * 60
# Seuil du nettoyage au démarrage — nettement plus long que DUREE_VIE_SECONDES,
# même raison que fichiers.purger_dossiers_orphelins_au_demarrage (ne pas
# supprimer le dossier encore actif d'une autre instance qui démarre juste).
SEUIL_ORPHELIN_SECONDES = 45 * 60
PREFIXE_DOSSIER = "mcp_plan_validation_pdfs_"
# Route HTTP d'upload direct (POST multipart, hors canal MCP) — protégée par
# le jeton Bearer serveur-à-serveur (contrairement à fichiers.PREFIXE_ROUTE).
PREFIXE_ROUTE = "/pdfs"


def purger_dossiers_orphelins_au_demarrage(seuil_secondes: float = SEUIL_ORPHELIN_SECONDES) -> int:
    """À appeler UNE fois au démarrage du process, AVANT toute mise en
    cache — même logique que `fichiers.purger_dossiers_orphelins_au_demarrage`
    (registre en mémoire, ne survit jamais à un redémarrage), registre et
    préfixe de dossier distincts. Ne lève jamais."""
    racine = Path(tempfile.gettempdir())
    seuil = time.time() - seuil_secondes
    try:
        candidats = [p for p in racine.iterdir()
                     if p.name.startswith(PREFIXE_DOSSIER) and p.is_dir() and not p.is_symlink()]
    except OSError:
        return 0
    supprimes = 0
    for dossier in candidats:
        try:
            if dossier.stat().st_mtime < seuil:
                shutil.rmtree(dossier, ignore_errors=True)
                supprimes += 1
        except OSError:
            pass
    return supprimes


purger_dossiers_orphelins_au_demarrage()
_DOSSIER = Path(tempfile.mkdtemp(prefix=PREFIXE_DOSSIER))
_VERROU = threading.Lock()
_REGISTRE: dict[str, dict] = {}


def _purger_expires() -> None:
    """Supprime les entrées dont la durée de vie a expiré. Appelé sous
    _VERROU, à chaque mise en cache ou lecture."""
    maintenant = time.monotonic()
    expires = [pdf_id for pdf_id, info in _REGISTRE.items() if info["expire_a"] <= maintenant]
    for pdf_id in expires:
        info = _REGISTRE.pop(pdf_id, None)
        if info:
            info["chemin"].unlink(missing_ok=True)


def mettre_en_cache(contenu: bytes) -> dict:
    """Écrit `contenu` (déjà décodé/validé, cf. util.decoder_pdf) sur disque
    sous un identifiant imprévisible et retourne {"pdf_id", "expire_dans_s"}.
    Appelé UNE fois par pipeline, par `inventaire_pdf`."""
    with _VERROU:
        _purger_expires()
        pdf_id = secrets.token_urlsafe(32)
        chemin = _DOSSIER / pdf_id
        chemin.write_bytes(contenu)
        _REGISTRE[pdf_id] = {"chemin": chemin, "expire_a": time.monotonic() + DUREE_VIE_SECONDES}
    return {"pdf_id": pdf_id, "expire_dans_s": DUREE_VIE_SECONDES}


def recuperer(pdf_id: str) -> bytes | None:
    """Retourne les octets du PDF pour `pdf_id`, ou None si inconnu/expiré.
    RÉUTILISABLE (pas d'usage unique, contrairement à `fichiers.py`) et
    PROLONGE la durée de vie de l'entrée à chaque appel (expiration
    glissante) : tant que l'agent continue d'extraire des pages, le PDF
    reste disponible."""
    with _VERROU:
        _purger_expires()
        info = _REGISTRE.get(pdf_id)
        if info is None:
            return None
        info["expire_a"] = time.monotonic() + DUREE_VIE_SECONDES
        chemin = info["chemin"]
    try:
        return chemin.read_bytes()
    except OSError:
        return None
