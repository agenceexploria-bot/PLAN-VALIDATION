# -*- coding: utf-8 -*-
"""
core/extract.py — Étapes 1 et 2 du pipeline : inventaire + extraction du PDF
fabricant. Adapté de scripts/extract_pdf.py du skill `plan-validation-vertical`
(logique inchangée), pour être appelé depuis l'UI Streamlit au lieu de la CLI.

Règles absolues respectées :
  - Le dessin n'est jamais redessiné : rendu 300 dpi brut, sans retouche.
  - Aucune valeur numérique n'est retapée : les cotes sont extraites
    programmatiquement (pymupdf) et copiées telles quelles.
  - Cote fusionnée à un suffixe texte (ex. "9700(FFL)") : la rédaction efface
    UNIQUEMENT le suffixe (`suffix_bbox`), jamais le nombre — le nombre reste
    toujours visible dans l'image rédigée, quel que soit le lecteur utilisé
    ensuite pour l'ouvrir.
"""
import re
import threading
from collections import Counter
from pathlib import Path

import fitz  # pymupdf

from . import translate

# Une seule extraction à la fois dans le processus (palier Render : 512 Mo).
# Le pic mémoire vient du rendu pleine page (~100 Mo par page A3 à 300 dpi) :
# deux sessions qui extraient en même temps additionnent leurs pics (constaté
# en prod : pages loguées deux fois, pic 543 Mo, OOM). Streamlit laisse en plus
# finir le thread d'une session abandonnée (rechargement, second onglet) jusqu'à
# son prochain appel d'interface. Vit dans ce module (importé une seule fois
# par processus) et non dans app.py, ré-exécuté à chaque rerun.
VERROU_EXTRACTION = threading.Lock()

# Mot "à traduire" = uniquement des LETTRES, hors code invariant. Un token qui
# contient un chiffre (nombre, cote, désignation Ø120 / M12 / R25 / H7 / Ra3.2,
# code EX.26.396R1 / DHYA.2 / LD82040 / P12345 / IP54 / RAL7016), une unité
# (12mm, 1500kg) ou une marque de coupe (A-A) n'est JAMAIS ciblé : ni effacé,
# ni retapé — le nombre reste toujours visible dans l'image (règle absolue).
INVARIANT = re.compile(
    r"^(RAL|IP|REV|RIF|LD|FFL|kW|KW|kg|mm|cm|m/s|V|A|Hz|N|P)\.?\d*$", re.I
)

# Marque de coupe / de vue : « A-A », « B-B », « C-C »…
CODE_COUPE = re.compile(r"^[A-Za-zØø]-[A-Za-zØø]$")

# Cote composée écrite sans espace autour du "x" de multiplication, ex.
# "2000X1500" ou "2835X1800X150" (largeur x profondeur [x hauteur]) : c'est
# UN SEUL token pymupdf, pas un nombre + un libellé. Trouvé au rendu réel (test
# DHYA.2) : sans cette règle, `fused_number` prenait "X1500" pour un suffixe à
# traduire (chevauchement de texte).
DIMENSION_COMPOSEE = re.compile(r"^\d[\d.,]*(?:[xX]\d[\d.,]*)+$")

# Continuation isolée d'une telle cote (au cas où un fabricant la tokenise en
# plusieurs mots séparés) : même logique, jamais traduisible.
FRAGMENT_COTE = re.compile(r"^[xX]\d[\d.,]*(?:[xX]\d[\d.,]*)*$")

# Cote fusionnée avec un suffixe texte : ex. "9700(FFL)", "200(PIT)".
NUM_PREFIX = re.compile(r"^[\d][\d.,]*")

# Un « suffixe » qui n'est qu'une unité (12mm, 1500kg, 3.5kW, 2,5m/s) n'est pas
# un qualificatif à traduire : le token entier est intouchable.
UNITE_SUFFIXE = re.compile(
    r"^\(?(mm|cm|dm|m|km|kg|g|t|kn|n|kw|w|v|a|ah|hz|bar|mpa|db|l|m/s)\)?\.?$", re.I
)

