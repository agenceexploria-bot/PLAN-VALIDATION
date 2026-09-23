# -*- coding: utf-8 -*-
"""mcp_server/tools/inventaire.py — outil MCP `inventaire_pdf` (étape 1)."""
from typing import Any

import fitz

from core import extract

from .. import fichiers, pdf_cache, util


def inventaire_pdf(*, pdf_id: str | None = None, pdf_base64: str | None = None) -> dict[str, Any]:
    """Inventaire d'un PDF de plan fabricant (étape 1 du pipeline, à appeler
    UNE SEULE FOIS par pipeline). Retourne le nombre de pages, la taille de
    chaque page en points PDF (page_size_pts) et un aperçu basse résolution
    (100 dpi) de chaque page — pour décrire le contenu à l'utilisateur et
    proposer un rôle par page (garde/vue 3D, planche dessin cotée, page qui
    alimente les specs, ou ignorée) AVANT toute extraction détaillée.
    Signale aussi les pages sans couche texte exploitable (PDF scanné
    probable), où aucune traduction/rédaction automatique ne sera possible.

    Fournissez EXACTEMENT UN des deux :
      - `pdf_id` (chemin NORMAL) : téléversez d'abord le PDF via
        `POST /pdfs` (multipart, champ `pdf` — PAS le canal MCP, donc pas de
        base64 à produire), typiquement en exécutant une commande curl dans
        votre bac à sable, puis passez ici le `pdf_id` retourné.
      - `pdf_base64` (repli, petits fichiers / tests directs uniquement) :
        contenu du PDF encodé en base64 (50 Mo max).

    Retourne `pdf_id` dans tous les cas (celui fourni, ou un nouveau si vous
    avez utilisé `pdf_base64`) : NE PAS redécoder ni retransmettre le PDF en
    base64 par la suite — passez ce `pdf_id` tel quel à `extraire_page` pour
    CHAQUE page retenue. Objectif : éviter de renvoyer le PDF complet à
    chaque appel (impossible ou trop coûteux pour un agent réel sur un plan
    de plusieurs pages) ; seul l'upload initial transporte le contenu
    complet.

    Chaque aperçu est une URL de téléchargement à usage unique (5 min de
    durée de vie), pas du contenu inline : le protocole MCP (streamable-http)
    limite la taille d'une réponse d'outil à 1 Mio côté client, dépassée dès
    quelques pages en base64. Récupérez chaque `apercu_url` par un GET simple.
    """
    contenu, pdf_id = util.resoudre_pdf(pdf_base64, pdf_id)
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

        return {
            "pdf_id": pdf_id,
            "pdf_id_expire_dans_s": pdf_cache.DUREE_VIE_SECONDES,
            "n_pages": len(tailles),
            "pages": pages,
        }
