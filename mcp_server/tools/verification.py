# -*- coding: utf-8 -*-
"""mcp_server/tools/verification.py — outil MCP `verifier_rendu` (étape 5,
portail obligatoire avant tout export PDF)."""
import hashlib
from typing import Any

from core import render, verify

from .. import fichiers, util

# Largeur maximale des images embarquées dans la COPIE du PPTX envoyée au
# moteur de rendu pour le contrôle visuel — jamais dans le PPTX assemblé ni
# dans le PDF d'`exporter_pdf`, qui restent à pleine résolution (le livrable
# contractuel n'est jamais dégradé pour économiser de la mémoire sur un
# aperçu).
#
# Les slides font 11,69 pouces de large (A4 paysage) : 1754 px ≈ 150 dpi
# effectifs, soit déjà PLUS FIN que le rendu de contrôle lui-même (PDF rendu
# à Matrix(2.0), ~1684 px pour une slide pleine, cf.
# core/render.py::_rendre_pngs_libreoffice). Au-delà, ce sont des pixels que
# l'utilisateur ne verra jamais sur ces images de validation, payés ~177 Mo
# de RAM dans `soffice` — mesuré sur le scénario réel : 411 Mo de
# sous-process avec les planches A3 300 dpi (4961 px) embarquées, 234 Mo sans.
LARGEUR_MAX_APERCU_PX = 1754

# Nom du PPTX republié par `verifier_rendu` / `exporter_pdf` : ils ne
# reçoivent que son identifiant, pas le nom donné par `assembler_pptx`.
NOM_PPTX = "plan.pptx"


