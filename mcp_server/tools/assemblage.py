# -*- coding: utf-8 -*-
"""mcp_server/tools/assemblage.py — outil MCP `assembler_pptx` (étape 4)."""
import base64
import re
from datetime import date
from typing import Any

from pptx import Presentation

from core import assemble

from .. import fichiers, util

NUMERO_AFFAIRE = re.compile(r"^LD\w+$", re.IGNORECASE)
CHAMPS_META_OBLIGATOIRES = ("numero", "client", "dessinateur", "indice", "type_equipement")


def assembler_pptx(projet_json: dict) -> dict[str, Any]:
    """Construit le Plan de validation Vertical (étape 4) : COPIE d'un plan
    de validation existant choisi selon le type d'équipement (jamais un
    template vide à jetons — cf. core/assemble.py), remplacements en place.
    Réutilise core/assemble.py TEL QUEL (contention des étiquettes dans le
    cadre, rotation 90/270°, page_w par planche, cartouche_replace appliqué
    partout, image générique de la garde neutralisée/conservée). Le dessin
    n'est jamais redessiné : chaque planche est l'image déjà extraite et
    rédigée par `extraire_page`, déposée telle quelle.

    Schéma attendu de `projet_json` :
    {
      "meta": {"numero": "LDxxxxx", "client": str, "dessinateur": str,
               "indice": "R00", "date": "JJ/MM/AAAA" (optionnel, aujourd'hui
               par défaut), "type_equipement": "non accompagné"|"accompagné"},
      "view3d": {"image_base64": str, "image_page_w_pt": float,
                 "callouts": [...]} | null,
      "specs": {"table": [[libelle_fr, valeur], ...]},
      "planches": [{"image_base64": str, "page_n": int, "page_w_pt": float,
                     "labels": [...]}, ...],
      "hors_glossaire": [...]  # optionnel, agrégé depuis traduire_mots —
                                 simplement repris dans le résumé renvoyé.
    }

    Aucune métadonnée n'est jamais fictive : chaque champ de `meta` est
    obligatoire (numéro au format LDxxxxx), l'appel échoue explicitement
    sinon plutôt que de générer un cartouche avec une valeur inventée.

    Retourne {"pptx_url": str, "pptx_sha256": str, "nom_fichier": str,
    "resume": {...}} — `pptx_url` est une URL de téléchargement à usage
    unique (5 min de durée de vie) : un PPTX réel dépasse largement la
    limite de 1 Mio par réponse d'outil du protocole MCP. Récupérez-le par
    un GET simple, puis ré-encodez-le en base64 pour `verifier_rendu` /
    `exporter_pdf`.
    """
    meta_in = projet_json.get("meta") or {}
    for champ in CHAMPS_META_OBLIGATOIRES:
        if not str(meta_in.get(champ, "")).strip():
            raise ValueError(
                f"meta.{champ} est obligatoire — jamais de valeur fictive dans le cartouche."
            )
    numero = meta_in["numero"].strip()
    if not NUMERO_AFFAIRE.match(numero):
        raise ValueError(f"meta.numero doit être au format LDxxxxx : {numero!r}.")

    meta = {
        **meta_in,
        "numero": numero,
        "equipement": meta_in.get("equipement", "MONTE-CHARGE"),
        "date": meta_in.get("date") or date.today().strftime("%d/%m/%Y"),
    }

    base = assemble.base_pour_type(meta["type_equipement"])

    with util.workdir_temporaire() as wd:
        view3d = None
        v3d_in = projet_json.get("view3d")
        if v3d_in:
            (wd / "cover_3d_full.png").write_bytes(base64.b64decode(v3d_in["image_base64"]))
            view3d = {
                "image": "cover_3d_full.png",
                "image_page_w_pt": v3d_in["image_page_w_pt"],
                "callouts": v3d_in.get("callouts", []),
            }

        planches = []
        for i, p in enumerate(projet_json.get("planches", [])):
            nom_image = f"planche_{i}.png"
            (wd / nom_image).write_bytes(base64.b64decode(p["image_base64"]))
            planches.append({
                "image": nom_image,
                "page_n": p.get("page_n"),
                "page_w_pt": p.get("page_w_pt"),
                "labels": p.get("labels", []),
            })

        nom_fichier = f"Plan de validation {numero}-{meta['indice']}.pptx"
        out_path = wd / nom_fichier

        proj = {
            "base": base, "out": out_path, "workdir": wd, "meta": meta,
            "specs": projet_json.get("specs") or {},
            "view3d": view3d, "planches": planches,
        }
        assemble.assembler(proj)

        nb_slides = len(Presentation(str(out_path)).slides)
        publication = fichiers.publier(
            out_path, nom_fichier,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )

        return {
            "pptx_url": publication["url"],
            "pptx_sha256": publication["sha256"],
            "nom_fichier": nom_fichier,
            "resume": {
                "nb_slides": nb_slides,
                "nb_planches": len(planches),
                "hors_glossaire": projet_json.get("hors_glossaire", []),
            },
        }
