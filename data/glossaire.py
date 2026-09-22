# -*- coding: utf-8 -*-
"""
Glossaire FR Vertical — transcrit de
.claude/skills/plan-validation-vertical/references/glossaire.md

Terminologie imposée pour la traduction EN/IT/TR -> FR des plans fabricant.
Ne jamais "améliorer" ou contourner ces traductions : elles sont contractuelles.

Statut des sections (cf. glossaire.md) :
  - CARTOUCHE_SPECS et SUFFIXES_COTE : validés par Marin Bosquier (15/06/2026),
    autorité supérieure à tout le reste.
  - Les tables EN/IT/TR (équipement, accès, machinerie, charges, finition) :
    la colonne FR Vertical est la traduction imposée ; les colonnes IT/TR sont
    des propositions non validées par un référent métier (utilisées quand même,
    faute de mieux, mais un terme trouvé uniquement via IT/TR devrait être
    signalé comme tel dans le rapport si l'app distingue les statuts).
  - MONTAGE_GENIE_CIVIL : FR "proposé, à valider par Marin" (statut identique
    aux colonnes IT/TR ci-dessus).
"""

import re

# ---------------------------------------------------------------------------
# Cartouche & specs — lexique validé Vertical (autorité supérieure)
# ---------------------------------------------------------------------------

CARTOUCHE_SPECS = {
    "MODEL": "Modèle",
    "PLATFORM SIZE": "Dimensions plateforme",
    "PIT SIZE": "Dimensions fosse",
    "RAMP SIZE": "Dimensions rampe",
    "SHAFT SIZE": "Dimensions gaine",
    "PIT": "Fosse",
    "RAMP": "Rampe",
    "STROKE": "Course",
    "STOPS": "Arrêts",
    "CAPACITY": "Capacité",
    "LIFT SPEED": "Vitesse",
    "TOP PLATFORM: ANTI SLIP TEAR METAL": "Plateforme en tôle larmée antidérapante",
    "POWER PACK": "Groupe hydraulique",
    "COLOUR": "Coloris",
    "HANDRAILS": "Garde-corps",
    "TABLE FRAME": "Structure plateforme",
    "FLOOR CONTROL STAINLESS STEEL PROFILE": "Boîtier de commande palier en inox",
    "ELECTRICAL CABIN": "Armoire électrique",
    "OFFER NO": "N° offre",
}

# Qualificatifs de cote (suffixe entre parenthèses collé à un nombre, ex.
# "2785(CONS.)" -> "2785 (structure)"). Le nombre est un invariant, jamais
# retapé : seul le suffixe passe par cette table.
SUFFIXES_COTE = {
    "CONS.": "structure",
    "PITSIZE": "dim. fosse",
    "PIT SIZE": "dim. fosse",
    "HANDRAILS": "garde-corps",
    "PIT": "fosse",
    "FFL": "FFL",  # sigle conservé, "niveau sol fini"
}

# ---------------------------------------------------------------------------
# Équipement et structure
# ---------------------------------------------------------------------------

