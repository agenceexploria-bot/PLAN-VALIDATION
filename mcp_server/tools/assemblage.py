# -*- coding: utf-8 -*-
"""mcp_server/tools/assemblage.py — outil MCP `assembler_pptx` (étape 4)."""
import re
from datetime import date
from numbers import Real
from typing import Any

from pptx import Presentation

from core import assemble

from .. import cache_disque, extraction_cache, fichiers, util

NUMERO_AFFAIRE = re.compile(r"^LD\w+$", re.IGNORECASE)
CHAMPS_META_OBLIGATOIRES = ("numero", "client", "dessinateur", "indice", "type_equipement")


def assembler_pptx(projet_json: dict) -> dict[str, Any]:
    """Construit le Plan de validation Vertical (étape 4) : COPIE d'un plan
    de validation existant choisi selon le type d'équipement (jamais un
    template vide à jetons — cf. core/assemble.py), remplacements en place.
    Réutilise core/assemble.py TEL QUEL (contention des étiquettes dans le
    cadre, rotation 90/270°, page_w par planche, cartouche_replace appliqué
    partout, garde sans AUCUNE image de plan ou de dessin — la vue 3D va sur
    la page specs, jamais sur la garde). Le dessin
    n'est jamais redessiné : chaque planche est l'image déjà extraite et
    rédigée par `extraire_page`, déposée telle quelle.

    Schéma attendu de `projet_json` (chemin NORMAL : uniquement des
    identifiants, le serveur retrouve lui-même images, largeurs de page et
    labels) :
    {
      "meta": {"numero": "LDxxxxx", "client": str, "dessinateur": str,
               "indice": "R00", "date": "JJ/MM/AAAA" (optionnel, aujourd'hui
               par défaut), "type_equipement": "non accompagné"|"accompagné"},
      "view3d": {"extraction_id": str, "callouts": [...] (optionnel)} | null,
                 # extraction_id de la page extraite avec role="garde" —
                 # absent/null : la page specs sort SANS vue 3D. Vue 3D et
                 # tableau specs sur la même page : même extraction_id que specs
      "specs": {"extraction_id": str},
                 # OBLIGATOIRE : extraction_id de la page specs, APRÈS
                 # traduire_mots(extraction_id=..., role="specs") — ou, en
                 # repli, {"table": [[libelle_fr, valeur], ...]} non vide
      "planches": [{"extraction_id": str}, ...],
                 # extraction_id de chaque page extraite avec role="planche",
                 # APRÈS traduire_mots(extraction_id=...) sur cette page
      "hors_glossaire": [...]  # optionnel, agrégé depuis traduire_mots —
                                 simplement repris dans le résumé renvoyé.
    }

    NE TÉLÉCHARGEZ JAMAIS les images pour les ré-encoder en base64 : une
    planche A3 à 300 dpi représente ~165k jetons, un agent Dust réel a
    échoué dessus. `planches[].labels` reste accepté pour SURCHARGER les
    labels de `traduire_mots`. Repli (petits fichiers / tests directs), à la
    place d'`extraction_id` : `{"image_base64", "page_n", "page_w_pt",
    "labels"}` pour une planche, `{"image_base64", "image_page_w_pt",
    "callouts"}` pour la vue 3D. Toute entrée invalide est refusée avec une
    erreur nommant le champ en cause (ex. `planches[1].extraction_id`).

    Aucune métadonnée n'est jamais fictive : chaque champ de `meta` est
    obligatoire (numéro au format LDxxxxx), l'appel échoue explicitement
    sinon plutôt que de générer un cartouche avec une valeur inventée.

    Retourne {"pptx_id": str, "pptx_url": str, "pptx_sha256": str,
    "nom_fichier": str, "resume": {...}} — passez `pptx_id` tel quel à
    `verifier_rendu` puis `exporter_pdf` (PPTX gardé sur le serveur, 30 min
    glissantes). `pptx_url` (usage unique, 5 min) sert seulement à donner le
    PPTX à l'utilisateur s'il le demande.
    """
    if not isinstance(projet_json, dict):
        raise ValueError("projet_json doit être un objet JSON.")
    _journaliser_entrees(projet_json)
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

    # Toutes les entrées résolues et validées AVANT d'écrire quoi que ce soit.
    v3d_in = projet_json.get("view3d")
    v3d_resolue = _resoudre_view3d(v3d_in) if v3d_in else None
    planches_in = projet_json.get("planches", [])
    if not isinstance(planches_in, list):
        raise ValueError("planches doit être une liste.")
    planches_resolues = [_resoudre_planche(i, p) for i, p in enumerate(planches_in)]
    specs = _resoudre_specs(projet_json.get("specs"))

    with util.workdir_temporaire() as wd:
        view3d = None
        if v3d_resolue:
            image, page_w, callouts = v3d_resolue
            (wd / "cover_3d_full.png").write_bytes(image)
            view3d = {"image": "cover_3d_full.png", "image_page_w_pt": page_w, "callouts": callouts}

        planches = []
        for i, (image, page_n, page_w, labels) in enumerate(planches_resolues):
            nom_image = f"planche_{i}.png"
            (wd / nom_image).write_bytes(image)
            planches.append({"image": nom_image, "page_n": page_n, "page_w_pt": page_w, "labels": labels})

        nom_fichier = f"Plan de validation {numero}-{meta['indice']}.pptx"
        out_path = wd / nom_fichier

        proj = {
            "base": base, "out": out_path, "workdir": wd, "meta": meta,
            "specs": specs,
            "view3d": view3d, "planches": planches,
        }
        assemble.assembler(proj)

        nb_slides = len(Presentation(str(out_path)).slides)
        pptx_id = cache_disque.PPTX.mettre_en_cache(out_path.read_bytes())
        publication = fichiers.publier(
            out_path, nom_fichier,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )

        return {
            "pptx_id": pptx_id,
            "pptx_url": publication["url"],
            "pptx_sha256": publication["sha256"],
            "nom_fichier": nom_fichier,
            "resume": {
                "nb_slides": nb_slides,
                "nb_planches": len(planches),
                "hors_glossaire": projet_json.get("hors_glossaire", []),
            },
        }


