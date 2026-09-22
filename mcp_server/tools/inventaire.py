# -*- coding: utf-8 -*-
"""mcp_server/tools/inventaire.py — outil MCP `inventaire_pdf` (étape 1)."""
from typing import Any

import fitz

from core import extract

from .. import fichiers, util


def inventaire_pdf(pdf_base64: str) -> dict[str, Any]:
    """Inventaire d'un PDF de plan fabricant (étape 1 du pipeline). Reçoit le
    PDF en base64 (50 Mo max), retourne le nombre de pages, la taille de
    chaque page en points PDF (page_size_pts) et un aperçu basse résolution
    (100 dpi) de chaque page — pour décrire le contenu à l'utilisateur et
    proposer un rôle par page (garde/vue 3D, planche dessin cotée, page qui
    alimente les specs, ou ignorée) AVANT toute extraction détaillée.
    Signale aussi les pages sans couche texte exploitable (PDF scanné
    probable), où aucune traduction/rédaction automatique ne sera possible.

    Chaque aperçu est une URL de téléchargement à usage unique (5 min de
    durée de vie), pas du contenu inline : le protocole MCP (streamable-http)
    limite la taille d'une réponse d'outil à 1 Mio côté client, dépassée dès
    quelques pages en base64. Récupérez chaque `apercu_url` par un GET simple.
    """
    contenu = util.decoder_pdf(pdf_base64)
    with util.workdir_temporaire() as wd:
        pdf_path = wd / "plan_fabricant.pdf"
        pdf_path.write_bytes(contenu)

        with fitz.open(pdf_path) as doc:
            tailles = [[round(p.rect.width, 1), round(p.rect.height, 1)] for p in doc]
        apercus = extract.rendre_apercus(pdf_path, dpi=100)
        pages_sans_texte = set(extract.pages_sans_texte(pdf_path))

        pages = []
        for i in range(len(tailles)):
            apercu_path = wd / f"apercu_{i + 1}.png"
            apercu_path.write_bytes(apercus[i])
            publication = fichiers.publier(apercu_path, apercu_path.name, "image/png")
            pages.append({
                "page_num": i + 1,
                "page_size_pts": tailles[i],
                "apercu_url": publication["url"],
                "apercu_sha256": publication["sha256"],
                "sans_couche_texte": (i + 1) in pages_sans_texte,
            })

        return {"n_pages": len(tailles), "pages": pages}
