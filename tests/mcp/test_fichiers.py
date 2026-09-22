# -*- coding: utf-8 -*-
"""
Tests de mcp_server/fichiers.py — le mécanisme de contournement de la limite
de 1 Mio/évènement SSE du SDK MCP (cf. docstring du module) : jeton
imprévisible à usage unique, isolation entre deux publications, expiration.
"""
import hashlib
import os
import tempfile
import time

from mcp_server import fichiers


def _jeton_de(url: str) -> str:
    return url.rsplit("/", 1)[-1]


def test_publier_puis_recuperer_rend_le_bon_contenu(tmp_path):
    source = tmp_path / "x.png"
    source.write_bytes(b"contenu-image-1")

    publication = fichiers.publier(source, "x.png", "image/png")
    assert publication["url"].endswith(fichiers.PREFIXE_ROUTE + "/" + _jeton_de(publication["url"]))
    assert publication["sha256"] == hashlib.sha256(b"contenu-image-1").hexdigest()
    assert publication["octets"] == len(b"contenu-image-1")

    resultat = fichiers.recuperer_et_invalider(_jeton_de(publication["url"]))
    assert resultat["contenu"] == b"contenu-image-1"
    assert resultat["content_type"] == "image/png"
    assert resultat["nom_fichier"] == "x.png"


def test_usage_unique_deuxieme_recuperation_echoue(tmp_path):
    source = tmp_path / "x.png"
    source.write_bytes(b"une-seule-fois")
    publication = fichiers.publier(source, "x.png", "image/png")
    jeton = _jeton_de(publication["url"])

    assert fichiers.recuperer_et_invalider(jeton) is not None
    assert fichiers.recuperer_et_invalider(jeton) is None


def test_jeton_inconnu_ne_leve_pas_et_retourne_none():
    assert fichiers.recuperer_et_invalider("jeton-qui-n-existe-pas") is None


def test_deux_publications_sont_totalement_isolees(tmp_path):
    """Pas de risque de mélanger les fichiers de deux appels différents :
    deux publications ont des jetons distincts, chacun ne menant qu'à SON
    propre fichier."""
    a = tmp_path / "a.png"
    a.write_bytes(b"fichier-A-utilisateur-1")
    b = tmp_path / "b.png"
    b.write_bytes(b"fichier-B-utilisateur-2")

    pub_a = fichiers.publier(a, "a.png", "image/png")
    pub_b = fichiers.publier(b, "b.png", "image/png")

    assert pub_a["url"] != pub_b["url"]

    resultat_a = fichiers.recuperer_et_invalider(_jeton_de(pub_a["url"]))
    resultat_b = fichiers.recuperer_et_invalider(_jeton_de(pub_b["url"]))
    assert resultat_a["contenu"] == b"fichier-A-utilisateur-1"
    assert resultat_b["contenu"] == b"fichier-B-utilisateur-2"

    # Le jeton de A ne redonne jamais le contenu de B, ni l'inverse — déjà
    # garanti par le test précédent (jetons différents), vérifié ici pour
    # de bon : consommer A ne doit pas affecter le fichier de B.
    assert fichiers.recuperer_et_invalider(_jeton_de(pub_a["url"])) is None
    assert resultat_b["contenu"] == b"fichier-B-utilisateur-2"


def test_expiration_purge_un_fichier_jamais_telecharge(tmp_path, monkeypatch):
    monkeypatch.setattr(fichiers, "DUREE_VIE_SECONDES", 0)
    source = tmp_path / "expire.png"
    source.write_bytes(b"jamais-recupere")
    publication = fichiers.publier(source, "expire.png", "image/png")
    jeton = _jeton_de(publication["url"])

    # Une nouvelle publication déclenche le balayage paresseux qui purge
    # l'entrée expirée (durée de vie 0 = expirée dès sa création).
    autre = tmp_path / "autre.png"
    autre.write_bytes(b"autre-contenu")
    fichiers.publier(autre, "autre.png", "image/png")

    assert fichiers.recuperer_et_invalider(jeton) is None


def test_base_url_publique_defaut_local(monkeypatch):
    monkeypatch.delenv("MCP_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.setenv("PORT", "9999")
    assert fichiers.base_url_publique() == "http://127.0.0.1:9999"


def test_base_url_publique_configuree_prioritaire(monkeypatch):
    monkeypatch.setenv("MCP_PUBLIC_BASE_URL", "https://plan-validation-mcp.example.com/")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://autre-url-render.onrender.com")
    assert fichiers.base_url_publique() == "https://plan-validation-mcp.example.com"


def test_base_url_publique_replie_sur_render_external_url(monkeypatch):
    monkeypatch.delenv("MCP_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://plan-validation-mcp.onrender.com")
    assert fichiers.base_url_publique() == "https://plan-validation-mcp.onrender.com"


def _vieillir(chemin, secondes_dans_le_passe):
    t = time.time() - secondes_dans_le_passe
    os.utime(chemin, (t, t))


def test_purge_au_demarrage_supprime_les_vrais_orphelins(tmp_path, monkeypatch):
    """Un dossier de publication VIEUX (au-delà du seuil) laissé par une
    instance précédente du serveur (redémarrage sans arrêt propre — OOM-kill
    Render déjà observé sur ce projet) doit être nettoyé au démarrage du
    process suivant. Le répertoire temporaire système est monkeypatché sur
    un `tmp_path` isolé, pour ne jamais toucher le vrai dossier `_DOSSIER`
    de CE process (encore utilisé par les autres tests de ce module)."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    orphelin = tmp_path / f"{fichiers.PREFIXE_DOSSIER}vieuxprocess"
    orphelin.mkdir()
    (orphelin / "jamais-nettoye.png").write_bytes(b"orphelin")
    _vieillir(orphelin, fichiers.SEUIL_ORPHELIN_SECONDES + 60)

    pas_concerne = tmp_path / "autre_chose"
    pas_concerne.mkdir()

    supprimes = fichiers.purger_dossiers_orphelins_au_demarrage()

    assert supprimes == 1
    assert not orphelin.exists()
    assert pas_concerne.exists()  # jamais touché : hors préfixe


def test_purge_au_demarrage_epargne_un_dossier_recent_dune_autre_instance(tmp_path, monkeypatch):
    """Bug trouvé en écrivant ce test : un nettoyage inconditionnel
    ('présent au démarrage = forcément orphelin') supprimait le dossier
    ENCORE ACTIF d'une autre instance du serveur partageant le même
    répertoire temporaire système (reproduit en pratique par
    tests/mcp/test_server_http.py, qui lance un vrai process serveur en
    sous-processus pendant que ce process pytest a lui-même déjà son propre
    `_DOSSIER`). Un dossier plus récent que SEUIL_ORPHELIN_SECONDES ne doit
    donc JAMAIS être supprimé, même s'il correspond au préfixe."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    recent = tmp_path / f"{fichiers.PREFIXE_DOSSIER}autre_instance_active"
    recent.mkdir()
    (recent / "en-cours-de-telechargement.pptx").write_bytes(b"pas-orphelin")

    supprimes = fichiers.purger_dossiers_orphelins_au_demarrage()

    assert supprimes == 0
    assert recent.exists()
