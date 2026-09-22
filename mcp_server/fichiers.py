# -*- coding: utf-8 -*-
"""
mcp_server/fichiers.py — publication de fichiers volumineux (PPTX, images
pleine résolution, PDF) par URL à durée de vie courte, plutôt qu'en base64
inline dans une réponse d'outil MCP.

Pourquoi : le SDK MCP officiel (streamable-http, mcp>=2.0) plafonne à 1 Mio
la taille d'un évènement SSE côté CLIENT (`httpx2._config.DEFAULT_MAX_EVENT_SIZE_BYTES`),
sans paramètre exposé par `mcp.client.streamable_http` pour l'augmenter. Un
PPTX réel (~3-5 Mo, ~5-7 Mo une fois en base64) ou plusieurs images de
vérification dans une seule réponse dépassent systématiquement cette limite
— constaté en testant le VRAI protocole MCP de bout en bout (pas seulement
les fonctions Python), cf. tests/mcp/test_server_http.py : `inventaire_pdf`
sur un PDF de 2 pages échouait déjà (« SSE stream ended without a
response »). Les outils renvoient donc une URL de téléchargement (+ taille,
sha256) pour tout contenu binaire, jamais le contenu lui-même.

Modèle de sécurité — URL à capacité (« capability URL »), comme une URL S3
pré-signée : un jeton aléatoire imprévisible (secrets.token_urlsafe, 256
bits) sert de clé unique vers UN SEUL fichier, dans un registre tenu en
mémoire par CE process. Aucune information du jeton n'encode de chemin :
impossible de construire ou deviner un jeton menant au fichier d'un autre
appel — chaque publication a son propre jeton, indépendant de tout autre
appel ou utilisateur. Le fichier est supprimé du disque ET du registre dès
le premier téléchargement réussi (usage unique — un deuxième GET sur le
même lien échoue), ou après DUREE_VIE_SECONDES s'il n'a jamais été récupéré
(balayage paresseux à chaque nouvelle publication, même principe que
core/nettoyage.py côté app Streamlit : pas de thread ni de tâche périodique
séparée). En complément, `purger_dossiers_orphelins_au_demarrage()` nettoie
au lancement du process tout dossier de publication laissé par une instance
précédente qui aurait redémarré sans arrêt propre (le registre, en mémoire,
ne survit jamais à un redémarrage — cf. son docstring).

⚠️ Limite connue, acceptée telle quelle (cf. mcp_server/README.md) : le
jeton est invalidé et le fichier supprimé du disque dès que le téléchargement
COMMENCE côté serveur, pas une fois confirmé reçu en entier par le client.
Une connexion coupée en cours de transfert rend donc le lien définitivement
inutilisable — l'agent doit relancer l'outil qui l'a produit, pas
retélécharger le même lien. Corriger ça proprement demanderait soit
d'affaiblir l'usage unique (fenêtre de re-essai), soit un mécanisme de
confirmation de réception que HTTP ne donne pas simplement — pas fait ici.

Cette route (cf. mcp_server/server.py) est volontairement EXEMPTÉE du jeton
Bearer MCP_AUTH_TOKEN (mcp_server/auth.py) : le lien est destiné à être
montré et cliqué directement par l'UTILISATEUR FINAL dans son navigateur
(relayé par l'agent Dust), qui n'a et ne doit jamais recevoir le jeton
Bearer serveur-à-serveur. Sa sécurité vient du jeton de capacité lui-même
(imprévisible, à usage unique, courte durée de vie), pas d'une
authentification séparée — exactement comme une URL de téléchargement S3
pré-signée n'exige pas non plus d'en-tête d'authentification à part.
"""
import hashlib
import os
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path

DUREE_VIE_SECONDES = 5 * 60
# Seuil du nettoyage au démarrage — nettement plus long que DUREE_VIE_SECONDES
# (cf. purger_dossiers_orphelins_au_demarrage : pourquoi un simple "présent
# au démarrage = orphelin" est FAUX).
SEUIL_ORPHELIN_SECONDES = 15 * 60
PREFIXE_ROUTE = "/fichiers"
PREFIXE_DOSSIER = "mcp_plan_validation_publies_"