EQUIPEMENT_STRUCTURE = {
    "goods lift": "monte-charge",
    "freight lift": "monte-charge",
    "montacarichi": "monte-charge",
    "yük asansörü": "monte-charge",
    "unaccompanied goods lift": "monte-charge non accompagné",
    "montacarichi senza operatore": "monte-charge non accompagné",
    "operatörsüz yük asansörü": "monte-charge non accompagné",
    "platform": "plate-forme",
    "piattaforma": "plate-forme",
    "platform (tr)": "plate-forme",
    "structure": "structure",
    "frame": "structure",
    "struttura": "structure",
    "konstrüksiyon": "structure",
    "şasi": "structure",
    "self-supporting structure": "structure autoportée",
    "struttura autoportante": "structure autoportée",
    "kendinden taşıyıcı yapı": "structure autoportée",
    "column": "colonne",
    "mast": "colonne",
    "colonna": "colonne",
    "montante": "colonne",
    "kolon": "colonne",
    "direk": "colonne",
    "double column guiding": "guidage double colonne",
    "guida a doppia colonna": "guidage double colonne",
    "çift kolon kılavuzlama": "guidage double colonne",
    "shaft": "gaine",
    "enclosure": "gaine",
    "vano": "gaine",
    "tamponamento": "gaine",
    "asansör kuyusu": "gaine",
    "pit": "fosse",
    "fossa": "fosse",
    "kuyu dibi": "fosse",
    "çukur": "fosse",
    "headroom": "hauteur libre sous dalle",
    "testata": "hauteur libre sous dalle",
    "extracorsa superiore": "hauteur libre sous dalle",
    "tepe boşluğu": "hauteur libre sous dalle",
    "travel": "course",
    "stroke": "course",
    "corsa": "course",
    "kurs": "course",
    "seyir mesafesi": "course",
    "floor": "niveau",
    "level": "niveau",
    "landing": "niveau",
    # « piano » (italien : étage) retiré : mot ambigu (plan, plateau…) hors contexte.
    "livello": "niveau",
    # « kat » (turc : étage) retiré : mot ambigu hors contexte, non traduit plutôt que faux.
    "seviye": "niveau",
    "durak": "niveau",
    "through access": "accès traversant",
    "pass-through": "accès traversant",
    "accesso passante": "accès traversant",
    "karşılıklı geçişli erişim": "accès traversant",
    "anchoring": "fixation",
    "fixing": "fixation",
    "ancoraggio": "fixation",
    "fissaggio": "fixation",
    "ankraj": "fixation",
    "sabitleme": "fixation",
}

# ---------------------------------------------------------------------------
# Accès et sécurité
# ---------------------------------------------------------------------------

ACCES_SECURITE = {
    "landing door": "porte palière",
    "porta di piano": "porte palière",
    "kat kapısı": "porte palière",
    "gate": "portillon",
    "cancelletto": "portillon",
    "platform kapısı": "portillon",
    "guardrail": "garde-corps",
    "handrail": "garde-corps",
    "parapetto": "garde-corps",
    "korkuluk": "garde-corps",
    "küpeşte": "garde-corps",
    "safety barrier": "barrière de sécurité",
    "barriera di sicurezza": "barrière de sécurité",
    "güvenlik bariyeri": "barrière de sécurité",
    "emergency stop": "arrêt d'urgence",
    "arresto di emergenza": "arrêt d'urgence",
    "acil durdurma": "arrêt d'urgence",
    "interlock": "verrouillage",
    "interblocco": "verrouillage",
    "kilitleme": "verrouillage",
    "anti-drift locks": "taquets antidérive",
    "safety pawls": "taquets antidérive",
    "arpioni": "taquets antidérive",
    "blocchi antideriva": "taquets antidérive",
    "güvenlik tırnakları": "taquets antidérive",
    "kaymaz kilitler": "taquets antidérive",
    "overload device": "dispositif de surcharge",
    "dispositivo di sovraccarico": "dispositif de surcharge",
    "aşırı yük cihazı": "dispositif de surcharge",
}

# ---------------------------------------------------------------------------
# Machinerie et électricité
# ---------------------------------------------------------------------------

