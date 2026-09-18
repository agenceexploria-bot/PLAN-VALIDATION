# -*- coding: utf-8 -*-
"""
core/assemble.py — Étape 4 du pipeline : assemblage du Plan de validation
Vertical. Adapté de scripts/build_pptx.py du skill `plan-validation-vertical`
(logique inchangée) :

  garde (image générique FIXE conservée) · specs (vue 3D fournisseur à
  gauche + callouts FR, tableau FR reconstruit à droite) · planches cotées.
  Structure conforme à references/cartouche.md et à la décision produit
  validée en session client (cr-session-2026-06-23.md : « Page après la
  garde = image 3D de la page 1 traduite en place + tableau de specs
  reconstruit en français »). La 3D fournisseur n'est JAMAIS sur la garde —
  uniquement sur la page specs, jamais sur une page dédiée à part (bug audit :
  une page 3D séparée avait été introduite par erreur, sans base documentée,
  laissant la page specs sans image et le tableau specs sans les callouts).

On ne part PAS d'un template vide à jetons : on COPIE un vrai plan de
validation existant (choisi selon le type d'équipement) et on remplace en
place. Bases embarquées dans templates/ :
  - "non accompagné" -> templates/LD82040.pptx
  - "accompagné"      -> templates/LD64397.pptx
(structure réelle inspectée, pas supposée : specs = TextBox, cartouche =
Table 4x5, image générique de garde = remplissage de "Freeform 2".)

Le projet est décrit par un dict Python (mêmes clés que le schéma JSON du
skill d'origine) :
{
  "base": Path, "out": Path, "workdir": Path,
  "meta": {"numero", "client", "dessinateur", "indice", "date", "equipement"},
  "cartouche_replace": {"LD82040": "...", "ECEE": "...", ...},
  "view3d": {"image": "cover_3d_full.png", "image_page_w_pt": 871.2,
             "callouts": [{"text": "...", "bbox": [...]}]} | None,
  "specs": {"table": [["N° offre", "EX.26.346R2"], ...]},
  "planches": [{"image": "page_2_redacted.png", "page_n": 4,
                "labels": [{"text": "...", "bbox": [...], "vertical": bool,
                            "fpt": 7, "fit_bbox": bool, "opaque": bool}]}],
}

Règles d'or respectées : les dessins ne sont jamais redessinés (images
déposées telles quelles) ; aucun nombre n'est retapé (cote fusionnée : le
nombre reste dans l'image, seul le suffixe est retraduit — voir
core/extract.py et core/translate.py).
"""
import copy
import re
from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.util import Pt, Cm
from pptx.dml.color import RGBColor
from pptx.oxml.ns import qn

NOIR = RGBColor(0, 0, 0)
BLANC = RGBColor(0xFF, 0xFF, 0xFF)
GRIS = RGBColor(0x40, 0x40, 0x40)
GRISF = RGBColor(0xEE, 0xEE, 0xEE)
PAGE_W_PT = 1190.55
R_EMBED, R_LINK, R_ID = qn("r:embed"), qn("r:link"), qn("r:id")

DOSSIER_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

BASES_PAR_TYPE = {
    "non accompagné": DOSSIER_TEMPLATES / "LD82040.pptx",
    "accompagné": DOSSIER_TEMPLATES / "LD64397.pptx",
}

# Contacts projet STANDARD Vertical, toujours déposés sur la garde (règle
# imposée, cf. SKILL.md « Contact projet de la garde »). Nadia est présente
# dans les deux bases ; Jérémie manque dans LD64397 (« accompagné ») -> on le
# restaure systématiquement. (left_cm, nom, tel, photo_asset).
CONTACTS = [
    (2.6, "Nadia", "+33 4 72 01 05 69", "contact_nadia.png"),
    (7.0, "Jérémie", "+33 7 77 83 24 87", "contact_jeremie.png"),
]

# Noms de shapes des TextBox specs à retirer sur la base LD82040 (relevés par
# inspection réelle — cf. SKILL.md). Une base différente peut nécessiter une
# autre valeur, passée via proj["specs_remove_shapes"].
SPECS_REMOVE_SHAPES_DEFAUT = ["TextBox 15", "TextBox 12"]

