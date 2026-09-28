# -*- coding: utf-8 -*-
"""
mcp_server/util.py — utilitaires communs aux outils MCP : décodage/encodage
base64, limite de taille des PDF (même règle que l'app Streamlit,
LIMITE_PDF_MO), résolution du PDF d'entrée (base64 direct OU pdf_id mis en
cache, cf. `resoudre_pdf`), résolution des données de mots extraits (JSON
direct OU extraction_id mis en cache, cf. `resoudre_words_data`), et dossier
de travail par appel. `core/etat.py` n'est pas réutilisable ici : il est
conçu autour de `st.session_state` (un dossier par SESSION Streamlit) alors
qu'un outil MCP crée son propre dossier jetable et le détruit avant de
renvoyer sa réponse — jamais partagé avec l'appel suivant. Le PDF fabricant
et les données de mots extraits font exception à ce principe (cf.
mcp_server/pdf_cache.py, mcp_server/extraction_cache.py) : les fonctions
`resoudre_*` de ce module sont le point d'entrée commun qui l'assume.
"""
import base64
import binascii
import io
import re
import shutil
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

from lxml import etree
from PIL import Image

from mcp.server.mcpserver.exceptions import ToolError

from core import cartouche_libreoffice, memlog

from . import cache_disque, extraction_cache, pdf_cache

_MOTIF_SLIDE = re.compile(r"ppt/slides/slide\d+\.xml")

# Même limite que app.py::LIMITE_PDF_MO (app Streamlit) — un PDF fabricant
# plus gros risque de saturer la mémoire du conteneur avant même l'extraction.
LIMITE_PDF_MO = 50


def verifier_taille_pdf(contenu: bytes) -> None:
    """Lève ValueError si `contenu` dépasse LIMITE_PDF_MO. Factorisé pour
    être appliqué aussi bien au PDF décodé depuis base64 (`decoder_pdf`) qu'à
    un PDF reçu par upload HTTP direct (octets déjà en clair, cf.
    mcp_server/server.py::televerser_pdf)."""
    taille_mo = len(contenu) / 1e6
    if taille_mo > LIMITE_PDF_MO:
        raise ValueError(
            f"PDF trop volumineux ({taille_mo:.0f} Mo, limite {LIMITE_PDF_MO} Mo) — "
            "compressez-le ou scindez-le avant de le fournir."
        )


def decoder_pdf(pdf_base64: str) -> bytes:
    """Décode un PDF reçu en base64 et vérifie sa taille AVANT tout
    traitement. Lève ValueError (jamais un plantage silencieux) si le PDF
    dépasse LIMITE_PDF_MO ou si le base64 est invalide."""
    try:
        contenu = base64.b64decode(pdf_base64, validate=True)
    except Exception as e:
        raise ValueError(f"pdf_base64 invalide (décodage base64 impossible) : {e}") from e
    verifier_taille_pdf(contenu)
    return contenu


def resoudre_pdf(pdf_base64: str | None, pdf_id: str | None) -> tuple[bytes, str]:
    """Résout le PDF d'entrée d'un outil qui accepte soit `pdf_id` (chemin
    NORMAL pour un agent Dust réel : PDF déjà en cache, obtenu via un POST
    multipart sur `{pdf_cache.PREFIXE_ROUTE}` ou renvoyé par un appel
    précédent d'`inventaire_pdf`), soit `pdf_base64` en repli (petits
    fichiers, tests directs — cf. mcp_server/pdf_cache.py : un agent Dust
    réel n'a souvent AUCUN moyen de produire ce base64 lui-même, constaté en
    conditions réelles). Exactement un des deux doit être fourni.

    Retourne (contenu, pdf_id) : `pdf_id` est TOUJOURS renvoyable tel quel à
    l'appelant — celui fourni (durée de vie prolongée) si `pdf_id` était
    donné, sinon un nouveau fraîchement mis en cache à partir du base64."""
    if bool(pdf_base64) == bool(pdf_id):
        raise ValueError("fournir exactement un de pdf_base64 ou pdf_id (pas les deux, pas aucun).")
    if pdf_id:
        contenu = pdf_cache.recuperer(pdf_id)
        if contenu is None:
            raise ValueError(f"pdf_id {pdf_id!r} inconnu ou expiré — retéléversez le PDF.")
        return contenu, pdf_id
    contenu = decoder_pdf(pdf_base64)
    return contenu, pdf_cache.mettre_en_cache(contenu)["pdf_id"]


_CHAMPS_MOT_REQUIS = (
    "block_no", "line_no", "word_no", "num", "translatable",
    "text", "bbox", "rotation_deg", "suffix_bbox", "suffix_en",
)


