# -*- coding: utf-8 -*-
"""
mcp_server/extraction_cache.py — cache serveur des données de mots extraits
d'une page (`words_data`, sortie de `extraire_page`), pour que l'agent
(Dust) n'ait PAS à RETRANSMETTRE ce JSON en argument des outils suivants
(`traduire_mots`, `verifier_rendu`). Même famille de problème que le PDF
fabricant (cf. mcp_server/pdf_cache.py, mêmes causes) : confirmé en
conditions réelles, un agent Dust a buté en tentant de relayer le
words_data complet d'une page dense à `traduire_mots` (échec sans message
exploitable, cf. mcp_server/tools/traduction.py), et commençait à le
reconstruire manuellement plutôt que de le relayer tel quel — même dérive
que celle vue sur le PDF.

`extraire_page` (étape 2) met en cache son `words_data` ICI et retourne un
`extraction_id` opaque, EN PLUS du contenu inline (`words`,
`page_size_pts` — conservés tels quels : ce n'est PAS leur réception qui
posait problème, seulement leur RETRANSMISSION comme argument d'un appel
suivant). `traduire_mots` (étape 3) et `verifier_rendu` (étape 5,
`words_par_page`, qui AGRÈGE plusieurs pages — pire cas) l'acceptent à la
place du JSON complet — cf. `mcp_server/util.py::resoudre_words_data`.

Contrairement à `mcp_server/pdf_cache.py` : gardé EN MÉMOIRE, jamais sur
disque. `words_data` (JSON structuré, quelques centaines de Ko au pire pour
une page dense) est sans commune mesure avec un PDF (jusqu'à 50 Mo,
LIMITE_PDF_MO) — l'aller-retour disque n'apporte rien ici. Conséquence :
rien à purger au démarrage (aucun fichier laissé sur disque par une
instance précédente, contrairement à pdf_cache/fichiers) ; un redémarrage
du process vide simplement le registre.

Une entrée porte aussi, pour `assembler_pptx` (qui reçoit une planche ou la
vue 3D par `extraction_id`, jamais en base64 — cf. mcp_server/cache_disque.py) :
les images de la page (identifiants dans `cache_disque.IMAGES`, les octets
restent SUR DISQUE) et les labels produits par `traduire_mots` sur cette
page. Lire l'entrée prolonge aussi ses images : elles vivent aussi
longtemps que l'extraction qui les référence.
"""
import secrets
import threading
import time

from . import cache_disque

DUREE_VIE_SECONDES = 30 * 60


_VERROU = threading.Lock()
_REGISTRE: dict[str, dict] = {}


def _purger_expires() -> None:
    """Supprime les entrées dont la durée de vie a expiré. Appelé sous
    _VERROU, à chaque mise en cache ou lecture."""
    maintenant = time.monotonic()
    expires = [extraction_id for extraction_id, info in _REGISTRE.items() if info["expire_a"] <= maintenant]
    for extraction_id in expires:
        _REGISTRE.pop(extraction_id, None)


def mettre_en_cache(words_data: dict, images: dict[str, bytes] | None = None) -> dict:
    """Garde `words_data` en mémoire sous un identifiant imprévisible et
    retourne {"extraction_id", "expire_dans_s"}. Appelé par `extraire_page`
    pour chaque page extraite. `images` ({"planche"|"vue_3d": octets PNG})
    est écrit dans `cache_disque.IMAGES`, rattaché à cette extraction."""
    ids_images = {nom: cache_disque.IMAGES.mettre_en_cache(contenu) for nom, contenu in (images or {}).items()}
    with _VERROU:
        _purger_expires()
        extraction_id = secrets.token_urlsafe(32)
        _REGISTRE[extraction_id] = {
            "donnees": words_data, "images": ids_images, "labels": None,
            "expire_a": time.monotonic() + DUREE_VIE_SECONDES,
        }
    return {"extraction_id": extraction_id, "expire_dans_s": DUREE_VIE_SECONDES}


def _entree(extraction_id: str) -> dict | None:
    """Entrée vivante pour `extraction_id` (expiration prolongée, images
    comprises), ou None si inconnue/expirée."""
    with _VERROU:
        _purger_expires()
        info = _REGISTRE.get(extraction_id)
        if info is None:
            return None
        info["expire_a"] = time.monotonic() + DUREE_VIE_SECONDES
    for ident in info["images"].values():
        cache_disque.IMAGES.prolonger(ident)
    return info


def recuperer(extraction_id: str) -> dict | None:
    """Retourne `words_data` pour `extraction_id`, ou None si inconnu/expiré.
    RÉUTILISABLE (pas d'usage unique) et PROLONGE la durée de vie de
    l'entrée à chaque appel (expiration glissante), même principe que
    `mcp_server/pdf_cache.py`."""
    info = _entree(extraction_id)
    return None if info is None else info["donnees"]


def recuperer_image(extraction_id: str, nom: str) -> bytes | None:
    """Octets PNG de l'image `nom` ("planche"|"vue_3d") de cette extraction,
    ou None si l'extraction est inconnue/expirée ou n'a pas produit cette
    image."""
    info = _entree(extraction_id)
    if info is None or nom not in info["images"]:
        return None
    return cache_disque.IMAGES.recuperer(info["images"][nom])


def enregistrer_labels(extraction_id: str, labels: list) -> None:
    """Rattache à l'extraction les labels produits par `traduire_mots`, pour
    qu'`assembler_pptx` les reprenne sans que l'agent les retransmette."""
    info = _entree(extraction_id)
    if info is not None:
        info["labels"] = labels


def recuperer_labels(extraction_id: str) -> list | None:
    """Labels enregistrés par `traduire_mots` pour cette extraction, ou None
    si `traduire_mots` n'a pas (encore) été appelé dessus."""
    info = _entree(extraction_id)
    return None if info is None else info["labels"]