def _source(entree) -> str:
    """Chemin utilisé pour une entrée (jamais son contenu ni son identifiant)."""
    if not entree:
        return "ABSENT"
    if not isinstance(entree, dict):
        return f"invalide({type(entree).__name__})"
    for cle in ("extraction_id", "image_base64", "table"):
        if entree.get(cle):
            return cle
    return "vide"


def _journaliser_entrees(projet_json: dict) -> None:
    """Une ligne de log serveur par appel : PRÉSENCE/ABSENCE de chaque entrée
    reçue, sans contenu ni identifiant (les extraction_id donnent accès aux
    données en cache). Sert à trancher, après un test Dust, entre « l'agent
    n'a rien envoyé » et « le serveur a mal traité ce qu'il a reçu ».
    `print(flush=True)` comme core/memlog.py : visible tel quel dans les logs
    Render. Appelé AVANT toute validation : un appel refusé est aussi tracé."""
    v3d = projet_json.get("view3d")
    planches_in = projet_json.get("planches")
    if not isinstance(planches_in, list):
        planches_in = []
    planches = ", ".join(
        f"{_source(p)}{'+labels' if isinstance(p, dict) and p.get('labels') is not None else ''}"
        for p in planches_in
    )
    print(
        f"[assembler_pptx] view3d={_source(v3d)}"
        f"{'+callouts' if isinstance(v3d, dict) and v3d.get('callouts') else ''}"
        f" specs={_source(projet_json.get('specs'))}"
        f" planches={len(planches_in)} [{planches}]"
        f" hors_glossaire={'fourni' if projet_json.get('hors_glossaire') else 'ABSENT'}",
        flush=True,
    )


def _nombre(valeur) -> bool:
    return isinstance(valeur, Real) and not isinstance(valeur, bool)