MACHINERIE_ELECTRICITE = {
    "hydraulic power unit": "groupe hydraulique",
    "power pack": "groupe hydraulique",
    "centralina idraulica": "groupe hydraulique",
    "hidrolik güç ünitesi": "groupe hydraulique",
    "santral": "groupe hydraulique",
    "remote power unit": "unité hydraulique déportée",
    "centralina remota": "unité hydraulique déportée",
    "ayrık güç ünitesi": "unité hydraulique déportée",
    "control cabinet": "armoire de commande",
    "control panel": "armoire de commande",
    "quadro elettrico": "armoire de commande",
    "quadro di comando": "armoire de commande",
    "kumanda panosu": "armoire de commande",
    "single-acting cylinder": "vérin simple effet",
    "cilindro a semplice effetto": "vérin simple effet",
    "tek etkili silindir": "vérin simple effet",
    "hose": "flexible",
    "tubo flessibile": "flexible",
    "hortum": "flexible",
    "power supply": "alimentation",
    "alimentazione": "alimentation",
    "besleme": "alimentation",
    "güç beslemesi": "alimentation",
    "control voltage": "tension de commande",
    "tensione di comando": "tension de commande",
    "kumanda gerilimi": "tension de commande",
    "three-phase": "3 phases (triphasé)",
    "trifase": "3 phases (triphasé)",
    "üç fazlı": "3 phases (triphasé)",
    "trifaze": "3 phases (triphasé)",
    "neutral": "neutre",
    "earth": "terre",
    "ground": "terre",
    "neutro": "neutre",
    "terra": "terre",
    "nötr": "neutre",
    "toprak": "terre",
    "circuit breaker": "disjoncteur",
    "interruttore magnetotermico": "disjoncteur",
    "otomatik sigorta": "disjoncteur",
    "şalter": "disjoncteur",
    "residual current device": "différentiel",
    "rcd": "différentiel",
    "differenziale": "différentiel",
    "kaçak akım rölesi": "différentiel",
    "call and send buttons": "boutons d'appel et envoi",
    "pulsanti di chiamata e invio": "boutons d'appel et envoi",
    "çağırma ve gönderme butonları": "boutons d'appel et envoi",
    "position indicator light": "indicateur lumineux de position",
    "indicatore luminoso di posizione": "indicateur lumineux de position",
    "konum gösterge lambası": "indicateur lumineux de position",
    "duty cycles": "cycles de fonctionnement",
    "cicli di funzionamento": "cycles de fonctionnement",
    "çalışma çevrimleri": "cycles de fonctionnement",
    "speed": "vitesse",
    "velocità": "vitesse",
    "hız": "vitesse",
    "power": "puissance",
    "potenza": "puissance",
    "güç": "puissance",
}

# ---------------------------------------------------------------------------
# Charges et dimensions
# ---------------------------------------------------------------------------

CHARGES_DIMENSIONS = {
    "load capacity": "capacité de charge",
    "rated load": "capacité de charge",
    "portata": "capacité de charge",
    "taşıma kapasitesi": "capacité de charge",
    "nominal yük": "capacité de charge",
    "platform dimensions": "dimensions plateforme",
    "dimensioni piattaforma": "dimensions plateforme",
    "platform ölçüleri": "dimensions plateforme",
    "overall dimensions": "encombrement",
    "ingombri": "encombrement",
    "genel ölçüler": "encombrement",
    "clear width": "largeur utile",
    "clear height": "hauteur utile",
    "larghezza utile": "largeur utile",
    "altezza utile": "hauteur utile",
    "net genişlik": "largeur utile",
    "yükseklik": "hauteur utile",
    "finished floor level": "niveau sol fini",
    "ffl": "niveau sol fini",
    "piano pavimento finito": "niveau sol fini",
    "bitmiş döşeme kotu": "niveau sol fini",
    "floor slab": "dalle",
    "soletta": "dalle",
    "döşeme": "dalle",
    "beton plak": "dalle",
    "recess": "réservation",
    "reservation": "réservation",
    "nicchia": "réservation",
    "predisposizione": "réservation",
    "niş": "réservation",
    "boşluk": "réservation",
}

# ---------------------------------------------------------------------------
# Finition
# ---------------------------------------------------------------------------

FINITION = {
    "anti-slip sheet": "tôle larmée antidérapante",
    "checker plate": "tôle larmée antidérapante",
    "tear metal": "tôle larmée antidérapante",
    "lamiera antiscivolo": "tôle larmée antidérapante",
    "mandorlata": "tôle larmée antidérapante",
    "kaymaz sac": "tôle larmée antidérapante",
    "baklavalı sac": "tôle larmée antidérapante",
    "paint": "peinture",
    "coating": "peinture",
    "verniciatura": "peinture",
    "boya": "peinture",
    "kaplama": "peinture",
    "anthracite grey": "gris anthracite",
    "grigio antracite": "gris anthracite",
    "antrasit gri": "gris anthracite",
    "galvanized": "galvanisé",
    "zincato": "galvanisé",
    "galvanizli": "galvanisé",
}

# ---------------------------------------------------------------------------
# Plan technique : cotation, détails, cartouche (TR -> FR)
# ---------------------------------------------------------------------------

