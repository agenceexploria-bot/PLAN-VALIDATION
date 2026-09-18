# -*- coding: utf-8 -*-
"""
Tests de non-régression pour core/extract.py — construits à partir de bugs
réels trouvés en faisant tourner le pipeline sur tests/fixtures/DHYA2_test.pdf
(cf. rapport de session). Ne pas affaiblir ces assertions sans revérifier au
rendu réel (core/render.py) que le bug ne revient pas.
"""
from pathlib import Path

import fitz
import pytest

from core import extract

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "DHYA2_test.pdf"


def test_dimension_composee_jamais_traduisible():
    """'2000X1500' (cote largeur x profondeur, un seul token pymupdf) ne doit
    JAMAIS être traité comme traduisible : sinon `fused_number` le prend pour
    une cote fusionnée à un suffixe texte et le réétiquette par-dessus le
    nombre voisin (chevauchement visuel constaté au rendu réel)."""
    assert extract.is_translatable("2000X1500") is False
    assert extract.is_translatable("2835X1800X150") is False
    assert extract.fused_number("2000X1500") is None


def test_fragment_cote_isole_jamais_traduisible():
    """Si un fabricant tokenise la continuation séparément (ex. 'X1500' seul),
    même règle : ce n'est pas un libellé."""
    assert extract.is_translatable("X1500") is False


def test_vraie_cote_fusionnee_reste_detectee():
    """Ne pas sur-corriger : une VRAIE cote fusionnée à un suffixe texte
    (ex. '9700(FFL)') doit toujours être reconnue comme telle."""
    assert extract.is_translatable("9700(FFL)") is True
    assert extract.fused_number("9700(FFL)") == "9700"


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="fixture PDF absente")
def test_extraction_reelle_sur_pdf_dhya2(tmp_path):
    """Extraction de bout en bout sur un vrai plan fabricant (2 pages,
    EN + cotation turque) : ne doit lever aucune exception et produire les
    fichiers attendus."""
    resultats = extract.extraire_pdf(FIXTURE_PDF, tmp_path, pages_1based=[1, 2], dpi=150)
    assert set(resultats) == {1, 2}
    assert (tmp_path / "page_1_redacted.png").exists()
    assert (tmp_path / "page_1_words.json").exists()
    # Le rendu brut séparé (page_1.png) a été retiré (optimisation mémoire,
    # audit RAM Render) : inutilisé en aval, il ne doit plus être produit du
    # tout — pas seulement absent par oubli.
    assert not (tmp_path / "page_1.png").exists()
    # La cote composée ne doit pas apparaître comme "translatable" dans les
    # mots extraits (régression du bug ci-dessus, sur les vraies données).
    mots_dimension = [w for w in resultats[1]["words"] if "X1500" in w["text"] or "X1800" in w["text"]]
    assert mots_dimension, "le mot de test a disparu du PDF de référence"
    assert all(not w["translatable"] for w in mots_dimension)


def test_pages_sans_texte_detecte_page_scannee(tmp_path):
    """Une page sans aucune couche texte (PDF scanné) doit être détectée dès
    l'étape 1, avant toute extraction lourde (bug audit : gap non
    documenté, aucun avertissement n'était affiché à l'utilisateur)."""
    doc = fitz.open()
    doc.new_page(width=400, height=300)
    pdf_path = tmp_path / "scan.pdf"
    doc.save(pdf_path)
    doc.close()

    assert extract.pages_sans_texte(pdf_path) == [1]


def test_est_pdf_scanne_reellement_branche_sur_extraction(tmp_path):
    """est_pdf_scanne() était définie mais jamais appelée dans le pipeline
    (bug audit) : une fois la page réellement extraite (étape 3), elle doit
    signaler l'absence de couche texte — c'est ce statut qui empêche le
    contrôle de cotes d'afficher PASS à tort sur un PDF scanné."""
    doc = fitz.open()
    doc.new_page(width=400, height=300)
    pdf_path = tmp_path / "scan.pdf"
    doc.save(pdf_path)
    doc.close()

    resultats = extract.extraire_pdf(pdf_path, tmp_path, pages_1based=[1], dpi=100)
    assert extract.est_pdf_scanne(resultats[1]) is True


@pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="fixture PDF absente")
def test_rendu_planche_en_png_direct_pas_en_palette(tmp_path):
    """Les rendus pleine page restent en PNG RGB direct : la conversion en
    palette coûtait ~+69 Mo de pic mémoire par planche A3 à 300 dpi (OOM
    constaté sur Render, palier 512 Mo). Ne pas la réintroduire ici sans
    remesurer le pic mémoire."""
    from PIL import Image

    extract.extraire_pdf(FIXTURE_PDF, tmp_path, [2], dpi=100)
    with Image.open(tmp_path / "page_2_redacted.png") as im:
        assert im.mode == "RGB"


def test_une_seule_extraction_a_la_fois_entre_sessions(monkeypatch, tmp_path):
    """Deux sessions qui extraient en parallèle additionnaient leurs pics
    mémoire (pages loguées deux fois en prod, puis OOM) : le verrou global
    doit sérialiser les extractions, quel que soit le nombre de threads."""
    import threading
    import time

    actifs, pic = [0], [0]
    garde = threading.Lock()

    def page_factice(doc, pno, out, dpi, generer_image):
        with garde:
            actifs[0] += 1
            pic[0] = max(pic[0], actifs[0])
        time.sleep(0.05)
        with garde:
            actifs[0] -= 1
        return {"page": pno + 1, "words": []}

    monkeypatch.setattr(extract, "extraire_page", page_factice)
    monkeypatch.setattr(extract.fitz, "open", lambda _p: type("D", (), {"close": lambda self: None})())

    threads = [
        threading.Thread(target=extract.extraire_pdf, args=(Path("x.pdf"), tmp_path, [1, 2, 3]))
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert pic[0] == 1
