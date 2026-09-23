# -*- coding: utf-8 -*-
"""
Test local des 6 outils MCP, appelés directement comme des fonctions Python
(pas de client/serveur MCP réel ici — cf. tests/mcp/test_server_http.py pour
un échange MCP complet, protocole réel). Pipeline complet sur
tests/fixtures/DHYA2_test.pdf : inventaire -> extraction (garde + planche)
-> traduction -> assemblage -> vérification -> export PDF (refus sans
validation, puis avec).

Les outils ne renvoient jamais de contenu binaire inline (base64) mais une
URL de téléchargement à usage unique (cf. mcp_server/fichiers.py — le SDK
MCP plafonne une réponse d'outil à 1 Mio, dépassé par un vrai PPTX/planche).
`_recuperer_base64` simule ce que ferait un vrai client : un GET sur l'URL,
puis un ré-encodage en base64 pour le passer à l'outil suivant. Ici, appel
direct au registre plutôt qu'un GET HTTP réel (pas de serveur dans ces
tests) — le téléchargement HTTP réel est couvert par test_server_http.py.
"""
import base64
from pathlib import Path

import pytest

from mcp_server import fichiers
from mcp_server.tools import assemblage, export, extraction, inventaire, traduction, verification

FIXTURE_PDF = Path(__file__).resolve().parent.parent / "fixtures" / "DHYA2_test.pdf"
pytestmark = pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="fixture PDF absente")