PLAN_TECHNIQUE_TR = {
    "detay": "détail",
    "ölçek": "échelle",
    "serbest ölçü toleransları": "tolérances générales (cotes libres)",
    "anma boyutları": "cotes nominales",
    "tolerans": "tolérance",
    "sınıf": "classe",
    "kalite": "qualité",
    "ölçü": "cote",  # alternative écartée : « mesure »
    "yüzey": "surface",
    "yüzey pürüzlülüğü": "rugosité de surface",
    "görünüş": "vue",
    "ön görünüş": "vue de face",
    "üst görünüş": "vue de dessus",
    "yan görünüş": "vue de côté",
    "kesit": "coupe",  # alternative écartée : « section »
    "plan": "plan",
    "malzeme": "matériau",
    "ağırlık": "poids",  # alternative écartée : « masse »
    "adet": "quantité",
    "parça": "pièce",
    "parça listesi": "nomenclature (liste de pièces)",
    "çizen": "dessiné par (dessinateur)",
    "kontrol": "contrôlé par",  # alternative écartée : « vérifié par »
    "onay": "approbation",  # alternative écartée : « visa »
    "tarih": "date",
    "revizyon": "révision",
    # « not » (turc : note) retiré : c'est aussi l'anglais « NOT » (DO NOT SCALE) et
    # il serait traduit « note » sans que la langue soit détectée. Le pluriel « notlar »
    # (non ambigu) reste traduit.
    "notlar": "notes",
    "montaj": "montage",
    # Cf. SKILL.md « Petites cellules (bloc tolérances ISO 2768 en marge) » —
    # bloc rugosité, toujours à côté de SERBEST ÖLÇÜ TOLERANSLARI.
    "ra (max)": "Ra (max)",
}

# ---------------------------------------------------------------------------
# Montage/installation & génie civil — proposé, à valider par Marin
# ---------------------------------------------------------------------------

MONTAGE_GENIE_CIVIL = {
    "corrugated tube": "fourreau annelé (TPC)",
    "technical area": "local technique",
    "threshold": "seuil",
    "adjustable support": "support réglable",
    "magnetothermic switch": "disjoncteur magnétothermique",
    "differential (curve c)": "différentiel (courbe C)",
    "impact wrench": "clé à choc",
    "hook": "crochet (de levage)",
    "flatbed": "plateau",
    "loads on wall": "charges sur le mur (efforts sur paroi)",
    "with operator on board": "avec opérateur à bord",
    # Valeur de « POWER PACK » sur les plans fabricant (groupe hydraulique hors
    # gaine). Traduction donnée par l'utilisateur (LOT C), à valider par Marin.
    "outside": "déporté (extérieur)",
    "finished level": "niveau fini",
    "finished floor": "niveau fini",
    "anchor": "ancre",  # alternative écartée : « cheville »
    "surrounding protection": "protection périphérique",
    "main lift components": "principaux composants du monte-charge",
    "to be handled": "à manutentionner",
    "firma per approvazione": "visa pour approbation",
    # « data » (italien : date) retiré : c'est aussi l'anglais « DATA », traduit à tort « date ».
    "disegno": "dessin",  # alternative écartée : « plan »
    "drawing": "dessin",  # alternative écartée : « plan »
    "designer": "dessinateur",
}

# ---------------------------------------------------------------------------
# Alternatives tranchées (LOT C2) — décisions de TERMINOLOGIE MÉTIER à faire
# valider par Marin. Une traduction publiée sur un document ne doit jamais
# contenir « a/b » : on retient le premier terme, l'autre est consigné ici ET
# dans le README (section « Glossaire : choix à valider par Marin »).
# ---------------------------------------------------------------------------

ALTERNATIVES_ECARTEES = {
    "ölçü":     {"retenu": "cote",          "ecarte": "mesure"},
    "kesit":    {"retenu": "coupe",         "ecarte": "section"},
    "ağırlık":  {"retenu": "poids",         "ecarte": "masse"},
    "kontrol":  {"retenu": "contrôlé par",  "ecarte": "vérifié par"},
    "onay":     {"retenu": "approbation",   "ecarte": "visa"},
    "anchor":   {"retenu": "ancre",         "ecarte": "cheville"},
    "disegno":  {"retenu": "dessin",        "ecarte": "plan"},
    "drawing":  {"retenu": "dessin",        "ecarte": "plan"},
}

# ---------------------------------------------------------------------------
# Table fusionnée pour la recherche (toutes sections, hors cartouche/specs
# qui reste consultée séparément par translate.py car prioritaire)
# ---------------------------------------------------------------------------

