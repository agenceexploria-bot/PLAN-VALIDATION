# -*- coding: utf-8 -*-
"""
LOT B — lisibilité des plans. Méthode inchangée : on n'a JAMAIS le droit de
redessiner ; on efface proprement l'anglais, puis on pose le français lisible
par-dessus l'image d'origine.

B1. Un mot traduit = un effacement + une pose, à la même position. Jamais de
    pose sans effacement dessous, jamais d'effacement sans pose. Un mot qu'on
    ne sait pas traduire (hors glossaire) reste tel quel dans l'image : ni
    rédigé, ni réétiqueté à l'identique.
B2. Les nombres, codes et désignations (Ø120, M12, R25, 2xØ14, 12mm, 1500kg,
    EX.26.396R1, DHYA.2…) ne sont jamais effacés ni retapés.

Les contrôles se font sur les PIXELS de l'image rédigée, comparée à un rendu
brut de la même page (mêmes dpi) — pas seulement sur des structures de données.
"""
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageChops

from core import extract, translate

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "DHYA2_test.pdf"
DPI = 150
PAGE = 2  # planche cotée du fixture

pytestmark = pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="fixture PDF absente")


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------

def _rendu_brut(pdf: Path, page_n: int, dpi: int = DPI) -> Image.Image:
    with fitz.open(pdf) as doc:
        s = dpi / 72
        pix = doc[page_n - 1].get_pixmap(matrix=fitz.Matrix(s, s))
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _zone(image: Image.Image, bbox, dpi: int = DPI, marge_px: int = 0) -> Image.Image:
    s = dpi / 72
    x0, y0, x1, y1 = bbox
    return image.crop((int(x0 * s) + marge_px, int(y0 * s) + marge_px,
                       int(x1 * s) - marge_px, int(y1 * s) - marge_px))


def _pixels_sombres(zone: Image.Image) -> int:
    return sum(1 for p in zone.convert("L").getdata() if p < 200)


def _texte_efface(zone_brute: Image.Image, zone_redigee: Image.Image, reste_max: float = 0.5) -> bool:
    """Le texte est effacé si la zone a perdu au moins la moitié de ses pixels
    sombres. Pas « zone blanche » : des traits de cotation / de cellule
    traversent souvent la bbox d'un mot et sont préservés volontairement (on
    ne touche jamais au dessin) — calibré sur le fixture : 0 à 37 % restent."""
    avant = _pixels_sombres(zone_brute)
    return avant > 0 and _pixels_sombres(zone_redigee) <= reste_max * avant


def _identique(a: Image.Image, b: Image.Image) -> bool:
    return ImageChops.difference(a, b).getbbox() is None


def _intersecte(b1, b2) -> bool:
    return not (b1[2] <= b2[0] or b2[2] <= b1[0] or b1[3] <= b2[1] or b2[3] <= b1[1])