# ── Purge du cartouche par LIBELLÉ (point de vigilance n°1) ─────────────────
# Contrairement au build_pptx.py d'origine (qui remplaçait des valeurs
# littérales connues à l'avance, ex. "LD82040"->"LDxxxxx"), on repère chaque
# champ par son LIBELLÉ FIXE, jamais par l'ancienne valeur : ça purge aussi
# les résidus CACHÉS d'un ancien dossier sans avoir à les connaître d'avance.
# Approche validée sur LD82040.pptx (qui contient un "Client : DIMMER SARL"
# résiduel dans une cellule fusionnée masquée, à côté de "Client : ECEE" visible).
MOTIFS_CARTOUCHE = [
    (re.compile(r"(?i)^(\s*dessinateur\s*:\s*).*$"), "dessinateur"),
    (re.compile(r"(?i)^(\s*date de création\s*:\s*).*$"), "date"),
    (re.compile(r"(?i)^(\s*plan de validation indice\s*:\s*).*$"), "indice"),
    (re.compile(r"(?i)^(\s*client\s*:\s*).*$"), "client"),
]
MOTIF_NUMERO_AFFAIRE_CELLULE = re.compile(r"(?i)^\s*LD\d+\s*$")
MOTIF_CODE_AFFAIRE_COUVERTURE = re.compile(r"(?i)LD\d+")
MOTIF_INDICE_COUVERTURE = re.compile(r"(?i)R\d+")
CHAMPS_A_COMPLETER = "à compléter"


# ── Clonage de slide FIABLE ─────────────────────────────────────────────────
def clone_slide(prs, index):
    """Copie TOUTES les relations (pas seulement les images) en remappant les
    rId dans le XML cloné. Un simple deepcopy du spTree corrompt le fichier
    (rId orphelin -> « PowerPoint n'a pas pu lire le contenu »)."""
    src = prs.slides[index]
    new = prs.slides.add_slide(src.slide_layout)
    for sh in list(new.shapes):
        sh._element.getparent().remove(sh._element)
    rid_map = {}
    for rid, rel in src.part.rels.items():
        if rel.reltype.endswith("slideLayout"):
            continue
        if rel.is_external:
            rid_map[rid] = new.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
        else:
            rid_map[rid] = new.part.relate_to(rel._target, rel.reltype)
    for sh in src.shapes:
        el = copy.deepcopy(sh._element)
        for node in el.iter():
            for att in (R_EMBED, R_LINK, R_ID):
                if att in node.attrib and node.attrib[att] in rid_map:
                    node.attrib[att] = rid_map[node.attrib[att]]
        new.shapes._spTree.append(el)
    return new


# ── Texte / cartouche ───────────────────────────────────────────────────────
def purger_cartouche(slide, meta: dict):
    """Remplace, dans CHAQUE cellule du cartouche — y compris une cellule
    fusionnée « masquée » à l'écran mais qui contient malgré tout un résidu
    d'un ancien dossier —, le libellé fixe suivi de sa valeur, run par run
    (préserve police/taille/couleur d'origine). Le champ est reconnu par son
    LIBELLÉ, jamais par l'ancienne valeur : aucun résidu caché ne peut
    survivre, même sans le connaître à l'avance.

    Cas particulier des cellules de CONTINUATION d'une fusion horizontale ou
    verticale (`hMerge`/`vMerge`, ex. cartouche 4x5 des gabarits Vertical) :
    PowerPoint n'affiche jamais leur contenu (seule la cellule d'origine du
    merge est visible), mais `row.cells` de python-pptx les visite comme des
    cellules normales, avec leur propre résidu de texte — la logique de
    remplacement par libellé les aurait donc dupliquées (même valeur que la
    cellule d'origine, ou pire, une valeur légale différente jamais nettoyée
    faute de libellé reconnu, ex. mention légale). Elles sont vidées, jamais
    dupliquées (bug audit — confirmé sur templates/LD82040.pptx : cellules
    hMerge/vMerge des lignes date/client/mention légale)."""
    for shape in slide.shapes:
        if not shape.has_table:
            continue
        for row in shape.table.rows:
            for cell in row.cells:
                tc = cell._tc
                if tc.get("hMerge") == "1" or tc.get("vMerge") == "1":
                    for para in cell.text_frame.paragraphs:
                        for run in para.runs:
                            run.text = ""
                    continue
                for para in cell.text_frame.paragraphs:
                    for run in para.runs:
                        texte = run.text
                        remplace = False
                        for motif, cle in MOTIFS_CARTOUCHE:
                            m = motif.match(texte)
                            if m:
                                run.text = m.group(1) + str(meta.get(cle, CHAMPS_A_COMPLETER))
                                remplace = True
                                break
                        if not remplace and MOTIF_NUMERO_AFFAIRE_CELLULE.match(texte):
                            run.text = str(meta.get("numero", CHAMPS_A_COMPLETER))