def verifier_rendu(
    pptx_base64: str = None, words_par_page: dict = None, meta: dict = None, pptx_id: str = None,
) -> dict[str, Any]:
    """Convertit le PPTX en image par slide (LibreOffice headless — jamais
    PowerPoint/COM, pour rester portable sur ce serveur) : les images
    DOIVENT être montrées à l'utilisateur AVANT toute validation (règle
    absolue n°3). Calcule aussi le rapport de vérification programmatique.

    Ces images de contrôle sont rendues depuis une copie du PPTX dont les
    images embarquées sont ramenées à ~150 dpi — déjà plus fin que le rendu
    lui-même, donc sans perte visible à l'écran. Le PPTX assemblé et le PDF
    d'`exporter_pdf` gardent leur PLEINE résolution : ce qui est validé ici
    correspond bien au livrable, seule l'empreinte mémoire de l'aperçu est
    réduite. Cette copie a aussi son cartouche adapté à LibreOffice (cf.
    core/cartouche_libreoffice.py), exactement comme celle d'`exporter_pdf` :
    les images montrent donc le cartouche tel qu'il sera dans le PDF.

    Fournissez EXACTEMENT UN des deux : `pptx_id` (chemin NORMAL, retourné
    par `assembler_pptx` — le PPTX reste sur le serveur) ou `pptx_base64`
    (repli, petits fichiers / tests directs). Ne téléchargez jamais le PPTX
    pour le ré-encoder en base64 : plusieurs Mo, bien au-delà du budget d'un
    agent.

    `words_par_page` (optionnel) : {"<page_num>": extraction_id | words_data,
    ...}, agrégé par l'appelant depuis les réponses de `extraire_page` sur
    TOUTES les pages extraites — planches dessin ET page specs : sans la
    page specs, les valeurs du tableau specs (vitesse, capacité, n° offre,
    RAL...) remontent à tort dans `deck_absents_de_la_source` (vu au 2e test
    Dust réel). Chaque valeur est SOIT l'`extraction_id` retourné
    par `extraire_page` (chemin NORMAL : ne transmet PAS le JSON complet),
    SOIT `words_data`/la réponse complète en repli (petits fichiers, tests
    directs). NE RECONSTRUISEZ JAMAIS ces données à la main : agréger
    plusieurs pages en JSON complet est le pire cas du problème de taille
    déjà rencontré sur `traduire_mots` — passez les `extraction_id` tels
    quels. Fourni, `words_par_page` active le CONTRÔLE DE COTES (confronte
    les nombres du PDF fabricant source à ceux du PPTX généré) — le contrôle
    programmatique le plus important du pipeline, puisqu'il n'y a ici aucun
    état côté serveur reliant ce PPTX à son PDF source. Sans lui,
    `rapport_cotes` est `null` : seul un contrôle de complétude générique est
    fait (cartouche sans résidu d'un ancien projet, deux contacts présents,
    aucun bloc de specs résiduel) — PAS de contrôle "ce cartouche correspond
    à ce projet".

    `meta` (optionnel, {"numero", "client", ...}) : fourni, active en plus la
    vérification que le numéro d'affaire et le client saisis apparaissent
    bien sur la garde/le cartouche.

    Retourne {"moteur", "slides": [{"url", "sha256"}, ...],
    "alertes_completude": [...], "rapport_cotes": {...} | null,
    "pptx_sha256": str, "pptx_url": str, "a_signaler_a_l_utilisateur": [str]}.
    Chaque slide, comme `pptx_url` (le PPTX vérifié lui-même, même sha256),
    est une URL de téléchargement à usage unique (5 min de durée de vie), pas
    du contenu inline : plusieurs images dans une seule réponse dépassent
    vite la limite de 1 Mio par réponse d'outil du protocole MCP.
    DÈS RÉCEPTION, téléchargez le PPTX et chaque image, et JOIGNEZ-LES comme
    fichiers réels dans la conversation — jamais d'URL brute à l'utilisateur
    — avec le message de `a_signaler_a_l_utilisateur` (il cite les sha256),
    AVANT de demander la validation. Si un téléchargement échoue, arrêtez-vous
    et dites-le clairement : ne fournissez jamais de document de
    remplacement. `pptx_sha256` DOIT être renvoyé
    tel quel à `exporter_pdf` (paramètre `pptx_sha256_verifie`) pour prouver
    que le PDF exporté correspond exactement à CE PPTX-ci, vu et validé par
    l'utilisateur sur ces images.
    """
    # Résolu AVANT le rendu (PowerPoint/LibreOffice, coûteux et parfois
    # fragile) : un extraction_id inconnu/expiré ou un words_data malformé
    # doit échouer immédiatement, pas après avoir gaspillé un rendu complet.
    wpp = None
    if words_par_page:
        wpp = {int(n): util.resoudre_entree_words_par_page(v) for n, v in words_par_page.items()}

    with util.travail_lourd("verifier_rendu"):
        contenu = util.resoudre_pptx(pptx_base64, pptx_id)
        pptx_sha256 = hashlib.sha256(contenu).hexdigest()

        with util.workdir_temporaire() as wd:
            pptx_path = wd / "plan.pptx"
            pptx_path.write_bytes(contenu)

            # Rendu fait sur une COPIE aux images réduites (cf.
            # LARGEUR_MAX_APERCU_PX) : le PPTX d'origine, lui, n'est jamais
            # modifié — c'est lui qui est contrôlé ci-dessous, dont
            # l'empreinte est renvoyée, et lui qu'`exporter_pdf` reprendra.
            apercu_path = util.copie_allegee(
                pptx_path, wd / "apercu.pptx", LARGEUR_MAX_APERCU_PX)

            with util.echec_rendu_explicite("verifier_rendu"):
                resultat_rendu = render.rendre_pngs(apercu_path, wd / "render")
            slides = []
            for p in resultat_rendu["pngs"]:
                publication = fichiers.publier(p, p.name, "image/png")
                slides.append({"url": publication["url"], "sha256": publication["sha256"]})

            alertes_completude = verify.controle_completude(pptx_path, meta or {})

            rapport_cotes = None
            if wpp is not None:
                rapport_cotes = verify.controle_cotes(wpp, pptx_path)

            # Lien FRAIS vers le PPTX vérifié lui-même (celui d'assembler_pptx
            # a expiré ou a déjà servi) : l'utilisateur juge sur les deux.
            pptx_publie = fichiers.publier(pptx_path, NOM_PPTX, fichiers.TYPE_PPTX)

            return {
                "moteur": resultat_rendu["moteur"],
                "slides": slides,
                "alertes_completude": alertes_completude,
                "rapport_cotes": rapport_cotes,
                "pptx_sha256": pptx_sha256,
                "pptx_url": pptx_publie["url"],
                "a_signaler_a_l_utilisateur": [
                    f"Fichiers joints pour validation : le PPTX vérifié ({NOM_PPTX}, "
                    f"sha256 {pptx_sha256}), fichier de travail à ouvrir dans "
                    f"PowerPoint, et {len(slides)} image(s) du rendu "
                    f"{resultat_rendu['moteur']}, qui est celui du PDF final. "
                    "Validez-vous ce rendu pour l'export PDF ?"
                ],
            }
