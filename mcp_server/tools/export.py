# -*- coding: utf-8 -*-
"""mcp_server/tools/export.py — outil MCP `exporter_pdf` (étape 6, dernière
étape, jamais automatique)."""
import hashlib
from pathlib import Path
from typing import Any

from core import render

from .. import fichiers, util


def exporter_pdf(
    pptx_base64: str = None, *, valide: bool, pptx_sha256_verifie: str = None, moteur: str = None,
    pptx_id: str = None,
) -> dict[str, Any]:
    """Export PDF final — UNIQUEMENT après validation explicite de
    l'utilisateur du rendu visuel produit par `verifier_rendu` (règle
    absolue n°3 : jamais d'export automatique ou silencieux).

    `valide` doit être True, posé par l'agent seulement après que
    l'utilisateur a lui-même confirmé, en le disant explicitement, que le
    rendu visuel (`verifier_rendu`) est conforme. `valide=False` (ou
    l'utilisateur n'a pas encore répondu) : l'appel ÉCHOUE explicitement, il
    ne renvoie jamais un PDF par défaut.

    Fournissez EXACTEMENT UN des deux : `pptx_id` (chemin NORMAL, le même
    que celui passé à `verifier_rendu`) ou `pptx_base64` (repli).

    `pptx_sha256_verifie` (fortement recommandé) : l'empreinte `pptx_sha256`
    renvoyée par `verifier_rendu`. Si fournie, DOIT correspondre à
    l'empreinte du PPTX de CET appel — sinon l'export est refusé :
    sans ce lien, rien n'empêche d'exporter un PPTX différent de celui que
    l'utilisateur a vu et validé (pas d'état côté serveur pour le vérifier
    autrement).

    `moteur` : moteur de rendu à réutiliser (celui qu'a utilisé
    `verifier_rendu`, cf. son champ `moteur`) — en pratique toujours
    LibreOffice sur ce serveur Linux (PowerPoint/COM indisponible).

    Retourne {"moteur": str, "pdf_url": str, "pdf_sha256": str,
    "nom_fichier": str}. `pdf_url` est une URL de téléchargement à usage
    unique (5 min de durée de vie), pas du contenu inline : un PDF réel
    dépasse largement la limite de 1 Mio par réponse d'outil du protocole
    MCP. Donnez ce lien à l'utilisateur pour qu'il télécharge le document
    final directement — c'est le seul document diffusé.
    """
    if not valide:
        raise ValueError(
            "Export refusé : le rapport de vérification n'a pas été validé "
            "explicitement par l'utilisateur (valide=False). Montrez-lui "
            "d'abord les images de verifier_rendu et obtenez son accord "
            "avant d'appeler exporter_pdf."
        )

    contenu = util.resoudre_pptx(pptx_base64, pptx_id)
    if pptx_sha256_verifie:
        empreinte = hashlib.sha256(contenu).hexdigest()
        if empreinte != pptx_sha256_verifie:
            raise ValueError(
                "Export refusé : le PPTX fourni ne correspond pas à celui "
                "vérifié (empreinte différente) — le rapport de vérification "
                "ne porte pas sur ce fichier. Relancez verifier_rendu sur le "
                "PPTX actuel avant d'exporter."
            )

    with util.workdir_temporaire() as wd:
        pptx_path = wd / "plan.pptx"
        pptx_path.write_bytes(contenu)
        with util.echec_rendu_explicite("exporter_pdf"):
            resultat = render.exporter_pdf(pptx_path, moteur=moteur)
        pdf_path = Path(resultat["pdf"])
        publication = fichiers.publier(pdf_path, pdf_path.name, "application/pdf")
        return {
            "moteur": resultat["moteur"],
            "pdf_url": publication["url"],
            "pdf_sha256": publication["sha256"],
            "nom_fichier": pdf_path.name,
        }
