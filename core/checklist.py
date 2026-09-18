# -*- coding: utf-8 -*-
"""
core/checklist.py — Lecture de la checklist revue d'affaire (.xlsm/.xlsx),
feuille "Technique", optionnelle.

⚠️ Point de vigilance documenté par le skill original (cf. SKILL.md) : la
checklist fournie est TRÈS SOUVENT le modèle vierge (seul le type
d'équipement est renseigné). L'utiliser telle quelle comme source d'écart
produirait un rapport trompeur — `est_vierge()` doit être vérifié avant tout
usage de son contenu comme source de comparaison.
"""
from pathlib import Path


def lire_checklist(xlsm_path: Path) -> dict:
    """Lit la feuille "Technique" de la checklist. Retourne
    {libellé: {colonne: valeur}}. Lève ValueError si la feuille est absente
    (nom de feuille à vérifier avec l'utilisateur plutôt que deviner)."""
    import openpyxl

    wb = openpyxl.load_workbook(xlsm_path, data_only=True)
    if "Technique" not in wb.sheetnames:
        raise ValueError(
            f"Feuille « Technique » introuvable dans {Path(xlsm_path).name} "
            f"(feuilles présentes : {wb.sheetnames})."
        )
    ws = wb["Technique"]
    headers = [c.value for c in ws[1]]
    lignes = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None:
            continue
        lignes[str(row[0])] = {
            str(headers[i]): row[i]
            for i in range(1, len(headers))
            if row[i] is not None
        }
    return lignes


def est_vierge(lignes: dict) -> bool:
    """True si aucune valeur exploitable n'est renseignée dans la feuille
    Technique (cas fréquent : modèle vierge, seul le type d'équipement est
    rempli en amont). Dans ce cas, la checklist ne doit PAS servir de source
    de comparaison — cf. references/verification.md."""
    for colonnes in lignes.values():
        for valeur in colonnes.values():
            if str(valeur).strip():
                return False
    return True