def _valider_overlays(liste, champ: str) -> None:
    """Labels (planche) ou callouts (vue 3D) : chaque élément doit avoir au
    moins `text` (str) et `bbox` ([x0, y0, x1, y1]), lus par core/assemble."""
    if not isinstance(liste, list):
        raise ValueError(f"{champ} doit être une liste.")
    for j, el in enumerate(liste):
        if not isinstance(el, dict):
            raise ValueError(f"{champ}[{j}] doit être un objet.")
        if not isinstance(el.get("text"), str):
            raise ValueError(f"{champ}[{j}].text manquant ou non textuel.")
        bbox = el.get("bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(_nombre(v) for v in bbox)):
            raise ValueError(f"{champ}[{j}].bbox doit être [x0, y0, x1, y1] (nombres), reçu : {bbox!r}.")


ERREUR_SPECS_ABSENT = (
    "specs est obligatoire et ne doit pas être vide : la page specs (slide 2) porte "
    "toujours au moins le tableau FR — sans lui, elle sort quasi blanche. Passez "
    "specs={\"extraction_id\": ...} après traduire_mots(extraction_id=..., role='specs') "
    "sur la page specs (ou, en repli, specs={\"table\": [[libellé, valeur], ...]})."
)


def _resoudre_specs(specs) -> dict:
    """{"table": [...]} non vide pour `specs` — fourni tel quel, ou repris
    de `traduire_mots(role="specs")` via `extraction_id`. Jamais vide :
    premier test réel sur Render, page specs sortie blanche sans erreur."""
    if not specs:
        raise ValueError(ERREUR_SPECS_ABSENT)
    if not isinstance(specs, dict):
        raise ValueError('specs doit être un objet {"extraction_id": ...} ou {"table": [...]}.')
    if bool(specs.get("extraction_id")) == bool(specs.get("table")):
        if not specs.get("extraction_id") and not specs.get("table"):
            raise ValueError(ERREUR_SPECS_ABSENT)
        raise ValueError("specs : fournir exactement un de extraction_id ou table (pas les deux).")
    if specs.get("extraction_id"):
        eid = specs["extraction_id"]
        _extraction(eid, "specs")
        table = extraction_cache.recuperer_traduction(eid, "specs")
        if table is None:
            raise ValueError(
                "specs : aucun tableau traduit pour cette extraction — appelez d'abord "
                "traduire_mots(extraction_id=..., role='specs') sur la page specs."
            )
    else:
        table = specs["table"]
        if not isinstance(table, list):
            raise ValueError("specs.table doit être une liste de [libellé, valeur].")
        for j, ligne in enumerate(table):
            if not (isinstance(ligne, list) and len(ligne) == 2):
                raise ValueError(f"specs.table[{j}] doit être [libellé, valeur], reçu : {ligne!r}.")
    return {"table": table}


def _exactement_un(entree: dict, champ: str) -> None:
    if bool(entree.get("extraction_id")) == bool(entree.get("image_base64")):
        raise ValueError(
            f"{champ} : fournir exactement un de extraction_id (chemin normal, retourné par "
            "extraire_page) ou image_base64 (repli) — pas les deux, pas aucun."
        )


def _extraction(extraction_id, champ: str) -> dict:
    if not isinstance(extraction_id, str):
        raise ValueError(f"{champ}.extraction_id doit être une chaîne.")
    donnees = extraction_cache.recuperer(extraction_id)
    if donnees is None:
        raise ValueError(f"{champ}.extraction_id {extraction_id!r} inconnu ou expiré — relancez extraire_page.")
    return donnees


def _resoudre_planche(i: int, p) -> tuple[bytes, Any, float, list]:
    """(image, page_n, page_w_pt, labels) pour `planches[i]`."""
    champ = f"planches[{i}]"
    if not isinstance(p, dict):
        raise ValueError(f"{champ} doit être un objet.")
    _exactement_un(p, champ)
    if p.get("extraction_id"):
        eid = p["extraction_id"]
        donnees = _extraction(eid, champ)
        image = extraction_cache.recuperer_image(eid, "planche")
        if image is None:
            raise ValueError(
                f"{champ}.extraction_id : cette extraction n'a pas d'image de planche "
                "(page extraite avec role='garde' ou 'specs' ?) — utilisez l'extraction_id "
                "d'un extraire_page(role='planche')."
            )
        page_n, page_w = donnees["page_num"], donnees["page_size_pts"][0]
        labels = p.get("labels")
        if labels is None:
            labels = extraction_cache.recuperer_traduction(eid, "planche")
            if labels is None:
                raise ValueError(
                    f"{champ} : aucun label traduit pour cette extraction — appelez d'abord "
                    "traduire_mots(extraction_id=..., role='planche') sur cette page."
                )
    else:
        image = util.decoder_base64(p["image_base64"], f"{champ}.image_base64")
        page_n, page_w, labels = p.get("page_n"), p.get("page_w_pt"), p.get("labels", [])
        if page_w is not None and not _nombre(page_w):
            raise ValueError(f"{champ}.page_w_pt doit être un nombre (points PDF), reçu : {page_w!r}.")
    _valider_overlays(labels, f"{champ}.labels")
    return image, page_n, page_w, labels


def _resoudre_view3d(v) -> tuple[bytes, float, list]:
    """(image, image_page_w_pt, callouts) pour `view3d`."""
    if not isinstance(v, dict):
        raise ValueError("view3d doit être un objet ou null.")
    _exactement_un(v, "view3d")
    if v.get("extraction_id"):
        eid = v["extraction_id"]
        donnees = _extraction(eid, "view3d")
        image = extraction_cache.recuperer_image(eid, "vue_3d")
        if image is None:
            raise ValueError(
                "view3d.extraction_id : aucune vue 3D pour cette extraction (page extraite sans "
                f"role='garde', ou vue_3d_erreur : {donnees.get('vue_3d_erreur')!r})."
            )
        page_w = donnees["vue_3d_page_w_pt"]
    else:
        image = util.decoder_base64(v["image_base64"], "view3d.image_base64")
        page_w = v.get("image_page_w_pt")
        if not _nombre(page_w):
            raise ValueError(f"view3d.image_page_w_pt doit être un nombre (points PDF), reçu : {page_w!r}.")
    callouts = v.get("callouts", [])
    _valider_overlays(callouts, "view3d.callouts")
    return image, page_w, callouts