def valider_words_data(words_data: dict) -> None:
    """Valide la structure minimale de `words_data` (sortie attendue
    d'`extraire_page`, ou de la réponse complète mise en cache sous
    `extraction_id`) AVANT tout traitement. Remplace un KeyError/TypeError
    opaque en cas de structure malformée — ex. reconstruite à la main par un
    agent qui a buté sur la taille du JSON complet (cf.
    mcp_server/extraction_cache.py) — par une erreur explicite nommant le
    champ en cause. Lève ValueError, jamais un échec silencieux."""
    if not isinstance(words_data, dict):
        raise ValueError(f"words_data doit être un objet JSON, reçu : {type(words_data).__name__}.")
    if "page_size_pts" not in words_data:
        raise ValueError("words_data invalide : champ 'page_size_pts' manquant.")
    taille = words_data["page_size_pts"]
    if not (isinstance(taille, (list, tuple)) and len(taille) == 2):
        raise ValueError(f"words_data invalide : 'page_size_pts' doit être [largeur, hauteur], reçu : {taille!r}.")
    if "words" not in words_data:
        raise ValueError("words_data invalide : champ 'words' manquant.")
    mots = words_data["words"]
    if not isinstance(mots, list):
        raise ValueError(f"words_data invalide : 'words' doit être une liste, reçu : {type(mots).__name__}.")
    for i, mot in enumerate(mots):
        if not isinstance(mot, dict):
            raise ValueError(f"words_data invalide : words[{i}] doit être un objet, reçu : {type(mot).__name__}.")
        manquants = [c for c in _CHAMPS_MOT_REQUIS if c not in mot]
        if manquants:
            raise ValueError(
                f"words_data invalide : words[{i}] (text={mot.get('text')!r}) — "
                f"champ(s) manquant(s) : {manquants}."
            )


def resoudre_words_data(words_data: dict | None, extraction_id: str | None) -> dict:
    """Résout les données de mots d'une page pour un outil qui accepte soit
    `extraction_id` (chemin NORMAL pour un agent Dust réel : données déjà en
    cache, retournées par un appel précédent d'`extraire_page` — cf.
    mcp_server/extraction_cache.py), soit `words_data` en repli (petits
    fichiers, tests directs). Exactement un des deux doit être fourni.
    Valide la structure résolue avant de la retourner (`valider_words_data`)."""
    if bool(words_data) == bool(extraction_id):
        raise ValueError("fournir exactement un de words_data ou extraction_id (pas les deux, pas aucun).")
    if extraction_id:
        resolu = extraction_cache.recuperer(extraction_id)
        if resolu is None:
            raise ValueError(f"extraction_id {extraction_id!r} inconnu ou expiré — relancez extraire_page.")
    else:
        resolu = words_data
    valider_words_data(resolu)
    return resolu


def resoudre_entree_words_par_page(valeur) -> dict:
    """Résout UNE entrée de `words_par_page` (`verifier_rendu`) : soit un
    `extraction_id` (str, chemin NORMAL — cf. `resoudre_words_data`), soit
    directement `words_data`/la réponse complète d'`extraire_page` en repli.
    Même famille de problème que `resoudre_pdf`/`resoudre_words_data` : évite
    à l'agent de devoir AGRÉGER et retransmettre le JSON complet de chaque
    page retenue (pire cas — plusieurs pages à la fois) pour activer le
    contrôle de cotes."""
    if isinstance(valeur, str):
        resolu = extraction_cache.recuperer(valeur)
        if resolu is None:
            raise ValueError(f"extraction_id {valeur!r} inconnu ou expiré dans words_par_page — relancez extraire_page.")
    elif isinstance(valeur, dict):
        resolu = valeur
    else:
        raise ValueError(
            f"words_par_page : chaque valeur doit être un extraction_id (str) ou un objet, "
            f"reçu : {type(valeur).__name__}."
        )
    valider_words_data(resolu)
    return resolu


