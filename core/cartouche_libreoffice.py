# -*- coding: utf-8 -*-
"""
core/cartouche_libreoffice.py — adaptation du cartouche des gabarits Vertical
au moteur LibreOffice, appliquée UNIQUEMENT à la copie jetable envoyée à
`soffice` (cf. mcp_server/util.py::copie_allegee). Le PPTX livré et les
gabarits ne sont jamais modifiés.

Mécanisme constaté (mesuré sur templates/LD82040.pptx et LD64397.pptx, rendus
LibreOffice) : la hauteur de contenu d'une cellule fusionnée verticalement
(`rowSpan`) est imputée ENTIÈREMENT à la dernière ligne qu'elle couvre, au
lieu d'être comparée à la hauteur cumulée des lignes comme le fait
PowerPoint. Les paragraphes vides comptent. Dans les gabarits, l'adresse
(3 paragraphes vides d'espacement + 3 lignes) et « Bon pour fabrication /
Date / Signature » (3 lignes + 4 paragraphes vides) couvrent 4 lignes de
85,5 pt au total, mais la dernière ne fait que 30,3 pt : elle est gonflée,
le tableau déborde de 48 à 64 pt sous le bord de la page, et LibreOffice
n'exporte pas ce qui est hors page (mention légale absente du PDF).

Correction, pour chaque tableau de la slide :
  1. le trait inférieur est ramené à l'intérieur de la page (le tableau des
     gabarits finit pile au bord : la demi-épaisseur du trait dépassait) ;
  2. toute cellule fusionnée verticalement perd ses paragraphes vides de
     tête et de fin — ceux de tête servaient à pousser le texte vers le bas :
     la cellule est alors ancrée en bas (l'adresse reste sous le logo) ;
  3. si le contenu dépasse encore la hauteur de la dernière ligne couverte,
     l'interligne est fixé juste assez bas pour tenir.
Le haut du tableau et toutes les autres formes restent en place : la zone du
dessin, dérivée du haut du cartouche (core/assemble.py), ne bouge pas.
"""
import math

from lxml import etree

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
EMU_PAR_PT = 12700

# Marge entre le bas du tableau et le bord de la page : de quoi loger la
# demi-épaisseur des traits du cartouche (0,33 pt dans les gabarits).
MARGE_BAS_PAGE_PT = 0.5
# Hauteur de ligne par défaut de LibreOffice pour un interligne proportionnel
# (Carlito/Calibri) : ~1,2 fois la taille de police.
FACTEUR_LIGNE_SIMPLE = 1.2
TAILLE_POLICE_DEFAUT_PT = 18.0
# Marge basse d'une cellule ancrée en bas : le texte ne touche pas le trait.
MARGE_TEXTE_ANCRE_BAS_EMU = 3 * EMU_PAR_PT


def _vide(p) -> bool:
    return not "".join(t.text or "" for t in p.iter(A + "t")).strip()


def _taille_police_pt(p) -> float:
    for tag in ("rPr", "endParaRPr"):
        for e in p.iter(A + tag):
            if e.get("sz"):
                return int(e.get("sz")) / 100
    return TAILLE_POLICE_DEFAUT_PT


def _hauteur_paragraphe_pt(p) -> float:
    """Hauteur d'une ligne du paragraphe telle que LibreOffice la compte :
    interligne fixe (`spcPts`) tel quel, proportionnel (`spcPct`) rapporté à
    la taille de police."""
    pPr = p.find(A + "pPr")
    ln = pPr.find(A + "lnSpc") if pPr is not None else None
    if ln is not None and len(ln):
        if ln[0].tag == A + "spcPts":
            return int(ln[0].get("val")) / 100
        if ln[0].tag == A + "spcPct":
            return int(ln[0].get("val")) / 100000 * FACTEUR_LIGNE_SIMPLE * _taille_police_pt(p)
    return FACTEUR_LIGNE_SIMPLE * _taille_police_pt(p)