# Nombres d'un texte (sert au contrôle de survie après rédaction).
NOMBRE = re.compile(r"\d+(?:[.,]\d+)?")


def is_translatable(word: str) -> bool:
    if not re.search(r"[A-Za-zÀ-ÿ]", word):
        return False  # nombre pur, cote -> on n'y touche jamais
    if re.search(r"\d", word):
        return False  # nombre, code ou désignation : jamais effacé ni retapé
    stripped = word.strip(".,;:()")
    if INVARIANT.match(stripped) or CODE_COUPE.match(stripped)             or FRAGMENT_COTE.match(stripped) or DIMENSION_COMPOSEE.match(stripped):
        return False
    return True


def fused_number(word: str):
    """Si `word` est une cote fusionnée à un suffixe TEXTE (ex. '9700(FFL)'),
    retourne le nombre en tête. Sinon None : cote composée (2000X1500),
    désignation (2xØ14), unité collée (12mm, 1500kg) ou nombre seul ne sont
    jamais découpés — leur « suffixe » n'est pas un libellé à traduire."""
    m = NUM_PREFIX.match(word)
    if not m:
        return None
    suffixe = word[m.end():]
    if not suffixe or not re.match(r"[A-Za-zÀ-ÿ(]", suffixe):
        return None
    if re.search(r"\d", suffixe) or UNITE_SUFFIXE.match(suffixe):
        return None
    return m.group(0)


def page_char_boxes(page):
    """Toutes les glyphes de la page avec leur bbox — permet de rédiger un
    SOUS-mot (le suffixe d'une cote) sans toucher au nombre."""
    boxes = []
    for b in page.get_text("rawdict")["blocks"]:
        for line in b.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    boxes.append((ch["c"], ch["bbox"]))
    return boxes


def page_span_directions(page):
    """Bbox + direction d'écriture (cos, sin) de chaque span de la page.
    Sert à déterminer si un mot est écrit horizontalement ou en rotation
    (cotes verticales) — jamais tranché à l'œil, toujours par la donnée
    vectorielle du PDF."""
    spans = []
    for b in page.get_text("rawdict")["blocks"]:
        for line in b.get("lines", []):
            # La direction d'écriture est portée par la LIGNE : les spans n'ont
            # pas de clé "dir" (la lire dessus rendait tout « horizontal »).
            direction = line.get("dir", (1.0, 0.0))
            for span in line.get("spans", []):
                spans.append((span["bbox"], direction))
    return spans


def word_rotation_deg(word_bbox, spans_dir) -> int:
    """Angle de rotation (0/90/270, sens horaire comme python-pptx) du mot
    `word_bbox`, déduit de la direction d'écriture du span qui le contient.
    0 = horizontal (cas par défaut si aucun span trouvé)."""
    import math

    wx0, wy0, wx1, wy1 = word_bbox
    cx, cy = (wx0 + wx1) / 2, (wy0 + wy1) / 2
    for (sx0, sy0, sx1, sy1), (dx, dy) in spans_dir:
        if sx0 - 1 <= cx <= sx1 + 1 and sy0 - 1 <= cy <= sy1 + 1:
            angle = math.degrees(math.atan2(dy, dx)) % 360
            if 45 <= angle < 135:
                return 90
            if 225 <= angle < 315:
                return 270
            return 0
    return 0


def suffix_bbox(char_boxes, word_bbox):
    """bbox des seuls caractères NON numériques (le suffixe texte) d'une cote
    fusionnée, à l'intérieur de la bbox du mot. Robuste à la rotation (cotes
    verticales) : sélection des glyphes hors [0-9.,], sans hypothèse d'ordre,
    donc le nombre n'est jamais inclus dans la zone rédigée."""
    wx0, wy0, wx1, wy1 = word_bbox
    xs0, ys0, xs1, ys1 = [], [], [], []
    for ch, (x0, y0, x1, y1) in char_boxes:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if wx0 - 0.5 <= cx <= wx1 + 0.5 and wy0 - 0.5 <= cy <= wy1 + 0.5:
            if ch not in "0123456789.,":
                xs0.append(x0); ys0.append(y0); xs1.append(x1); ys1.append(y1)
    if not xs0:
        return None
    return [round(min(xs0), 2), round(min(ys0), 2),
            round(max(xs1), 2), round(max(ys1), 2)]