def purger_page_de_couverture(slide, meta: dict):
    """La page de garde affiche le numéro d'affaire (et parfois l'indice) du
    gabarit en texte libre (ex. « N°LD82040 »). Repéré par son FORMAT
    (LDxxxxx), pas par la valeur exacte du gabarit choisi : robuste quelle
    que soit la base copiée."""
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        for para in shape.text_frame.paragraphs:
            for run in para.runs:
                if not MOTIF_CODE_AFFAIRE_COUVERTURE.search(run.text):
                    continue
                nouveau = MOTIF_CODE_AFFAIRE_COUVERTURE.sub(
                    str(meta.get("numero", CHAMPS_A_COMPLETER)), run.text
                )
                indice = meta.get("indice")
                if indice and indice != CHAMPS_A_COMPLETER and MOTIF_INDICE_COUVERTURE.search(nouveau):
                    indice_norm = indice if indice.upper().startswith("R") else f"R{indice}"
                    nouveau = MOTIF_INDICE_COUVERTURE.sub(indice_norm, nouveau)
                run.text = nouveau


def set_page_number(slide, n):
    for shape in slide.shapes:
        if not shape.has_table:
            continue
        for row in shape.table.rows:
            for cell in row.cells:
                for para in cell.text_frame.paragraphs:
                    for run in para.runs:
                        if re.search(r"Page\s*:\s*\d+", run.text):
                            run.text = re.sub(r"Page\s*:\s*\d+", f"Page : {n}", run.text)


def remove_shapes(slide, names):
    for sh in list(slide.shapes):
        if sh.name in names:
            sh._element.getparent().remove(sh._element)


def delete_slide(prs, index):
    """Retire une slide : on enlève l'entrée `sldIdLst` ET la relation
    associée, pour ne pas laisser de part orpheline (sinon un clonage
    ultérieur réutilise le même nom de part et corrompt le fichier)."""
    xml_slides = prs.slides._sldIdLst
    sldId = list(xml_slides)[index]
    rId = sldId.get(R_ID)
    if rId:
        prs.part.drop_rel(rId)
    xml_slides.remove(sldId)


def trim_to(prs, n_keep):
    """Ne garde que les n_keep premières slides (garde + specs + planches)."""
    while len(prs.slides._sldIdLst) > n_keep:
        delete_slide(prs, len(prs.slides._sldIdLst) - 1)


def slide_text(slide):
    return " ".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame)


def ensure_contacts(slide):
    """Garantit les DEUX contacts projet standard sur la garde. Idempotent :
    un contact déjà présent (par son prénom) n'est pas redoublé. Photo
    déposée si l'asset existe ; sinon seul le bloc texte est ajouté."""
    have = slide_text(slide)
    for left_cm, nom, tel, photo in CONTACTS:
        if nom in have:
            continue
        ph = DOSSIER_TEMPLATES / photo if photo else None
        if ph and ph.exists():
            slide.shapes.add_picture(str(ph), Cm(left_cm), Cm(14.6), Cm(3.2), Cm(3.2))
        tb = slide.shapes.add_textbox(Cm(left_cm), Cm(18.2), Cm(3.6), Cm(1.0))
        tb.text_frame.word_wrap = True
        r = tb.text_frame.paragraphs[0].add_run(); r.text = nom
        r.font.size = Pt(11); r.font.bold = True; r.font.color.rgb = NOIR
        p = tb.text_frame.add_paragraph(); r2 = p.add_run(); r2.text = tel
        r2.font.size = Pt(9); r2.font.color.rgb = GRIS