def _jeton_de(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def _contenu_publie(url: str) -> bytes:
    resultat = fichiers.recuperer_et_invalider(_jeton_de(url))
    assert resultat is not None, f"fichier publié introuvable : {url}"
    return resultat["contenu"]


def _recuperer_base64(url: str) -> str:
    return base64.b64encode(_contenu_publie(url)).decode("ascii")


@pytest.fixture(scope="module")
def pdf_base64():
    return base64.b64encode(FIXTURE_PDF.read_bytes()).decode("ascii")


@pytest.fixture(scope="module")
def pdf_id(pdf_base64):
    """`inventaire_pdf` n'est appelé qu'UNE fois par pipeline (cf.
    mcp_server/pdf_cache.py) — les tests suivants réutilisent le pdf_id
    qu'il retourne, comme le ferait l'agent Dust réel, au lieu de
    retransmettre le PDF en base64 à chaque outil."""
    return inventaire.inventaire_pdf(pdf_base64)["pdf_id"]


def test_inventaire_pdf(pdf_base64):
    resultat = inventaire.inventaire_pdf(pdf_base64)
    assert resultat["pdf_id"]
    assert resultat["n_pages"] == 2
    assert len(resultat["pages"]) == 2
    for page in resultat["pages"]:
        assert page["apercu_url"].startswith("http")
        assert _contenu_publie(page["apercu_url"])[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(page["page_size_pts"]) == 2


def test_extraire_page_garde(pdf_id):
    resultat = extraction.extraire_page(pdf_id, page_num=1, dpi=150, role="garde")
    assert resultat["page_num"] == 1
    assert "image_url" in resultat
    assert "survie_nombres" not in resultat  # role=garde : rediger=False, aucune rédaction
    # La vue 3D peut échouer à s'extraire sur ce fixture minimal (pas de
    # tableau specs distinct à masquer) — les deux issues sont acceptables,
    # seul un plantage de l'outil ne le serait pas.
    assert "vue_3d_url" in resultat or "vue_3d_erreur" in resultat


def test_extraire_page_planche(pdf_id):
    resultat = extraction.extraire_page(pdf_id, page_num=2, dpi=150, role="planche")
    assert resultat["page_num"] == 2
    assert _contenu_publie(resultat["image_url"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert resultat["survie_nombres"]["perdus"] == []
    assert resultat["redaction_pil_fallback"] == []
    assert any(w["translatable"] for w in resultat["words"])


def test_extraire_page_role_invalide(pdf_id):
    with pytest.raises(ValueError):
        extraction.extraire_page(pdf_id, page_num=1, role="inconnu")


def test_extraire_page_hors_limites(pdf_id):
    with pytest.raises(ValueError):
        extraction.extraire_page(pdf_id, page_num=99)


def test_extraire_page_pdf_id_inconnu_refuse():
    with pytest.raises(ValueError, match="pdf_id"):
        extraction.extraire_page("jeton-inexistant", page_num=1)


def test_pdf_trop_volumineux_refuse():
    faux_pdf_base64 = base64.b64encode(b"x" * (51 * 1_000_000)).decode("ascii")
    with pytest.raises(ValueError, match="volumineux"):
        inventaire.inventaire_pdf(faux_pdf_base64)


def test_traduire_mots_planche(pdf_id):
    page = extraction.extraire_page(pdf_id, page_num=2, dpi=150, role="planche")
    resultat = traduction.traduire_mots(
        {"page_size_pts": page["page_size_pts"], "words": page["words"]}, role="planche",
    )
    assert isinstance(resultat["labels"], list) and resultat["labels"]
    assert resultat["page_w_pt"] == page["page_size_pts"][0]


def test_traduire_mots_glossaire_version_non_supportee(pdf_id):
    page = extraction.extraire_page(pdf_id, page_num=2, dpi=150, role="planche")
    with pytest.raises(ValueError, match="glossaire_version"):
        traduction.traduire_mots(
            {"page_size_pts": page["page_size_pts"], "words": page["words"]},
            role="planche", glossaire_version="2026-01",
        )


# ---------------------------------------------------------------------------
# Pipeline complet : assemblage -> vérification -> export
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def projet_assemble(pdf_id):
    page_planche = extraction.extraire_page(pdf_id, page_num=2, dpi=150, role="planche")
    image_planche_b64 = _recuperer_base64(page_planche["image_url"])
    trad_planche = traduction.traduire_mots(
        {"page_size_pts": page_planche["page_size_pts"], "words": page_planche["words"]},
        role="planche",
    )

    projet_json = {
        "meta": {
            "numero": "LDMCP001", "client": "Client Test MCP", "dessinateur": "AB",
            "indice": "R00", "type_equipement": "non accompagné",
        },
        "specs": {"table": [["Modèle", "TEST"]]},
        "planches": [{
            "image_base64": image_planche_b64,
            "page_n": 2,
            "page_w_pt": page_planche["page_size_pts"][0],
            "labels": trad_planche["labels"],
        }],
        "hors_glossaire": trad_planche["hors_glossaire"],
    }
    resultat = assemblage.assembler_pptx(projet_json)
    pptx_base64 = _recuperer_base64(resultat["pptx_url"])  # jeton consommé une seule fois, ici
    return resultat, pptx_base64, {2: page_planche}


def test_assembler_pptx(projet_assemble):
    resultat, pptx_base64, _ = projet_assemble
    assert resultat["pptx_sha256"]
    assert base64.b64decode(pptx_base64)[:2] == b"PK"  # signature ZIP/OOXML
    assert resultat["resume"]["nb_slides"] >= 3  # garde + specs + 1 planche
    assert resultat["resume"]["nb_planches"] == 1


def test_assembler_pptx_meta_incomplete_refuse():
    with pytest.raises(ValueError, match="numero"):
        assemblage.assembler_pptx({"meta": {"client": "X", "dessinateur": "AB",
                                             "indice": "R00", "type_equipement": "non accompagné"}})


def test_assembler_pptx_numero_mal_forme_refuse():
    with pytest.raises(ValueError, match="LDxxxxx"):
        assemblage.assembler_pptx({"meta": {
            "numero": "PASBONDUTOUT", "client": "X", "dessinateur": "AB",
            "indice": "R00", "type_equipement": "non accompagné",
        }})


@pytest.fixture(scope="module")
def rendu_verifie(projet_assemble):
    _, pptx_base64, words_par_page = projet_assemble
    words_par_page_json = {str(n): wd for n, wd in words_par_page.items()}
    resultat = verification.verifier_rendu(
        pptx_base64, words_par_page=words_par_page_json,
        meta={"numero": "LDMCP001", "client": "Client Test MCP"},
    )
    return pptx_base64, resultat


def test_verifier_rendu(rendu_verifie):
    _, resultat = rendu_verifie
    assert resultat["moteur"] in ("powerpoint", "libreoffice")
    assert len(resultat["slides"]) >= 3
    for slide in resultat["slides"]:
        assert _contenu_publie(slide["url"])[:8] == b"\x89PNG\r\n\x1a\n"
    assert resultat["pptx_sha256"]
    assert resultat["rapport_cotes"] is not None
    assert resultat["rapport_cotes"]["survie_nombres"]["statut"] == "PASS"


def test_exporter_pdf_refuse_sans_validation(rendu_verifie):
    pptx_base64, _ = rendu_verifie
    with pytest.raises(ValueError, match="[Rr]efus"):
        export.exporter_pdf(pptx_base64, valide=False)


def test_exporter_pdf_refuse_si_empreinte_ne_correspond_pas(rendu_verifie):
    pptx_base64, _ = rendu_verifie
    with pytest.raises(ValueError, match="empreinte"):
        export.exporter_pdf(pptx_base64, valide=True, pptx_sha256_verifie="0" * 64)


def test_exporter_pdf_valide_avec_empreinte_correcte(rendu_verifie):
    pptx_base64, resultat_verif = rendu_verifie
    resultat = export.exporter_pdf(
        pptx_base64, valide=True,
        pptx_sha256_verifie=resultat_verif["pptx_sha256"],
        moteur=resultat_verif["moteur"],
    )
    assert resultat["pdf_sha256"]
    assert resultat["nom_fichier"].endswith(".pdf")
    assert _contenu_publie(resultat["pdf_url"])[:5] == b"%PDF-"
