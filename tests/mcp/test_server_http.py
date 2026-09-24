# -*- coding: utf-8 -*-
"""
Test du serveur MCP réel, en process séparé, sur le vrai protocole
streamable-http (pas d'appel direct aux fonctions Python — cf.
tests/mcp/test_tools_local.py pour ça) : démarre `mcp_server.server`,
vérifie que le jeton Bearer est exigé, puis fait un échange MCP complet
(initialize -> list_tools -> call_tool) avec un vrai client.
"""
import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

RACINE = Path(__file__).resolve().parent.parent.parent
FIXTURE_PDF = RACINE / "tests" / "fixtures" / "DHYA2_test.pdf"
JETON = "jeton-de-test-mcp"
JETON_UPLOAD = "jeton-de-test-upload-pdf"  # distinct de JETON — cf. mcp_server/auth.py

pytestmark = pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="fixture PDF absente")


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def url_serveur(tmp_path_factory):
    port = _port_libre()
    env = {**os.environ, "MCP_AUTH_TOKEN": JETON, "PDF_UPLOAD_TOKEN": JETON_UPLOAD, "PORT": str(port)}
    # Sortie du serveur dans un FICHIER, pas un PIPE jamais lu : au-delà du
    # tampon du pipe (~64 Ko de logs, vite atteint par un rendu à 300 dpi),
    # le serveur se bloquait en écriture et les appels partaient en timeout.
    journal = tmp_path_factory.mktemp("serveur_mcp") / "serveur.log"
    sortie_serveur = open(journal, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server"],
        cwd=str(RACINE), env=env,
        stdout=sortie_serveur, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    pret = False
    for _ in range(50):
        if proc.poll() is not None:
            break
        try:
            httpx2.get(url, timeout=0.5)
            pret = True
            break
        except Exception:
            time.sleep(0.3)
    if not pret:
        proc.terminate()
        sortie_serveur.close()
        pytest.fail(f"le serveur MCP n'a pas démarré à temps : {journal.read_text(errors='replace')}")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    sortie_serveur.close()


def test_requete_sans_jeton_refusee(url_serveur):
    r = httpx2.post(url_serveur, json={}, timeout=5)
    assert r.status_code == 401


def test_mauvais_jeton_refuse(url_serveur):
    r = httpx2.post(url_serveur, json={}, headers={"Authorization": "Bearer mauvais-jeton"}, timeout=5)
    assert r.status_code == 401


def test_liste_les_6_outils_et_appelle_inventaire_pdf(url_serveur):
    """Reproduit le bug trouvé (mcp>=2 plafonne une réponse d'outil à 1 Mio
    côté client, sans réglage possible) : un `inventaire_pdf` sur un PDF de
    2 pages cassait avant le passage aux URLs de téléchargement
    (mcp_server/fichiers.py) — la réponse ne doit plus jamais contenir de
    contenu binaire inline, seulement des URLs + empreintes."""
    async def _run():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {JETON}"}, timeout=30) as http_client:
            async with streamable_http_client(url_serveur, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    outils = await session.list_tools()
                    noms = {t.name for t in outils.tools}
                    assert noms == {
                        "inventaire_pdf", "extraire_page", "traduire_mots",
                        "assembler_pptx", "verifier_rendu", "exporter_pdf",
                    }

                    pdf_b64 = base64.b64encode(FIXTURE_PDF.read_bytes()).decode("ascii")
                    resultat = await session.call_tool("inventaire_pdf", {"pdf_base64": pdf_b64})
                    assert not resultat.is_error, resultat.content
                    data = resultat.structured_content
                    assert data["pdf_id"]
                    assert data["n_pages"] == 2
                    for page in data["pages"]:
                        assert page["apercu_url"].startswith(url_serveur.rsplit("/mcp", 1)[0])
                        assert "apercu_png_base64" not in page

                    # extraire_page référence le PDF par pdf_id (mcp_server/pdf_cache.py)
                    # plutôt que de le retransmettre en base64 — c'est le point qui a
                    # fait hésiter un agent Dust réel face au volume répété.
                    resultat_page = await session.call_tool(
                        "extraire_page", {"pdf_id": data["pdf_id"], "page_num": 2, "dpi": 150, "role": "planche"}
                    )
                    assert not resultat_page.is_error, resultat_page.content
                    assert resultat_page.structured_content["page_num"] == 2

    asyncio.run(_run())


def test_upload_pdf_via_curl_externe_puis_pipeline_complet(url_serveur):
    """Simule ce qu'un agent Dust réel ferait dans son bac à sable — `curl`,
    PAS le client MCP Python qu'on contrôle par ailleurs dans ce fichier —
    pour téléverser le PDF hors canal MCP (mcp_server/pdf_cache.py) : upload
    multipart sur POST /pdfs avec PDF_UPLOAD_TOKEN (jeton DISTINCT de celui
    des appels d'outils, cf. mcp_server/auth.py), puis réutilisation du
    `pdf_id` obtenu dans inventaire_pdf et extraire_page via un vrai échange
    MCP (avec MCP_AUTH_TOKEN, cette fois). C'est précisément le scénario
    (upload externe non-Python) qui avait échappé aux tests précédents et
    qui a révélé, en conditions réelles avec Dust, qu'un agent n'a souvent
    AUCUN moyen de produire du base64 lui-même."""
    base_url = url_serveur.rsplit("/mcp", 1)[0]
    resultat_curl = subprocess.run(
        [
            "curl", "-sS", "-X", "POST", f"{base_url}/pdfs",
            "-H", f"Authorization: Bearer {JETON_UPLOAD}",
            "-F", f"pdf=@{FIXTURE_PDF}",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert resultat_curl.returncode == 0, resultat_curl.stderr
    data_upload = json.loads(resultat_curl.stdout)
    pdf_id = data_upload["pdf_id"]
    assert pdf_id
    assert data_upload["expire_dans_s"] > 0

    async def _run():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {JETON}"}, timeout=30) as http_client:
            async with streamable_http_client(url_serveur, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    resultat = await session.call_tool("inventaire_pdf", {"pdf_id": pdf_id})
                    assert not resultat.is_error, resultat.content
                    data = resultat.structured_content
                    assert data["pdf_id"] == pdf_id  # même id réutilisé, pas un nouveau
                    assert data["n_pages"] == 2

                    resultat_page = await session.call_tool(
                        "extraire_page", {"pdf_id": pdf_id, "page_num": 2, "dpi": 150, "role": "planche"}
                    )
                    assert not resultat_page.is_error, resultat_page.content
                    assert resultat_page.structured_content["page_num"] == 2

    asyncio.run(_run())


def _appel_mcp(url_serveur, appels):
    """Ouvre UNE session MCP réelle et y exécute `appels(session)` (coroutine)."""
    async def _run():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {JETON}"}, timeout=300) as http_client:
            async with streamable_http_client(url_serveur, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await appels(session)
    return asyncio.run(_run())


def test_erreur_d_outil_arrive_en_clair_cote_client(url_serveur):
    """Bug trouvé en auditant l'échec d'assembler_pptx : le SDK mcp (2.x) ne
    transmet au client QUE le texte d'une `ToolError` — toute autre exception
    (dont nos ValueError) arrive réduite à « Error executing tool <nom> »,
    sans le message. Le correctif précédent sur traduire_mots (message
    nommant le champ en cause) n'atteignait donc JAMAIS l'agent Dust : seuls
    les tests en appel Python direct le voyaient."""
    async def _appels(session):
        return await session.call_tool("traduire_mots", {"extraction_id": "jeton-inexistant"})

    resultat = _appel_mcp(url_serveur, _appels)
    assert resultat.is_error
    texte = resultat.content[0].text
    assert "extraction_id" in texte and "relancez extraire_page" in texte, texte


# Budget d'arguments par appel d'outil : quelques Ko, soit ~1-3k jetons. Un
# agent Dust réel a ~48k jetons de budget ; la planche A3 à 300 dpi en base64
# (~165k jetons estimés par l'agent lui-même) l'a fait échouer.
BUDGET_ARGUMENTS_OCTETS = 8_000


def test_scenario_agent_dust_references_uniquement(url_serveur):
    """Scénario complet tel qu'un agent Dust réel le vit : PDF A3 téléversé
    par `curl`, extraction à 300 dpi (planche 4961×3508 px, taille réelle
    du cas qui a échoué), puis chaque outil suivant ne reçoit QUE des
    identifiants (pdf_id, extraction_id, pptx_id) — jamais d'image, de
    PPTX ou de words_data en base64/JSON. Chaque argument envoyé est mesuré
    contre BUDGET_ARGUMENTS_OCTETS, et le PPTX produit doit contenir l'image
    de planche en PLEINE résolution (rien n'a été réduit pour tenir)."""
    import io
    import zipfile
    from PIL import Image

    base_url = url_serveur.rsplit("/mcp", 1)[0]
    upload = subprocess.run(
        ["curl", "-sS", "-X", "POST", f"{base_url}/pdfs",
         "-H", f"Authorization: Bearer {JETON_UPLOAD}", "-F", f"pdf=@{FIXTURE_PDF}"],
        capture_output=True, text=True, timeout=30,
    )
    assert upload.returncode == 0, upload.stderr
    pdf_id = json.loads(upload.stdout)["pdf_id"]

    tailles_arguments = {}

    async def _appels(session):
        async def appeler(nom, arguments):
            tailles_arguments[nom] = len(json.dumps(arguments))
            resultat = await session.call_tool(nom, arguments)
            assert not resultat.is_error, (nom, resultat.content)
            return resultat.structured_content

        await appeler("inventaire_pdf", {"pdf_id": pdf_id})
        garde = await appeler("extraire_page", {"pdf_id": pdf_id, "page_num": 1, "dpi": 300, "role": "garde"})
        planche = await appeler("extraire_page", {"pdf_id": pdf_id, "page_num": 2, "dpi": 300, "role": "planche"})
        trad = await appeler("traduire_mots", {"extraction_id": planche["extraction_id"], "role": "planche"})
        assert trad["labels"]

        projet_json = {
            "meta": {"numero": "LDMCP002", "client": "Client Scénario Dust", "dessinateur": "AB",
                     "indice": "R00", "type_equipement": "non accompagné"},
            "specs": {"table": [["Modèle", "TEST"]]},
            "planches": [{"extraction_id": planche["extraction_id"]}],
        }
        if "vue_3d_url" in garde:
            projet_json["view3d"] = {"extraction_id": garde["extraction_id"]}
        assemble = await appeler("assembler_pptx", {"projet_json": projet_json})
        assert assemble["pptx_id"]

        verif = await appeler("verifier_rendu", {
            "pptx_id": assemble["pptx_id"],
            "words_par_page": {"2": planche["extraction_id"]},
            "meta": {"numero": "LDMCP002", "client": "Client Scénario Dust"},
        })
        assert verif["rapport_cotes"]["survie_nombres"]["statut"] == "PASS"
        export = await appeler("exporter_pdf", {
            "pptx_id": assemble["pptx_id"], "valide": True,
            "pptx_sha256_verifie": verif["pptx_sha256"], "moteur": verif["moteur"],
        })
        return assemble, export

    assemble, export = _appel_mcp(url_serveur, _appels)

    for nom, taille in tailles_arguments.items():
        assert taille < BUDGET_ARGUMENTS_OCTETS, f"{nom} : {taille} octets d'arguments"

    pptx = httpx2.get(assemble["pptx_url"], timeout=30)
    assert pptx.status_code == 200
    with zipfile.ZipFile(io.BytesIO(pptx.content)) as z:
        tailles_images = [Image.open(io.BytesIO(z.read(n))).size
                          for n in z.namelist() if n.startswith("ppt/media/") and n.endswith(".png")]
    assert (4961, 3508) in tailles_images, tailles_images

    pdf = httpx2.get(export["pdf_url"], timeout=30)
    assert pdf.content[:5] == b"%PDF-"


def test_assembler_pptx_erreur_nomme_le_champ_cote_client(url_serveur):
    """Bug d'origine (indépendant de la taille) : assembler_pptx échouait sans
    message exploitable. Une entrée malformée doit nommer le champ en cause,
    lisible PAR L'AGENT à travers le vrai protocole."""
    async def _appels(session):
        return await session.call_tool("assembler_pptx", {"projet_json": {
            "meta": {"numero": "LDMCP003", "client": "X", "dessinateur": "AB",
                     "indice": "R00", "type_equipement": "non accompagné"},
            "planches": [{"page_n": 2}],
        }})

    resultat = _appel_mcp(url_serveur, _appels)
    assert resultat.is_error
    assert "planches[0]" in resultat.content[0].text, resultat.content[0].text


def test_upload_pdf_sans_jeton_bearer_refuse(url_serveur):
    """Contrairement au téléchargement de SORTIE (fichiers.py, exempté du
    Bearer — destiné au navigateur de l'utilisateur final), l'upload
    d'ENTRÉE est un point d'entrée serveur-à-serveur : protégé par un jeton
    Bearer (PDF_UPLOAD_TOKEN — distinct de celui de /mcp, cf. test suivant)."""
    base_url = url_serveur.rsplit("/mcp", 1)[0]
    r = httpx2.post(
        f"{base_url}/pdfs", files={"pdf": ("x.pdf", b"%PDF-1.4 pas un vrai pdf")}, timeout=10,
    )
    assert r.status_code == 401


def test_jetons_upload_et_mcp_non_interchangeables(url_serveur):
    """Cœur de la portée réduite de PDF_UPLOAD_TOKEN (mcp_server/auth.py) :
    une fuite de ce jeton (réaliste, destiné à être collé en clair dans les
    instructions d'un agent Dust) ne doit PAS donner accès à /mcp (tous les
    outils, y compris exporter_pdf) — et réciproquement, MCP_AUTH_TOKEN ne
    doit PAS marcher sur /pdfs."""
    base_url = url_serveur.rsplit("/mcp", 1)[0]

    # Le jeton d'upload ne doit PAS ouvrir /mcp.
    r1 = httpx2.post(url_serveur, json={}, headers={"Authorization": f"Bearer {JETON_UPLOAD}"}, timeout=5)
    assert r1.status_code == 401

    # Le jeton MCP ne doit PAS ouvrir /pdfs.
    r2 = httpx2.post(
        f"{base_url}/pdfs",
        files={"pdf": ("x.pdf", b"%PDF-1.4 pas un vrai pdf")},
        headers={"Authorization": f"Bearer {JETON}"},
        timeout=10,
    )
    assert r2.status_code == 401


def test_url_publiee_telechargeable_sans_jeton_bearer_et_a_usage_unique(url_serveur):
    """Le lien de téléchargement (mcp_server/fichiers.py) doit être cliquable
    directement par l'utilisateur final dans son navigateur — donc
    accessible SANS le jeton Bearer serveur-à-serveur — mais protégé par son
    propre jeton imprévisible, à usage unique."""
    async def _appeler_inventaire():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {JETON}"}, timeout=30) as http_client:
            async with streamable_http_client(url_serveur, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    pdf_b64 = base64.b64encode(FIXTURE_PDF.read_bytes()).decode("ascii")
                    resultat = await session.call_tool("inventaire_pdf", {"pdf_base64": pdf_b64})
                    return resultat.structured_content["pages"][0]["apercu_url"]

    apercu_url = asyncio.run(_appeler_inventaire())

    # Téléchargeable SANS en-tête Authorization du tout.
    r1 = httpx2.get(apercu_url, timeout=10)
    assert r1.status_code == 200
    assert r1.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert r1.headers["content-type"] == "image/png"

    # À usage unique : un deuxième GET sur le même lien échoue.
    r2 = httpx2.get(apercu_url, timeout=10)
    assert r2.status_code == 404