def decoder_base64(valeur, champ: str) -> bytes:
    """Décode `valeur` (base64) en nommant `champ` dans l'erreur si ce
    n'est pas du base64 valide — jamais un binascii.Error opaque."""
    if not isinstance(valeur, str) or not valeur:
        raise ValueError(f"{champ} doit être une chaîne base64 non vide.")
    try:
        return base64.b64decode(valeur, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{champ} : base64 invalide.") from None


def resoudre_pptx(pptx_base64: str | None, pptx_id: str | None) -> bytes:
    """Résout le PPTX d'entrée de `verifier_rendu` / `exporter_pdf` : soit
    `pptx_id` (chemin NORMAL, retourné par `assembler_pptx`, PPTX gardé sur
    le serveur — cf. mcp_server/cache_disque.py), soit `pptx_base64` en
    repli (petits fichiers, tests directs). Exactement un des deux."""
    if bool(pptx_base64) == bool(pptx_id):
        raise ValueError("fournir exactement un de pptx_id ou pptx_base64 (pas les deux, pas aucun).")
    if pptx_id:
        contenu = cache_disque.PPTX.recuperer(pptx_id)
        if contenu is None:
            raise ValueError(f"pptx_id {pptx_id!r} inconnu ou expiré — relancez assembler_pptx.")
        return contenu
    return decoder_base64(pptx_base64, "pptx_base64")


# Un seul travail lourd à la fois dans TOUT le process (palier Render :
# 512 Mo) — extraction, inventaire, rendu de vérification et export PDF
# confondus, pas seulement les extractions entre elles. Mesuré sur le
# scénario réel (2 pages A3 300 dpi) : une extraction coûte +90 à +166 Mo de
# pic, un rendu LibreOffice ~410 Mo de sous-process `soffice`. Deux travaux
# lourds simultanés dépassent donc le palier (une extraction pendant un rendu
# ≈ 700 Mo : OOM garanti), alors qu'aucun n'est en cause isolément.
#
# `core/extract.py::VERROU_EXTRACTION` ne protège PAS ce chemin : il vit dans
# `extraire_pdf`, que le serveur MCP n'appelle jamais (les outils appellent
# `extract.extraire_page` directement). Le verrou ajouté en 5046753 contre ce
# même OOM était donc contourné ici depuis l'origine.
#
# Verrou NON réentrant assumé : aucun outil MCP n'en appelle un autre (ils
# sont enregistrés à plat dans server.py et communiquent uniquement par
# identifiants de cache), donc aucune imbrication possible. Si une
# imbrication était introduite un jour, l'attente bornée ci-dessous la
# ferait échouer avec un message explicite au lieu de bloquer le process
# indéfiniment.
VERROU_TRAVAIL_LOURD = threading.Lock()
# Bornée, jamais infinie : au-delà, l'appel échoue avec un message lisible
# par l'agent plutôt que de laisser la requête pendue. Calée sur le délai
# maximal d'UN travail lourd (le `timeout=120` de la conversion LibreOffice,
# cf. core/render.py::_convertir_pdf_libreoffice) : de quoi laisser finir le
# travail en cours, sans accumuler une file d'attente invisible.
ATTENTE_TRAVAIL_LOURD_S = 120


@contextmanager
def travail_lourd(outil: str):
    """Sérialise les étapes coûteuses en mémoire (cf. VERROU_TRAVAIL_LOURD).
    Lève ToolError — jamais une attente indéfinie — si le serveur est resté
    occupé plus de ATTENTE_TRAVAIL_LOURD_S : l'agent doit pouvoir expliquer
    l'attente à l'utilisateur et réessayer, pas voir son appel se figer."""
    if not VERROU_TRAVAIL_LOURD.acquire(timeout=ATTENTE_TRAVAIL_LOURD_S):
        raise ToolError(
            f"{outil} : serveur occupé par un autre traitement lourd depuis plus de "
            f"{ATTENTE_TRAVAIL_LOURD_S} s. Ce n'est pas une erreur dans vos arguments — "
            "le serveur ne traite qu'un plan à la fois pour tenir dans sa mémoire. "
            "Prévenez l'utilisateur et relancez le même appel dans quelques instants."
        )
    try:
        yield
    finally:
        memlog.logger_memoire_conteneur(outil)
        VERROU_TRAVAIL_LOURD.release()


@contextmanager
def echec_rendu_explicite(outil: str):
    """Entoure l'appel au moteur de rendu (core/render.py) de
    `verifier_rendu` / `exporter_pdf`. Un échec du moteur (LibreOffice seul
    sous Docker/Render, PowerPoint/COM en local) lève RuntimeError, OSError,
    com_error... : pour le SDK mcp ce sont des « plantages », dont le texte
    n'est JAMAIS transmis à l'agent (« Error executing tool <nom> »). Converti
    ici en ToolError, avec le détail du moteur, pour que l'agent explique le
    problème à l'utilisateur plutôt que de réessayer à l'aveugle. ValueError
    (ex. moteur inconnu) est laissée telle quelle : déjà relayée par
    server.py::_erreurs_explicites."""
    try:
        yield
    except ValueError:
        raise
    except Exception as e:
        raise ToolError(
            f"{outil} : échec du moteur de rendu côté serveur — ce n'est pas une "
            "erreur dans vos arguments, inutile de réessayer tel quel ; "
            f"signalez-le à l'utilisateur. Détail : {e}"
        ) from e


def encoder_fichier(chemin: Path) -> str:
    """Encode le contenu d'un fichier en base64 (ASCII)."""
    return base64.b64encode(Path(chemin).read_bytes()).decode("ascii")


def _reduire_image(donnees: bytes, largeur_max: int) -> bytes:
    """Ramène une image embarquée à `largeur_max` de large si elle dépasse.
    Retourne les octets D'ORIGINE si l'image est déjà assez petite, si son
    format n'est pas réductible sans risque (PowerPoint embarque aussi du
    .wdp, .emf, .wmf) ou si Pillow ne sait pas la relire. Le format d'origine
    (PNG/JPEG) est conservé — le type de la partie est déduit de son
    extension dans [Content_Types].xml, qui n'est pas réécrit ici."""
    try:
        with Image.open(io.BytesIO(donnees)) as img:
            fmt = img.format
            if fmt not in ("PNG", "JPEG") or img.width <= largeur_max:
                return donnees
            hauteur = round(img.height * largeur_max / img.width)
            if img.mode in ("P", "CMYK"):
                img = img.convert("RGBA" if fmt == "PNG" else "RGB")
            reduite = img.resize((largeur_max, hauteur), Image.LANCZOS)
        try:
            sortie = io.BytesIO()
            reduite.save(sortie, format=fmt)
        finally:
            reduite.close()
        return sortie.getvalue()
    except (OSError, ValueError):
        return donnees


def copie_allegee(source: Path, destination: Path, largeur_max: int) -> Path:
    """Copie JETABLE de `source`, destinée au seul moteur de rendu, dont les
    images embarquées sont ramenées à `largeur_max` de large. Tout le reste
    du PPTX est recopié tel quel : structure, texte et cartouche intacts.

    Ni le PPTX de référence (celui d'`assembler_pptx`, publié au
    téléchargement et gardé sous `pptx_id`) ni aucun fichier remis à
    l'utilisateur n'est touché — seule cette copie temporaire, détruite avec
    le workdir de l'appel, porte les images réduites. Les contrôles
    programmatiques continuent de porter sur l'original.

    Pourquoi c'est sans effet sur le livrable réel : mesuré sur le scénario
    de référence, LibreOffice rééchantillonne DÉJÀ lui-même les images à
    l'export PDF (planche entrée à 4961 px, ressortie à 2584 px en JPEG dans
    le PDF final). Les pixels au-delà sont payés en RAM dans `soffice` — le
    poste qui faisait dépasser le palier Render à 512 Mo — puis écartés par
    LibreOffice au moment d'écrire le PDF.

    Réduit UNE image à la fois : deux planches A3 décompressées simultanément
    en RAM coûteraient plus cher que ce qu'on cherche à économiser.

    Adapte aussi le cartouche au moteur LibreOffice (cf.
    core/cartouche_libreoffice.py) : sans ça, LibreOffice impute la hauteur
    des cellules fusionnées sur plusieurs lignes à la seule dernière ligne,
    le cartouche déborde de 48 à 64 pt sous la page et la mention légale
    disparaît du PDF. Même règle que pour les images : seule cette copie est
    touchée, jamais le PPTX livré.

    Retourne le chemin À RENDRE : `destination` si l'allègement a abouti,
    `source` sinon. Un PPTX illisible comme archive (fichier tronqué, contenu
    qui n'est pas un OOXML) doit produire l'erreur explicite du moteur de
    rendu, pas un `BadZipFile` opaque venu d'une simple optimisation
    mémoire — cette économie n'est jamais une raison de faire échouer un
    rendu qui aurait marché."""
    try:
        with zipfile.ZipFile(source) as src, \
                zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as dst:
            hauteur_slide = cartouche_libreoffice.hauteur_slide_emu(src.read("ppt/presentation.xml"))
            for item in src.infolist():
                donnees = src.read(item.filename)
                if item.filename.startswith("ppt/media/"):
                    donnees = _reduire_image(donnees, largeur_max)
                elif _MOTIF_SLIDE.fullmatch(item.filename):
                    donnees = cartouche_libreoffice.ajuster_slide_xml(donnees, hauteur_slide)
                dst.writestr(item, donnees)
    except (OSError, KeyError, zipfile.BadZipFile, etree.XMLSyntaxError):
        return source
    return destination


@contextmanager
def workdir_temporaire():
    """Dossier de travail jetable pour la durée d'UN appel d'outil — jamais
    réutilisé d'un appel à l'autre (contrairement à l'app Streamlit, qui garde
    un workdir par session utilisateur) : chaque appel MCP est indépendant,
    sans état conservé côté serveur au-delà de sa propre exécution."""
    wd = Path(tempfile.mkdtemp(prefix="mcp_plan_validation_"))
    try:
        yield wd
    finally:
        shutil.rmtree(wd, ignore_errors=True)