def nb_pages(pdf_path: Path) -> int:
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def pages_sans_texte(pdf_path: Path) -> list:
    """Numéros de page (1-based) sans aucune couche texte exploitable — PDF
    scanné probable. Vérification légère (pas de rendu ni de rédaction),
    utilisable dès l'étape 1 pour avertir l'utilisateur avant tout
    traitement, plutôt que de laisser passer un échec silencieux (bug
    audit). Confirmé plus précisément par `est_pdf_scanne` à l'étape 3, une
    fois les pages retenues réellement extraites (mots traduisibles)."""
    doc = fitz.open(pdf_path)
    try:
        return [i + 1 for i in range(len(doc)) if not doc[i].get_text("words")]
    finally:
        doc.close()


def rendre_apercus(pdf_path: Path, dpi: int = 100):
    """Étape 1 (inventaire) : rend chaque page du PDF en aperçu basse résolution.
    Retourne une liste de bytes PNG, une par page, dans l'ordre du document.
    Utilisé par l'UI pour la grille de classement des pages."""
    doc = fitz.open(pdf_path)
    try:
        scale = dpi / 72
        return [
            doc[i].get_pixmap(matrix=fitz.Matrix(scale, scale)).tobytes("png")
            for i in range(len(doc))
        ]
    finally:
        doc.close()