def _fixer_interligne(p, interligne_pt: float) -> None:
    pPr = p.find(A + "pPr")
    if pPr is None:
        pPr = etree.Element(A + "pPr")
        p.insert(0, pPr)
    ancien = pPr.find(A + "lnSpc")
    if ancien is not None:
        pPr.remove(ancien)
    ln = etree.Element(A + "lnSpc")
    etree.SubElement(ln, A + "spcPts").set("val", str(int(interligne_pt * 100)))
    pPr.insert(0, ln)  # lnSpc est le premier enfant de pPr (schéma OOXML)


def _reduire_police(p, taille_max_pt: float) -> None:
    """Police ramenée sous l'interligne (par demi-point) : sinon les lignes
    se chevauchent."""
    sz = str(int(taille_max_pt * 2) * 50)
    for tag in ("rPr", "endParaRPr"):
        for e in p.iter(A + tag):
            e.set("sz", sz)


def _ajuster_cellule_fusionnee(tc, hauteur_derniere_ligne_emu: int) -> None:
    corps = tc.find(A + "txBody")
    if corps is None:
        return
    paras = corps.findall(A + "p")
    tete_retiree = False
    while len(paras) > 1 and _vide(paras[0]):
        corps.remove(paras.pop(0))
        tete_retiree = True
    while len(paras) > 1 and _vide(paras[-1]):
        corps.remove(paras.pop())

    tcPr = tc.find(A + "tcPr")
    if tcPr is None:
        tcPr = etree.SubElement(tc, A + "tcPr")
    if tete_retiree:
        tcPr.set("anchor", "b")
        if int(tcPr.get("marB", 45720)) < MARGE_TEXTE_ANCRE_BAS_EMU:
            tcPr.set("marB", str(MARGE_TEXTE_ANCRE_BAS_EMU))
    # Marges par défaut OOXML : 3,6 pt en haut et en bas.
    marges_pt = (int(tcPr.get("marT", 45720)) + int(tcPr.get("marB", 45720))) / EMU_PAR_PT
    disponible_pt = hauteur_derniere_ligne_emu / EMU_PAR_PT - marges_pt
    if sum(_hauteur_paragraphe_pt(p) for p in paras) > disponible_pt:
        interligne = math.floor(disponible_pt / len(paras) * 10) / 10
        for p in paras:
            _fixer_interligne(p, interligne)
            if _taille_police_pt(p) > interligne:
                _reduire_police(p, interligne)


def _ajuster_tableau(cadre, hauteur_slide_emu: int) -> None:
    lignes = cadre.findall(f".//{A}tbl/{A}tr")
    off = cadre.find(f"{P}xfrm/{A}off")
    if not lignes or off is None:
        return
    bas = int(off.get("y")) + sum(int(tr.get("h")) for tr in lignes)
    depassement = bas - (hauteur_slide_emu - int(MARGE_BAS_PAGE_PT * EMU_PAR_PT))
    if depassement > 0:
        derniere = lignes[-1]
        derniere.set("h", str(int(derniere.get("h")) - depassement))

    hauteurs = [int(tr.get("h")) for tr in lignes]
    for i, tr in enumerate(lignes):
        for tc in tr.findall(A + "tc"):
            portee = int(tc.get("rowSpan", "1"))
            if portee > 1:
                _ajuster_cellule_fusionnee(tc, hauteurs[min(i + portee, len(lignes)) - 1])


def ajuster_slide_xml(xml: bytes, hauteur_slide_emu: int) -> bytes:
    """Retourne le XML d'une slide (`ppt/slides/slideN.xml`) dont chaque
    tableau est adapté au rendu LibreOffice (cf. docstring de module). Une
    slide sans tableau est renvoyée telle quelle, octet pour octet."""
    racine = etree.fromstring(xml)
    cadres = [gf for gf in racine.iter(P + "graphicFrame") if gf.find(f".//{A}tbl") is not None]
    if not cadres:
        return xml
    for cadre in cadres:
        _ajuster_tableau(cadre, hauteur_slide_emu)
    return etree.tostring(racine, xml_declaration=True, encoding="UTF-8", standalone=True)


def hauteur_slide_emu(presentation_xml: bytes) -> int:
    """Hauteur des slides lue dans `ppt/presentation.xml`."""
    return int(etree.fromstring(presentation_xml).find(P + "sldSz").get("cy"))