@pytest.fixture(scope="module")
def planche(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("lisibilite")
    words = extract.extraire_pdf(FIXTURE_PDF, tmp, [PAGE], dpi=DPI)[PAGE]
    labels, hors_glossaire = translate.traduire_labels_planche(words)
    redigee = Image.open(tmp / f"page_{PAGE}_redacted.png").convert("RGB")
    return {"words": words, "labels": labels, "hors_glossaire": hors_glossaire,
            "redigee": redigee, "brut": _rendu_brut(FIXTURE_PDF, PAGE), "tmp": tmp}


# ---------------------------------------------------------------------------
# B1 — un mot traduit = un effacement + une pose
# ---------------------------------------------------------------------------

def test_aucune_etiquette_identique_au_mot_source(planche):
    """Réécrire un mot à l'identique n'apporte rien et détruit l'original."""
    assert planche["labels"], "le fixture doit produire des étiquettes"
    identiques = [l for l in planche["labels"]
                  if l["text"].strip("() ").casefold() == l["source"].strip("() ").casefold()]
    assert not identiques, [l["text"] for l in identiques]


def test_jamais_de_pose_sans_effacement(planche):
    """Sous chaque étiquette FR, l'image d'origine est effacée (blanc) :
    sinon on lit l'anglais ET le français superposés (« gribouillis »)."""
    non_effaces = [
        l["source"] for l in planche["labels"]
        if not _texte_efface(_zone(planche["brut"], l["bbox"]), _zone(planche["redigee"], l["bbox"]))
    ]
    assert not non_effaces, f"anglais encore visible sous l'étiquette FR : {non_effaces}"


def test_les_mots_traduits_etaient_bien_visibles_avant_effacement(planche):
    """Garde-fou du test précédent : la zone contenait bien de l'encre (l'anglais)
    dans le rendu brut — le test d'effacement ne passe pas « à vide »."""
    for l in planche["labels"]:
        assert _pixels_sombres(_zone(planche["brut"], l["bbox"])) > 0, l["source"]


def test_mot_hors_glossaire_reste_intact_dans_l_image(planche):
    """Un mot qu'on ne sait pas traduire n'est NI rédigé NI réétiqueté : sa zone
    est identique au rendu brut (hors zones effacées pour une autre étiquette)."""
    bbox_labels = [l["bbox"] for l in planche["labels"]]
    sources_hg = {t["source"] for t in planche["hors_glossaire"]}
    candidats = [w for w in planche["words"]["words"]
                 if w["text"] in sources_hg
                 and not any(_intersecte(w["bbox"], b) for b in bbox_labels)]
    assert candidats, "le fixture doit contenir au moins un mot hors glossaire isolé"
    for w in candidats:
        assert _identique(_zone(planche["redigee"], w["bbox"]), _zone(planche["brut"], w["bbox"])), \
            f"« {w['text']} » (hors glossaire) a été altéré dans l'image"


def test_aucune_etiquette_pour_un_mot_hors_glossaire(planche):
    sources_hg = {t["source"] for t in planche["hors_glossaire"]}
    assert sources_hg, "le fixture doit contenir des mots hors glossaire"
    poses = {l["source"] for l in planche["labels"]}
    assert not (sources_hg & poses)


def test_seules_les_zones_des_etiquettes_sont_modifiees(planche):
    """Tout pixel modifié par la rédaction appartient à la bbox d'une étiquette
    posée : aucun autre effacement (rien d'effacé « pour rien »)."""
    diff = ImageChops.difference(planche["brut"], planche["redigee"]).convert("L").point(lambda p: 255 if p else 0)
    masque = Image.new("L", diff.size, 0)
    s = DPI / 72
    from PIL import ImageDraw
    d = ImageDraw.Draw(masque)
    for l in planche["labels"]:
        x0, y0, x1, y1 = l["bbox"]
        d.rectangle([int(x0 * s) - 2, int(y0 * s) - 2, int(x1 * s) + 2, int(y1 * s) + 2], fill=255)
    hors_zone = ImageChops.subtract(diff, masque)
    assert hors_zone.getbbox() is None, f"pixels modifiés hors des étiquettes : {hors_zone.getbbox()}"


def test_page_de_garde_non_redigee_quand_aucune_etiquette_n_est_posee(tmp_path):
    """La vue 3D de la garde ne reçoit aucune étiquette FR : y effacer du texte
    sans rien reposer serait de la perte d'information pure."""
    extract.extraire_pdf(FIXTURE_PDF, tmp_path, [1], dpi=DPI, rediger=False)
    redigee = Image.open(tmp_path / "page_1_redacted.png").convert("RGB")
    assert _identique(redigee, _rendu_brut(FIXTURE_PDF, 1))


# ---------------------------------------------------------------------------
# B1 (cause racine découverte au rendu) — les libellés d'une cote/cellule
# verticale doivent être posés verticalement, sinon ils s'empilent en travers.
# La direction d'écriture est portée par la LIGNE (`line["dir"]`) ; le code la
# lisait sur le span (`span.get("dir", (1, 0))`), où elle n'existe pas : tout
# était donc « horizontal ».
# ---------------------------------------------------------------------------

def test_mots_verticaux_detectes_comme_verticaux(planche):
    rot = {w["text"]: w["rotation_deg"] for w in planche["words"]["words"]}
    assert rot["SINIF"] == 270          # cellule du bloc tolérances, lue de bas en haut
    for cote in ("1800(PITSIZE)", "1750(CONS.)", "3930(FFL)"):
        assert rot[cote] in (90, 270), f"{cote} : rotation détectée {rot[cote]}"


def test_mots_horizontaux_restent_horizontaux(planche):
    rot = {w["text"]: w["rotation_deg"] for w in planche["words"]["words"]}
    assert rot["2000"] == 0
    assert rot["2835(PITSIZE)"] == 0


def test_etiquettes_de_cotes_verticales_marquees_verticales(planche):
    verticales = {l["source"] for l in planche["labels"] if l["vertical"]}
    assert "SINIF" in verticales


# ---------------------------------------------------------------------------
# B1 — la pose ne doit pas recouvrir le nombre voisin, et suit la rotation
# ---------------------------------------------------------------------------
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from core import assemble


@pytest.fixture(scope="module")
def deck(planche):
    pl, _ = translate.construire_planche(PAGE, planche["words"])
    out = planche["tmp"] / "deck.pptx"
    assemble.assembler({
        "base": assemble.base_pour_type("non accompagné"), "out": out,
        "workdir": planche["tmp"],
        "meta": {"numero": "LDTEST1", "client": "C", "dessinateur": "AB", "indice": "R00",
                 "date": "01/01/2026", "equipement": "MONTE-CHARGE"},
        "specs": {}, "planches": [pl],
    })
    formes = list(Presentation(out).slides[2].shapes)
    i_img = max(i for i, sh in enumerate(formes) if sh.shape_type == MSO_SHAPE_TYPE.PICTURE)
    return {"planche": pl, "image": formes[i_img], "boites": formes[i_img + 1:],
            "page_w": planche["words"]["page_size_pts"][0]}


def _rect_pts(deck, tb):
    """Emprise RÉELLE (après rotation) de la zone de texte, en points PDF."""
    k = deck["page_w"] / deck["image"].width
    cx = (tb.left + tb.width / 2 - deck["image"].left) * k
    cy = (tb.top + tb.height / 2 - deck["image"].top) * k
    w, h = tb.width * k, tb.height * k
    if tb.rotation in (90, 270):
        w, h = h, w
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def test_etiquettes_de_suffixe_partent_du_debut_du_suffixe(deck):
    """« 2835(PITSIZE) » : l'étiquette « (dim. fosse) », plus large que le
    suffixe anglais, ne doit pas mordre sur « 2835 » qui la précède."""
    ancrees = [(l, tb) for l, tb in zip(deck["planche"]["labels"], deck["boites"])
               if l.get("ancre") == "debut"]
    assert any(not l["vertical"] for l, _ in ancrees) and any(l["vertical"] for l, _ in ancrees)
    for lab, tb in ancrees:
        x0, y0, x1, y1 = lab["bbox"]                    # bbox du suffixe source
        r = _rect_pts(deck, tb)
        if not lab["vertical"]:
            assert r[0] >= x0 - 1.0, f"« {lab['text']} » déborde vers la gauche, sur le nombre"
        elif lab["rotation_deg"] == 270:                # lu de bas en haut : le nombre est en dessous
            assert r[3] <= y1 + 1.0, f"« {lab['text']} » déborde vers le bas, sur le nombre"
        else:                                           # lu de haut en bas : le nombre est au-dessus
            assert r[1] >= y0 - 1.0, f"« {lab['text']} » déborde vers le haut, sur le nombre"


def test_zones_de_texte_tournees_comme_le_mot_source(deck):
    for lab, tb in zip(deck["planche"]["labels"], deck["boites"]):
        attendu = lab["rotation_deg"] if lab["vertical"] else 0
        assert tb.rotation == attendu, f"« {lab['text']} » : rotation {tb.rotation}, attendu {attendu}"


# ===========================================================================
# B2 — nombres, codes et désignations : jamais effacés, jamais retapés
# ===========================================================================
from collections import Counter

TOKENS_INTOUCHABLES = [
    "Ø120", "M12", "R25", "H7", "Ra3.2", "2xØ14", "12mm", "1500kg", "3.5kW",
    "EX.26.396R1", "DHYA.2", "LD82040", "P12345", "IP54", "RAL7016", "45°",
    "2000X1500", "X1500", "A-A", "B-B",
]


@pytest.mark.parametrize("token", TOKENS_INTOUCHABLES)
def test_token_avec_chiffre_ou_code_jamais_traduisible(token):
    assert extract.is_translatable(token) is False


@pytest.mark.parametrize("token", ["Ø120", "M12", "R25", "2xØ14", "12mm", "1500kg",
                                   "3.5kW", "2000X1500", "45°", "1.5", "2,5m/s"])
def test_ce_n_est_pas_une_cote_fusionnee_a_un_suffixe_a_traduire(token):
    """Unités, dimensions composées et désignations ne sont PAS « nombre +
    suffixe texte » : leur « suffixe » (mm, xØ14…) n'est jamais réécrit."""
    assert extract.fused_number(token) is None


@pytest.mark.parametrize("token,nombre", [("9700(FFL)", "9700"), ("150(PIT)", "150"),
                                          ("1750(CONS.)", "1750"), ("2835(PITSIZE)", "2835")])
def test_vraie_cote_fusionnee_toujours_reconnue(token, nombre):
    assert extract.fused_number(token) == nombre
    assert extract.is_translatable(token) is False   # le MOT n'est jamais effacé en entier


def _pdf_de_codes(chemin: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    disposition = [
        ("Ø120", 50, 100), ("M12", 150, 100), ("R25", 250, 100), ("2xØ14", 350, 100),
        ("12mm", 450, 100), ("1500kg", 50, 150), ("EX.26.396R1", 150, 150),
        ("DHYA.2", 300, 150), ("PIT", 50, 250), ("150(PIT)", 150, 250),
    ]
    for texte, x, y in disposition:
        page.insert_text((x, y), texte, fontsize=12)
    doc.save(chemin)
    doc.close()
    return chemin


def test_nombres_codes_et_designations_intacts_dans_l_image(tmp_path):
    pdf = _pdf_de_codes(tmp_path / "codes.pdf")
    words = extract.extraire_pdf(pdf, tmp_path, [1], dpi=DPI)[1]
    redigee = Image.open(tmp_path / "page_1_redacted.png").convert("RGB")
    brut = _rendu_brut(pdf, 1)

    par_texte = {w["text"]: w for w in words["words"]}
    for token in ("Ø120", "M12", "R25", "2xØ14", "12mm", "1500kg", "EX.26.396R1", "DHYA.2"):
        w = par_texte[token]
        assert _identique(_zone(redigee, w["bbox"]), _zone(brut, w["bbox"])), f"« {token} » a été altéré"

    # « PIT » (traduisible) est bien effacé...
    assert _texte_efface(_zone(brut, par_texte["PIT"]["bbox"]), _zone(redigee, par_texte["PIT"]["bbox"]))
    # ... et « 150(PIT) » : seul le suffixe est effacé, le nombre « 150 » reste
    w = par_texte["150(PIT)"]
    x0, y0, x1, y1 = w["bbox"]
    nombre = [x0, y0, w["suffix_bbox"][0] - 0.5, y1]
    assert _identique(_zone(redigee, nombre), _zone(brut, nombre)), "le nombre de 150(PIT) a été touché"
    assert _texte_efface(_zone(brut, w["suffix_bbox"]), _zone(redigee, w["suffix_bbox"]))


def test_aucune_etiquette_ne_retape_un_nombre_ou_un_code(tmp_path):
    pdf = _pdf_de_codes(tmp_path / "codes.pdf")
    words = extract.extraire_pdf(pdf, tmp_path, [1], dpi=DPI)[1]
    labels, _ = translate.traduire_labels_planche(words)
    assert [l["text"] for l in labels if any(c.isdigit() for c in l["text"])] == []
    assert sorted(l["source"] for l in labels) == ["(PIT)", "PIT"]


# --- contrôle : la rédaction a un effet VISUEL réel, pas seulement textuel -

def test_redaction_sans_effet_visuel_bascule_en_redaction_pil(tmp_path):
    """Bug trouvé sur un vrai plan fabricant (260002601__UP25_SPB) : ce PDF
    dessine son texte en tracés vectoriels (pas des glyphes de police),
    doublés d'une couche de texte invisible superposée pour la sélection/
    recherche — un montage PDF « CAD hybride » courant. `apply_redactions()`
    retire la couche de texte et RAPPORTE UN SUCCÈS, mais ne touche pas le
    tracé vectoriel visible s'il ne tient pas ENTIÈREMENT dans le petit
    rectangle de rédaction du mot (comportement par défaut de pymupdf :
    `graphics=1`, « remove graphics if contained in rectangle » — un tracé
    qui déborde, même légèrement, survit). `survie_nombres` ne peut pas voir
    ce bug : aucun nombre n'est en cause, c'est du texte anglais qui reste
    visible SOUS l'étiquette française posée par-dessus.

    Simulé ici : un rectangle plein qui déborde largement la bbox du mot
    invisible « PIT » (le « tracé vectoriel »)."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(70, 90, 220, 130), fill=(0, 0, 0))
    page.insert_text((100, 114), "PIT", fontsize=16, render_mode=3)
    pdf = tmp_path / "vecteur.pdf"
    doc.save(pdf)
    doc.close()

    brut = _rendu_brut(pdf, 1)
    words = extract.extraire_pdf(pdf, tmp_path, [1], dpi=DPI)[1]
    redigee = Image.open(tmp_path / "page_1_redacted.png").convert("RGB")

    w = [w for w in words["words"] if w["text"] == "PIT"][0]
    zone_brute = _zone(brut, w["bbox"])
    zone_redigee = _zone(redigee, w["bbox"])
    assert _texte_efface(zone_brute, zone_redigee), (
        "le tracé vectoriel visible doit disparaître de l'image rédigée "
        "(bascule en rédaction PIL attendue), même si apply_redactions() ne "
        "l'a pas retiré du PDF"
    )


# --- contrôle : les nombres SURVIVENT à la rédaction -----------------------

def test_survie_des_nombres_pass_sur_le_fixture(planche):
    survie = planche["words"]["survie_nombres"]
    assert survie["perdus"] == []
    assert survie["total"] > 20


def test_nombre_efface_par_la_redaction_est_detecte(tmp_path):
    """« PIT » (traduisible) et « 150 » se chevauchent : rédiger « PIT » efface
    aussi « 150 ». Le contrôle doit le voir."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.insert_text((100, 100), "PIT", fontsize=24)
    page.insert_text((110, 104), "150", fontsize=10)
    pdf = tmp_path / "chevauchement.pdf"
    doc.save(pdf); doc.close()

    words = extract.extraire_pdf(pdf, tmp_path, [1], dpi=DPI)[1]
    assert "150" in words["survie_nombres"]["perdus"]


def test_survie_visible_dans_le_rapport_de_verification(planche, deck):
    from core import verify
    pptx = planche["tmp"] / "deck.pptx"
    ok = verify.controle_cotes({PAGE: planche["words"]}, pptx)
    assert ok["survie_nombres"]["statut"] == "PASS"
    texte = verify.construire_rapport("x.pptx", ok, [], [], [], False)
    assert "Survie des nombres après rédaction : PASS" in texte

    ko_words = {**planche["words"], "survie_nombres": {"total": 30, "perdus": ["2400", "150"]}}
    ko = verify.controle_cotes({PAGE: ko_words}, pptx)
    assert ko["statut_cotes"].startswith("ÉCHEC")
    texte = verify.construire_rapport("x.pptx", ko, [], [], [], False)
    assert "Survie des nombres après rédaction : ÉCHEC" in texte
    assert "2400" in texte and "150" in texte and "BLOQUANT" in texte


def test_ecran_3_refuse_d_assembler_si_un_nombre_a_ete_efface(tmp_path):
    """Dans l'app réelle : nombre perdu -> erreur BLOQUANTE + bouton désactivé."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_app_etat import _app, _bouton, _etat_apres_assemblage

    at = _app()
    etat = _etat_apres_assemblage(tmp_path, etape=3)
    etat["words_par_page"] = {2: {"words": [], "survie_nombres": {"total": 30, "perdus": ["2400"]}}}
    for cle, val in etat.items():
        at.session_state[cle] = val
    at.run()
    assert not at.exception
    assert any("BLOQUANT" in e.value and "2400" in e.value for e in at.error)
    assert _bouton(at, "Assembler le PPTX →").disabled


def test_ecran_3_assemble_normalement_si_tous_les_nombres_survivent(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_app_etat import _app, _bouton, _etat_apres_assemblage

    at = _app()
    etat = _etat_apres_assemblage(tmp_path, etape=3)
    etat["words_par_page"] = {2: {"words": [], "survie_nombres": {"total": 30, "perdus": []}}}
    for cle, val in etat.items():
        at.session_state[cle] = val
    at.run()
    assert not at.exception
    assert not _bouton(at, "Assembler le PPTX →").disabled