# ── Images / étiquettes ─────────────────────────────────────────────────────
def fit_picture(slide, img_path, zl, zt, zw, zh):
    """Dépose une VRAIE Picture rectangulaire dans la zone, ratio préservé
    (jamais étirée, jamais en remplissage de forme — cf. SKILL.md « Images
    figées / cadrées / qui débordent »)."""
    with Image.open(img_path) as im:
        pw, ph = im.size
    s = min(zw / pw, zh / ph)
    w, h = int(pw * s), int(ph * s)
    left = zl + (zw - w) // 2
    top = zt + (zh - h) // 2
    slide.shapes.add_picture(str(img_path), left, top, w, h)
    return left, top, w, h


def draw_leader(slide, x1, y1, x2, y2, hexc="404040", w=9525):
    """Trait de rappel fin callout -> composant (connecteur droit)."""
    from pptx.enum.shapes import MSO_CONNECTOR
    cn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, int(x1), int(y1), int(x2), int(y2))
    cn.line.color.rgb = RGBColor.from_string(hexc)
    cn.line.width = w


# Largeur approximative (EMU) d'un texte rendu à une taille de police donnée
# (~0.52 * taille par caractère, police du template) : sert à garantir que le
# texte FR affiché — souvent plus long que le mot source, ex. "HANDLED DURING"
# -> traduction plus longue — ne déborde jamais de son étiquette (bug audit :
# largeur/hauteur fixes, identiques pour toutes les étiquettes).
CAR_PT = 0.52


def _largeur_texte(text, fpt):
    return Pt(fpt * CAR_PT * max(len(text), 1))


