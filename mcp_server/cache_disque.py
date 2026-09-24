# -*- coding: utf-8 -*-
"""
mcp_server/cache_disque.py — cache serveur, SUR DISQUE, de fichiers binaires
produits par un outil et consommés par un outil suivant : images de planche
et de vue 3D (`extraire_page` -> `assembler_pptx`) et PPTX assemblé
(`assembler_pptx` -> `verifier_rendu` / `exporter_pdf`).

Troisième occurrence de la même famille de problème que le PDF fabricant
(mcp_server/pdf_cache.py) et les mots extraits (mcp_server/extraction_cache.py) :
`assembler_pptx` exigeait l'image de planche en base64 — une planche A3 à
300 dpi (4961×3508 px) représentait ~165k jetons pour un agent Dust réel
disposant de ~48k, qui a échoué. Même principe, appliqué cette fois à TOUS
les blobs restants en entrée : le serveur garde le fichier et l'outil suivant
le reçoit par identifiant.

Même mécanisme que pdf_cache (disque, identifiant imprévisible, réutilisable,
expiration glissante de 30 min, purge au démarrage des dossiers orphelins
d'une instance précédente), en classe pour servir à deux registres
distincts. Sur disque plutôt qu'en mémoire : quelques Mo par image ou par
PPTX, sur un palier Render à 512 Mo déjà sujet aux OOM-kills.
"""
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

DUREE_VIE_SECONDES = 30 * 60
# Nettement plus long que DUREE_VIE_SECONDES, même raison que
# fichiers.purger_dossiers_orphelins_au_demarrage (ne pas supprimer le dossier
# encore actif d'une autre instance qui démarre juste).
SEUIL_ORPHELIN_SECONDES = 45 * 60


class CacheDisque:
    def __init__(self, prefixe_dossier: str):
        self._prefixe = prefixe_dossier
        self._purger_orphelins()
        self._dossier = Path(tempfile.mkdtemp(prefix=prefixe_dossier))
        self._verrou = threading.Lock()
        self._registre: dict[str, dict] = {}

    def _purger_orphelins(self) -> None:
        """Supprime les dossiers de même préfixe laissés par une instance
        précédente (registre en mémoire, jamais survivant à un redémarrage).
        Ne lève jamais."""
        seuil = time.time() - SEUIL_ORPHELIN_SECONDES
        try:
            candidats = [p for p in Path(tempfile.gettempdir()).iterdir()
                         if p.name.startswith(self._prefixe) and p.is_dir() and not p.is_symlink()]
        except OSError:
            return
        for dossier in candidats:
            try:
                if dossier.stat().st_mtime < seuil:
                    shutil.rmtree(dossier, ignore_errors=True)
            except OSError:
                pass

    def _purger_expires(self) -> None:
        """Appelé sous self._verrou."""
        maintenant = time.monotonic()
        for ident in [i for i, info in self._registre.items() if info["expire_a"] <= maintenant]:
            self._registre.pop(ident)["chemin"].unlink(missing_ok=True)

    def mettre_en_cache(self, contenu: bytes) -> str:
        """Écrit `contenu` sur disque et retourne son identifiant."""
        with self._verrou:
            self._purger_expires()
            ident = secrets.token_urlsafe(32)
            chemin = self._dossier / ident
            chemin.write_bytes(contenu)
            self._registre[ident] = {"chemin": chemin, "expire_a": time.monotonic() + DUREE_VIE_SECONDES}
        return ident

    def prolonger(self, ident: str) -> None:
        """Repousse l'expiration de `ident` s'il existe encore."""
        with self._verrou:
            info = self._registre.get(ident)
            if info is not None:
                info["expire_a"] = time.monotonic() + DUREE_VIE_SECONDES

    def recuperer(self, ident: str) -> bytes | None:
        """Octets de `ident`, ou None si inconnu/expiré. Réutilisable et
        prolonge l'expiration à chaque lecture."""
        with self._verrou:
            self._purger_expires()
            info = self._registre.get(ident)
            if info is None:
                return None
            info["expire_a"] = time.monotonic() + DUREE_VIE_SECONDES
            chemin = info["chemin"]
        try:
            return chemin.read_bytes()
        except OSError:
            return None


IMAGES = CacheDisque("mcp_plan_validation_images_")
PPTX = CacheDisque("mcp_plan_validation_pptx_")
