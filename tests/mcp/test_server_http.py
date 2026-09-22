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

pytestmark = pytest.mark.skipif(not FIXTURE_PDF.exists(), reason="fixture PDF absente")


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def url_serveur():
    port = _port_libre()
    env = {**os.environ, "MCP_AUTH_TOKEN": JETON, "PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.server"],
        cwd=str(RACINE), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
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
        sortie = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        proc.terminate()
        pytest.fail(f"le serveur MCP n'a pas démarré à temps : {sortie}")
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


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
                    assert data["n_pages"] == 2
                    for page in data["pages"]:
                        assert page["apercu_url"].startswith(url_serveur.rsplit("/mcp", 1)[0])
                        assert "apercu_png_base64" not in page

    asyncio.run(_run())


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