def add_overlay(slide, text, bbox, geom, vertical=False, fpt=8, page_w=PAGE_W_PT,
                 wrap=False, box_w_cm=None, anchor_left=False, leader_to=None,
                 fit_bbox=False, opaque=False):
    """Étiquette FR superposée — zone de texte indépendante et éditable,
    jamais aplatie dans l'image. Fond transparent par défaut (l'image rédigée
    a déjà effacé le texte source) ; `opaque=True` réservé au cas d'un PDF
    scanné (non rédigé). Cotes verticales : rotation 270 (lecture bas->haut).
    `fit_bbox` cale la boîte sur la cellule source et réduit la police pour
    tenir dedans (indispensable pour les petites cellules type bloc
    tolérances, cf. SKILL.md).

    Taille de la boîte (largeur ET hauteur, jamais fixe — bug audit) dérivée
    de la bbox du mot source (échelle réelle du plan) ET du texte FR
    réellement affiché (avec marge), le plus grand des deux l'emportant :
    une étiquette ne doit ni écraser un texte FR plus long que la source, ni
    laisser une boîte disproportionnée sur un texte source très court."""
    left, top, disp_w, _ = geom
    epp = disp_w / page_w
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    sy = int(top + cy * epp)
    marge = Cm(0.15)
    if vertical and fit_bbox:
        bw = max(int((y1 - y0) * epp), Cm(0.6)); bh = max(int((x1 - x0) * epp), Cm(0.28))
        tb = slide.shapes.add_textbox(int(left + cx * epp - bw / 2), int(sy - bh / 2), int(bw), int(bh))
        tb.rotation = 270
        fpt = max(3.5, min(fpt, (bw / 12700.0) / (len(text) * 0.55)))
    elif vertical:
        # Boîte tournée (cote verticale) : la largeur AVANT rotation suit la
        # hauteur du mot source, la hauteur AVANT rotation suit sa largeur —
        # et le texte FR affiché, qui peut déborder du mot d'origine.
        bw = max(int((y1 - y0) * epp) + marge, _largeur_texte(text, fpt) + marge, Cm(0.6))
        bh = max(int((x1 - x0) * epp) + marge, Cm(0.28))
        tb = slide.shapes.add_textbox(int(left + cx * epp - bw / 2), int(sy - bh / 2), int(bw), int(bh))
        tb.rotation = 270
    elif anchor_left:
        bw = Cm(box_w_cm or 5); bh = Cm(1.3)
        tb = slide.shapes.add_textbox(int(left + x0 * epp), int(sy - bh / 2), int(bw), int(bh))
    else:
        bw = max(int((x1 - x0) * epp) + marge, _largeur_texte(text, fpt) + marge, Cm(0.6))
        bh = max(int((y1 - y0) * epp) + marge, Pt(fpt * 1.4))
        tb = slide.shapes.add_textbox(int(left + cx * epp - bw / 2), int(sy - bh / 2), int(bw), int(bh))
    tf = tb.text_frame; tf.word_wrap = wrap
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    if opaque:
        tb.fill.solid(); tb.fill.fore_color.rgb = BLANC
    else:
        tb.fill.background()
    tb.line.fill.background()
    from pptx.enum.text import PP_ALIGN
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    r = tf.paragraphs[0].add_run(); r.text = text
    r.font.size = Pt(fpt); r.font.color.rgb = NOIR
    if leader_to:
        epp_l = disp_w / page_w
        tx, ty = left + leader_to[0] * epp_l, top + leader_to[1] * epp_l
        draw_leader(slide, tb.left + tb.width // 2, tb.top + tb.height // 2, tx, ty)


# ── Tableau FR bordé ────────────────────────────────────────────────────────
def cell_border(tc, hexc="404040", w=12700):
    """Les a:ln* DOIVENT précéder le remplissage dans a:tcPr (ordre OOXML),
    sinon PowerPoint ignore silencieusement les bordures."""
    tcPr = tc.find(qn("a:tcPr"))
    if tcPr is None:
        tcPr = tc.makeelement(qn("a:tcPr"), {}); tc.append(tcPr)
    for edge in ("lnL", "lnR", "lnT", "lnB"):
        for old in tcPr.findall(qn("a:" + edge)):
            tcPr.remove(old)
    for idx, edge in enumerate(("lnL", "lnR", "lnT", "lnB")):
        ln = tcPr.makeelement(qn("a:" + edge), {"w": str(w), "cap": "flat", "cmpd": "sng", "algn": "ctr"})
        sf = ln.makeelement(qn("a:solidFill"), {}); ln.append(sf)
        sc = sf.makeelement(qn("a:srgbClr"), {"val": hexc}); sf.append(sc)
        tcPr.insert(idx, ln)


def add_fr_table(slide, left, top, width, rows):
    n = len(rows)
    tbl = slide.shapes.add_table(n, 2, left, top, width, Cm(0.85 * n)).table
    tbl.columns[0].width = Cm(4.6); tbl.columns[1].width = width - Cm(4.6)
    for r, (lab, val) in enumerate(rows):
        for c, (txt, bold, fill) in enumerate([(lab, True, GRISF), (val, False, BLANC)]):
            cell = tbl.cell(r, c)
            cell.fill.solid(); cell.fill.fore_color.rgb = fill
            cell.margin_left = Cm(0.15); cell.margin_top = Cm(0.02); cell.margin_bottom = Cm(0.02)
            tf = cell.text_frame
            for p in tf.paragraphs:
                for run in p.runs:
                    run.text = ""
            run = tf.paragraphs[0].add_run(); run.text = txt
            run.font.size = Pt(9); run.font.bold = bold; run.font.color.rgb = NOIR
            cell_border(cell._tc)
    return tbl


# ── Construction ────────────────────────────────────────────────────────────
def base_pour_type(type_equipement: str) -> Path:
    """Retourne le chemin de la base pptx (LD82040/LD64397) selon le type
    d'équipement saisi ('non accompagné' / 'accompagné')."""
    cle = type_equipement.strip().lower()
    if cle not in BASES_PAR_TYPE:
        raise ValueError(
            f"Type d'équipement inconnu : « {type_equipement} ». "
            f"Valeurs acceptées : {list(BASES_PAR_TYPE)}."
        )
    chemin = BASES_PAR_TYPE[cle]
    if not chemin.exists():
        raise FileNotFoundError(
            f"Base de montage introuvable : {chemin}. Les plans de validation "
            "existants (LD82040.pptx / LD64397.pptx) doivent être présents "
            "dans templates/ — ce sont des documents fournis par Vertical, "
            "jamais régénérés par l'application."
        )
    return chemin


def assembler(proj: dict) -> Path:
    """Construit le Plan de validation Vertical. Voir le docstring de module
    pour le schéma complet de `proj`. Retourne le chemin du pptx généré."""
    base = Path(proj["base"]); out = Path(proj["out"])
    wd = Path(proj["workdir"]); meta = proj["meta"]

    import shutil
    shutil.copy(base, out)
    prs = Presentation(str(out))
    W = prs.slide_width

    specs_remove = proj.get("specs_remove_shapes", SPECS_REMOVE_SHAPES_DEFAUT)
    v3d = proj.get("view3d")
    planches = proj.get("planches", [])

    # Élagage : garde + specs + autant de planches que demandé.
    trim_to(prs, 2 + len(planches))

    # SLIDE 1 — page de garde : on CONSERVE l'image générique fixe du
    # monte-charge (la 3D fournisseur va sur la page specs, jamais ici).
    # Seuls n° LD / indice et les contacts changent.
    s1 = prs.slides[0]
    purger_page_de_couverture(s1, meta)
    ensure_contacts(s1)

    # SLIDE 2 — page specs : vue 3D fournisseur (+ callouts FR) à GAUCHE,
    # tableau FR reconstruit à DROITE (cf. references/cartouche.md — pas de
    # page dédiée séparée, cf. docstring de module).
    s2 = prs.slides[1]
    sp = proj.get("specs", {})
    remove_shapes(s2, specs_remove)
    purger_cartouche(s2, meta)
    if v3d:
        geom2 = fit_picture(s2, wd / v3d["image"], Cm(0.6), Cm(2.3), Cm(17.2), Cm(15.3))
        for c in v3d.get("callouts", []):
            add_overlay(s2, c["text"], c["bbox"], geom2,
                        page_w=v3d.get("image_page_w_pt", PAGE_W_PT), wrap=True,
                        box_w_cm=c.get("box_w_cm", 4.2), anchor_left=True,
                        fpt=c.get("fpt", 8), leader_to=c.get("leader_to"),
                        opaque=c.get("opaque", False))
    if sp.get("table"):
        add_fr_table(s2, Cm(18.4), Cm(3.4), Cm(10.8), [tuple(r) for r in sp["table"]])

    # SLIDES 3+ — planches dessin.
    base_idx = 2
    for i, planche in enumerate(planches):
        s = (prs.slides[base_idx + i] if (base_idx + i) < len(prs.slides._sldIdLst)
             else clone_slide(prs, base_idx))
        for sh in list(s.shapes):
            if sh.shape_type == 13 and sh.width > Cm(8):  # image fournisseur d'origine
                sh._element.getparent().remove(sh._element)
        purger_cartouche(s, meta)
        geom = fit_picture(s, wd / planche["image"], Cm(0.6), Cm(2.3), W - Cm(1.2), Cm(15.3))
        # Largeur RÉELLE de la page source (points PDF) : les bbox des étiquettes
        # sont dans son repère, `add_overlay` en déduit l'échelle points -> EMU.
        # La constante A3 (PAGE_W_PT) n'est qu'un repli : sur tout autre format
        # (A2, A1…) elle tassait toutes les étiquettes dans un coin de la planche.
        page_w = planche.get("page_w_pt", PAGE_W_PT)
        for lab in planche.get("labels", []):
            add_overlay(s, lab["text"], lab["bbox"], geom, page_w=page_w,
                        vertical=lab.get("vertical", False), fpt=lab.get("fpt", 8),
                        fit_bbox=lab.get("fit_bbox", False), opaque=lab.get("opaque", False))

    # Numérotation par position finale (garde=1 sans champ, specs=2, planches=3…)
    for i, s in enumerate(prs.slides):
        set_page_number(s, i + 1)

    prs.save(str(out))
    return out