def purger_dossiers_orphelins_au_demarrage(seuil_secondes: float = SEUIL_ORPHELIN_SECONDES) -> int:
    """À appeler UNE fois au démarrage du process, AVANT toute publication.
    Le registre (_REGISTRE) vit en mémoire : il ne survit jamais à un
    redémarrage du process (crash, OOM-kill Render, redéploiement — déjà
    observé sur ce projet) — un dossier `PREFIXE_DOSSIER*` laissé par une
    instance PRÉCÉDENTE est orphelin, aucun jeton du process courant ne peut
    plus y mener.

    ⚠️ Mais « présent au démarrage » ne veut PAS dire « forcément d'une
    instance précédente » : le même répertoire temporaire système peut être
    partagé par un AUTRE process encore vivant au même instant (constaté en
    testant ce module : le process pytest et le process serveur qu'il lance
    en sous-processus pour tests/mcp/test_server_http.py coexistent tous les
    deux, chacun avec son propre `_DOSSIER` sous le même préfixe — un
    nettoyage inconditionnel supprimait le dossier ENCORE ACTIF de l'autre).
    Un dossier plus récent que `seuil_secondes` n'est donc PAS touché : le
    seuil, nettement plus long que DUREE_VIE_SECONDES, laisse largement le
    temps à toute autre instance légitimement en train de démarrer de
    publier son premier fichier sans risquer d'être vue comme orpheline.

    Retourne le nombre de dossiers supprimés. Ne lève jamais : un échec de
    purge ne doit pas empêcher le serveur de démarrer."""
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
    """Supprime les entrées jamais téléchargées dont la durée de vie a
    expiré. Appelé sous _VERROU, à chaque publication."""
    maintenant = time.monotonic()
    expires = [jeton for jeton, info in _REGISTRE.items() if info["expire_a"] <= maintenant]
    for jeton in expires:
        info = _REGISTRE.pop(jeton, None)
        if info:
            info["chemin"].unlink(missing_ok=True)


def base_url_publique() -> str:
    """URL de base publique du serveur, pour construire des liens de
    téléchargement absolus utilisables par Dust/l'utilisateur final. Ordre :
      1. MCP_PUBLIC_BASE_URL — réglage explicite, prioritaire, pour tout
         déploiement hors Render (ou pour surcharger Render si besoin) ;
      2. RENDER_EXTERNAL_URL — renseignée AUTOMATIQUEMENT par Render pour
         tout service web (cf. mcp_server/README.md) : évite l'œuf-et-la-
         poule de devoir connaître l'URL Render avant le premier déploiement ;
      3. http://127.0.0.1:{PORT} — repli pour les tests locaux UNIQUEMENT,
         jamais valable en production (sans (1) ni (2), les liens générés
         sont inutilisables par un client distant)."""
    configuree = os.environ.get("MCP_PUBLIC_BASE_URL", "").strip()
    if configuree:
        return configuree.rstrip("/")
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if render_url:
        return render_url.rstrip("/")
    port = os.environ.get("PORT", "8000")
    return f"http://127.0.0.1:{port}"


def publier(chemin_source: Path, nom_fichier: str, content_type: str) -> dict:
    """Copie `chemin_source` dans le dossier de publication sous un nom de
    fichier imprévisible et retourne {"url", "sha256", "octets",
    "expire_dans_s"}. La copie publiée a son propre cycle de vie,
    indépendant du nettoyage de fin d'appel (cf.
    mcp_server/util.workdir_temporaire) — le fichier D'ORIGINE peut être
    supprimé juste après sans affecter le téléchargement."""
    contenu = Path(chemin_source).read_bytes()
    sha256 = hashlib.sha256(contenu).hexdigest()

    with _VERROU:
        _purger_expires()
        jeton = secrets.token_urlsafe(32)
        chemin_publie = _DOSSIER / jeton
        chemin_publie.write_bytes(contenu)
        _REGISTRE[jeton] = {
            "chemin": chemin_publie,
            "expire_a": time.monotonic() + DUREE_VIE_SECONDES,
            "content_type": content_type,
            "nom_fichier": nom_fichier,
        }

    return {
        "url": f"{base_url_publique()}{PREFIXE_ROUTE}/{jeton}",
        "sha256": sha256,
        "octets": len(contenu),
        "expire_dans_s": DUREE_VIE_SECONDES,
    }


def recuperer_et_invalider(jeton: str) -> dict | None:
    """Retourne {"contenu", "content_type", "nom_fichier"} pour un jeton
    valide et le retire IMMÉDIATEMENT du disque et du registre (usage
    unique) — un deuxième appel avec le même jeton retourne None, comme un
    jeton inconnu ou expiré (même comportement observable dans les trois
    cas : jamais d'indice sur ce qui a précisément échoué)."""
    with _VERROU:
        _purger_expires()
        info = _REGISTRE.pop(jeton, None)
    if info is None:
        return None
    chemin = info["chemin"]
    try:
        contenu = chemin.read_bytes()
    except OSError:
        return None
    finally:
        chemin.unlink(missing_ok=True)
    return {"contenu": contenu, "content_type": info["content_type"], "nom_fichier": info["nom_fichier"]}