_SECTIONS_GENERALES = [
    EQUIPEMENT_STRUCTURE,
    ACCES_SECURITE,
    MACHINERIE_ELECTRICITE,
    CHARGES_DIMENSIONS,
    FINITION,
    PLAN_TECHNIQUE_TR,
    MONTAGE_GENIE_CIVIL,
]

GLOSSAIRE = {}
for _section in _SECTIONS_GENERALES:
    GLOSSAIRE.update(_section)

# ---------------------------------------------------------------------------
# Invariants — ne jamais traduire ni reformater (détection, pas traduction)
# ---------------------------------------------------------------------------

# Valeurs numériques et unités, codes normatifs, n° d'affaire, références
# fabricant : reconnus pour ne JAMAIS être passés à la traduction.
RE_INVARIANT = re.compile(
    r"""^(
        \d[\d.,]*\s*(x\s*\d[\d.,]*)?\s*(mm|cm|m|kg|kw|v|a|hz|m/s)?  # cotes, poids, unités
        |RAL\s*\d+
        |IP\s*\d+
        |\d+\s*V
        |LD\d+
        |RIF\.?\s*P?\d+
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


def est_invariant(texte: str) -> bool:
    """True si le texte est une valeur numérique/code qui ne doit jamais être
    traduit ni reformaté (règle absolue n°2 du cahier des charges)."""
    return bool(RE_INVARIANT.match(texte.strip()))


# Repli ASCII pour le turc : les exports CAO déposent très souvent les
# caractères turcs sans leurs diacritiques ou avec leur variante ASCII la
# plus proche (ex. "SINIF" pour "sınıf", le ı sans point devenant un I
# ordinaire). On indexe donc CHAQUE table aussi sous sa forme repliée, et on
# cherche d'abord la forme exacte (fidèle au glossaire), puis la forme
# repliée en secours — jamais l'inverse, pour ne pas dégrader un match exact.
_PLI_TR = str.maketrans({
    "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o",
    "ü": "u", "Ü": "u",
    "ş": "s", "Ş": "s",
    "ç": "c", "Ç": "c",
    "ğ": "g", "Ğ": "g",
})


def _plier(s: str) -> str:
    return s.translate(_PLI_TR)


def _index_avec_repli(d: dict) -> dict:
    """Index {clé: valeur} complété par {clé repliée ASCII: valeur} pour les
    clés dont le repli diffère (n'écrase jamais une clé exacte existante)."""
    index = dict(d)
    for cle, valeur in d.items():
        pliee = _plier(cle)
        if pliee != cle and pliee not in index:
            index[pliee] = valeur
    return index


def traduire_cartouche_specs(terme: str):
    """Cherche `terme` dans le lexique cartouche/specs validé (autorité
    supérieure). Retourne la traduction ou None si absent."""
    index = _index_avec_repli({k.upper(): v for k, v in CARTOUCHE_SPECS.items()})
    brut = terme.strip()
    return index.get(brut.upper()) or index.get(_plier(brut).upper())


def traduire_suffixe_cote(suffixe: str):
    """Traduit le suffixe d'une cote fusionnée, ex. '(CONS.)' -> '(structure)'.
    Le nombre associé n'est jamais concerné : voir core/extract.py (champ num)."""
    index = _index_avec_repli({k.upper(): v for k, v in SUFFIXES_COTE.items()})
    brut = suffixe.strip("() ")
    valeur = index.get(brut.upper()) or index.get(_plier(brut).upper())
    if valeur is None:
        return None
    return f"({valeur})" if suffixe.strip().startswith("(") else valeur


_GLOSSAIRE_INDEX = _index_avec_repli(GLOSSAIRE)


def traduire_terme(terme: str):
    """Cherche `terme` dans le glossaire général (toutes langues confondues).
    Retourne la traduction FR ou None si le terme est hors glossaire.
    Le repli ASCII (`_plier`) est appliqué AVANT la mise en minuscule : le
    caractère turc İ (I majuscule pointé) se change sinon, via `.lower()`
    standard (non turc-aware), en "i" + accent combinant au lieu d'un simple
    "i", ce qui casserait le rapprochement avec la clé "i" du glossaire."""
    brut = terme.strip()
    return _GLOSSAIRE_INDEX.get(brut.lower()) or _GLOSSAIRE_INDEX.get(_plier(brut).lower())
