# -*- coding: utf-8 -*-
"""mcp_server/tools/extraction.py — outil MCP `extraire_page` (étape 2)."""
from typing import Any

import fitz

from core import cover, extract

from .. import extraction_cache, fichiers, util

ROLES_VALIDES = ("planche", "garde", "specs")


def extraire_page(
    *, page_num: int, pdf_id: str | None = None, pdf_base64: str | None = None,
    dpi: int = 300, role: str = "planche",
) -> dict[str, Any]:
    """Extrait UNE page retenue du PDF fabricant (étape 2 du pipeline, une
    fois par page retenue) : rendu image et mots détectés avec bbox +
    indicateur traduisible. Cote fusionnée à un suffixe texte (ex.
    "9700(FFL)") : le nombre reste TOUJOURS visible dans l'image, seul le
    suffixe est isolé (champ `suffix_bbox`) — jamais de cote retapée de
    mémoire.

    Fournissez EXACTEMENT UN des deux : `pdf_id` (chemin NORMAL, retourné par
    `inventaire_pdf` appelé une seule fois en amont) ou `pdf_base64` (repli,
    petits fichiers / tests directs). Si `pdf_id` est inconnu ou expiré
    (cache 30 min glissantes), relancez `inventaire_pdf`. La réponse renvoie
    `pdf_id` dans tous les cas, à réutiliser pour la page suivante.

    L'image (et la vue 3D le cas échéant) est une URL de téléchargement à
    usage unique (5 min de durée de vie), pas du contenu inline : à 300 dpi
    une planche dépasse largement la limite de 1 Mio par réponse d'outil du
    protocole MCP. Récupérez-la par un GET simple, puis ré-encodez-la en
    base64 pour la fournir à `assembler_pptx` (`planches[].image_base64` /
    `view3d.image_base64`).

    La réponse est aussi mise en cache dans son intégralité côté serveur et
    identifiée par `extraction_id` (30 min glissantes) : passez CET
    identifiant, pas le JSON complet, à `traduire_mots` puis à
    `verifier_rendu` (`words_par_page`). NE RECONSTRUISEZ JAMAIS `words`
    à la main pour ces appels suivants — un agent Dust réel a buté sur la
    taille de ce JSON en tentant de le retransmettre tel quel.

    `role` adapte le traitement à la nature réelle de la page (comme le fait
    le pipeline de référence, core/extract.py + core/cover.py) :
      - "planche" (défaut) : planche dessin cotée — rédaction des libellés
        traduisibles ET des cotes fusionnées à suffixe ; `image_url` pointe
        vers l'image RÉDIGÉE ;
      - "garde" : page de garde / vue 3D fournisseur — AUCUNE rédaction (rien
        n'y est traduit en place) ; la vue 3D est en plus recadrée SANS
        rognage (`vue_3d_url` + `vue_3d_page_w_pt`) ;
      - "specs" : page qui alimente le tableau specs — aucune image générée
        (seul le texte est utile), `image_url` absent de la réponse.

    ⚠️ Piège réel (vu sur un plan fabricant, texte dessiné en tracés
    vectoriels plutôt qu'en glyphes de police) : la rédaction PDF peut
    "réussir" sans effet visuel. Le pipeline vérifie par comparaison PIXEL,
    pour chaque étiquette, que la rédaction a réellement changé l'image, et
    bascule sinon en rédaction directe sur l'image rasterisée (PIL) —
    reflété par `redaction_pil_fallback` (bbox concernées, vide si tout
    s'est bien passé). `survie_nombres` est un contrôle DIFFÉRENT et
    complémentaire : il garantit qu'aucun NOMBRE n'a disparu de la couche
    texte à la rédaction.
    """
    if role not in ROLES_VALIDES:
        raise ValueError(f"role invalide : {role!r} (attendu : {ROLES_VALIDES}).")

    contenu, pdf_id = util.resoudre_pdf(pdf_base64, pdf_id)
    with util.workdir_temporaire() as wd:
        pdf_path = wd / "plan_fabricant.pdf"
        pdf_path.write_bytes(contenu)

        with fitz.open(pdf_path) as doc:
            if not (1 <= page_num <= len(doc)):
                raise ValueError(f"page_num {page_num} hors limites (1 à {len(doc)}).")
            words_data = extract.extraire_page(
                doc, page_num - 1, wd, dpi=dpi,
                generer_image=(role != "specs"),
                rediger=(role == "planche"),
            )

        reponse = {
            "page_num": page_num,
            "pdf_id": pdf_id,
            "page_size_pts": words_data["page_size_pts"],
            "words": words_data["words"],
        }
        if "survie_nombres" in words_data:
            reponse["survie_nombres"] = words_data["survie_nombres"]
        if "redaction_pil_fallback" in words_data:
            reponse["redaction_pil_fallback"] = words_data["redaction_pil_fallback"]

        image_path = wd / f"page_{page_num}_redacted.png"
        if image_path.exists():
            publication = fichiers.publier(image_path, image_path.name, "image/png")
            reponse["image_url"] = publication["url"]
            reponse["image_sha256"] = publication["sha256"]

            if role == "garde":
                try:
                    crop_path = wd / "cover_3d_full.png"
                    info = cover.extraire_vue_3d(image_path, crop_path, words_data=words_data, dpi=dpi)
                    publication_3d = fichiers.publier(crop_path, crop_path.name, "image/png")
                    reponse["vue_3d_url"] = publication_3d["url"]
                    reponse["vue_3d_sha256"] = publication_3d["sha256"]
                    reponse["vue_3d_page_w_pt"] = info["width_pt"]
                except ValueError as e:
                    # Pas d'exception bloquante : la vue 3D est optionnelle sur
                    # la page specs, signalée plutôt qu'un échec brutal de l'outil.
                    reponse["vue_3d_erreur"] = str(e)

        cache = extraction_cache.mettre_en_cache(reponse)
        reponse["extraction_id"] = cache["extraction_id"]
        reponse["extraction_id_expire_dans_s"] = cache["expire_dans_s"]
        return reponse