def extraire_page(doc, pno: int, out: Path, dpi: int = 300, generer_image: bool = True,
                  rediger: bool = True) -> dict:
    """Extraction complète d'UNE page retenue (étape 2) :
      - page_N_redacted.png : SEUL rendu produit — rédigé (suffixes de cotes
        fusionnées et libellés texte purs retirés, nombres jamais touchés),
        en PNG direct (pas de palette) : la conversion en palette coûtait
        ~+69 Mo de pic mémoire par planche A3 à 300 dpi (+176 Mo contre +107
        Mo mesurés) pour un PPTX ~30 % plus léger — mesuré en prod Render :
        pic à 543 Mo, OOM sur le palier 512 Mo. La mémoire prime sur le poids.
        Le rendu brut séparé (page_N.png) a été retiré
        (bug audit RAM Render) : il n'était consommé nulle part en aval.
        `generer_image=False` (pages « specs » : leur image n'est jamais
        utilisée, seul le texte alimente le tableau reconstruit) saute
        entièrement la rédaction ET le rendu — aucune raison de payer ce
        coût mémoire/CPU pour une image qui ne sera jamais embarquée ;
      - page_N_words.json (retourné ici comme dict, écrit par l'appelant) ;
    Retourne le dict `words_data` correspondant à page_N_words.json.
    """
    page = doc[pno]
    n = pno + 1
    scale = dpi / 72
    out.mkdir(parents=True, exist_ok=True)

    char_boxes = page_char_boxes(page)
    spans_dir = page_span_directions(page)
    words = []
    for w in page.get_text("words"):
        bbox = [round(v, 2) for v in w[:4]]
        num = fused_number(w[4])
        words.append({
            "text": w[4],
            "bbox": bbox,
            "translatable": is_translatable(w[4]),
            "num": num,
            "suffix_en": w[4][len(num):] if num else None,
            "suffix_bbox": suffix_bbox(char_boxes, bbox) if num else None,
            # 0/90/270 (sens horaire, convention python-pptx) — déduit de la
            # direction d'écriture du PDF, jamais estimé à l'œil.
            "rotation_deg": word_rotation_deg(bbox, spans_dir),
            # Regroupement en ligne (fourni par pymupdf) : permet à
            # core/translate.py de reconstituer les EXPRESSIONS de plusieurs
            # mots (ex. "with operator on board") avant de retomber sur une
            # traduction mot à mot, qui rate tout terme composé du glossaire.
            "block_no": w[5],
            "line_no": w[6],
            "word_no": w[7],
        })

    words_data = {
        "page": n,
        "page_size_pts": [page.rect.width, page.rect.height],
        "render_dpi": dpi,
        "words": words,
    }

    if not generer_image:
        return words_data

    # Rédaction : UNIQUEMENT les zones où une étiquette FR sera posée (règle B1 :
    # un mot traduit = un effacement + une pose ; jamais de pose sans effacement,
    # jamais d'effacement sans pose). Les zones viennent de la MÊME fonction que
    # celle qui construit les étiquettes (`translate.traduire_labels_planche`),
    # donc les deux ne peuvent pas diverger. Un mot hors glossaire, ou traduit à
    # l'identique, n'est pas touché. Cote fusionnée (« 9700(FFL) ») : seule la
    # bbox du suffixe est effacée, jamais le nombre.
    # `rediger=False` (page de garde / vue 3D, où aucune étiquette n'est posée) :
    # rendu brut, rien d'effacé.
    if rediger:
        labels, _ = translate.traduire_labels_planche({"words": words})
        for lab in labels:
            page.add_redact_annot(fitz.Rect(lab["bbox"]))
        if labels:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        # Contrôle bloquant : chaque nombre de la page doit SURVIVRE à la
        # rédaction (comparaison au texte d'origine, multiensembles). Si un
        # nombre a disparu — une zone effacée mordait dessus —, la page est en
        # échec : l'app refuse d'assembler et le rapport de vérification l'affiche.
        avant = Counter(n for w in words for n in NOMBRE.findall(w["text"]))
        apres = Counter(n for w in page.get_text("words") for n in NOMBRE.findall(w[4]))
        words_data["survie_nombres"] = {
            "total": sum(avant.values()),
            "perdus": sorted((avant - apres).elements()),
        }
    # Libération explicite du pixmap dès l'écriture sur disque (bug audit RAM
    # Render) : ~50 Mo par pixmap RGB non compressé pour une page A3 à 300
    # dpi — jamais laissé référencé au-delà du .save() qui l'écrit.
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    pix.save(out / f"page_{n}_redacted.png")
    pix = None

    return words_data


def est_pdf_scanne(words_data: dict) -> bool:
    """True si la page n'a aucune couche texte exploitable (pas de mot
    traduisible détecté) : la rédaction vectorielle est alors impossible,
    il faut basculer en mode légende (traductions en repères numérotés)."""
    return not any(w["translatable"] for w in words_data["words"])


def extraire_pdf(pdf_path: Path, out: Path, pages_1based, dpi: int = 300, generer_image: bool = True,
                 rediger: bool = True) -> dict:
    """Extraction de plusieurs pages retenues. `pages_1based` = liste de
    numéros de page 1-based. Écrit page_N_redacted.png (sauf si
    `generer_image=False`, cf. `extraire_page`) / page_N_words.json dans
    `out`, et retourne {page: words_data}. Logge le pic mémoire après
    CHAQUE page individuelle (pas seulement en fin de boucle) : permet de
    voir directement, page par page — y compris dans les logs Render en
    prod —, si la mémoire s'accumule au fil du traitement ou reste stable
    (bug audit RAM Render, diagnostic memlog)."""
    import json

    from . import memlog

    with VERROU_EXTRACTION:
        doc = fitz.open(pdf_path)
        resultats = {}
        try:
            for n in pages_1based:
                words_data = extraire_page(doc, n - 1, out, dpi, generer_image, rediger)
                (out / f"page_{n}_words.json").write_text(
                    json.dumps(words_data, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
                resultats[n] = words_data
                memlog.logger_etape(f"extraction page {n}")
        finally:
            doc.close()
    return resultats
