# -*- coding: utf-8 -*-
"""mcp_server/tools/traduction.py — outil MCP `traduire_mots` (étape 3)."""
from typing import Any

from core import translate

from .. import util


def traduire_mots(
    *, extraction_id: str | None = None, words_data: dict | None = None,
    role: str = "planche", glossaire_version: str = "latest",
) -> dict[str, Any]:
    """Applique le glossaire Vertical aux mots d'UNE page déjà extraite
    (`extraire_page`). `role` doit correspondre à celui utilisé à
    l'extraction : "planche" (planche dessin cotée) ou "specs" (page qui
    alimente le tableau specs) — "garde" n'a rien à traduire en place (la vue
    3D fournisseur n'est jamais traduite : limite connue, cf. README).

    Fournissez EXACTEMENT UN des deux : `extraction_id` (chemin NORMAL,
    retourné par `extraire_page`) ou `words_data` (repli, petits fichiers /
    tests directs). NE RECONSTRUISEZ JAMAIS `words_data` à la main pour
    relayer la sortie d'`extraire_page` — passez `extraction_id` tel quel ;
    un agent Dust réel a buté sur la taille de ce JSON en tentant de le
    retransmettre en argument. Toute structure invalide (champ manquant,
    type incorrect) est rejetée avec une erreur explicite nommant le champ
    en cause — jamais un échec silencieux.

    Regroupement en phrases (pas seulement des étiquettes isolées) : les
    mots sont reconstitués en lignes de lecture du PDF (block/line/word),
    puis les n-grammes de mots contigus sont essayés contre le glossaire du
    plus long au plus court, avant de retomber sur une traduction mot à mot —
    ça retrouve les expressions du glossaire ("with operator on board")
    qu'une correspondance mot à mot raterait systématiquement. Un texte
    source illisible (police non standard, mot corrompu) n'est JAMAIS deviné :
    il ressort dans `hors_glossaire` (statut "hors_glossaire") plutôt que
    d'inventer une traduction.

    Retourne, selon `role` :
      - "planche" : `labels` (étiquettes FR à superposer, prêtes pour
        `assembler_pptx.planches[].labels`), `page_w_pt`, `hors_glossaire` ;
      - "specs" : `table` (liste [libellé_fr, valeur]) avec les rubriques
        commerciales absentes du plan fabricant automatiquement ajoutées et
        marquées "à confirmer (service commercial)"
        (`rubriques_commerciales_ajoutees`), `hors_glossaire`.

    `glossaire_version` : seule la valeur "latest" est supportée — le
    glossaire Vertical (data/glossaire.py) est un dictionnaire Python
    statique, pas versionné.
    """
    if glossaire_version != "latest":
        raise ValueError(
            f"glossaire_version non supportée : {glossaire_version!r} "
            "(seule 'latest' existe — le glossaire n'est pas versionné)."
        )
    if role not in ("planche", "specs"):
        raise ValueError(f"role invalide pour la traduction : {role!r} (attendu : planche, specs).")

    words_data = util.resoudre_words_data(words_data, extraction_id)

    if role == "planche":
        labels, hors_glossaire = translate.traduire_labels_planche(words_data)
        return {
            "labels": labels,
            "page_w_pt": words_data["page_size_pts"][0],
            "hors_glossaire": hors_glossaire,
        }

    table_fr, hors_glossaire = translate.extraire_tableau_specs(words_data)
    table_fr, ajouts = translate.completer_champs_commerciaux(table_fr)
    return {
        "table": [list(ligne) for ligne in table_fr],
        "hors_glossaire": hors_glossaire,
        "rubriques_commerciales_ajoutees": ajouts,
    }
