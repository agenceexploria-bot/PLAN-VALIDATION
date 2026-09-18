# -*- coding: utf-8 -*-
"""
app.py — Interface Streamlit "Plan de validation Vertical".

5 écrans séquentiels (cahier des charges) :
  1. Upload du PDF fabricant + inventaire des pages
  2. Métadonnées du dossier (n° affaire, client, dessinateur, indice, type)
  3. Traduction & assemblage (aperçu éditable avant montage du PPTX)
  4. Vérification (portail obligatoire — validation humaine explicite)
  5. Téléchargement (PPTX puis PDF, PDF débloqué seulement après validation)

Aucun export PDF automatique : le bouton "Générer le PDF" ne s'active qu'après
que l'utilisateur a explicitement validé l'écran de vérification (règle
absolue n°3 du cahier des charges).
"""
import os
import re
import secrets
import tempfile
from datetime import date
from pathlib import Path

import streamlit as st

from core import assemble, checklist as checklist_mod, cover, extract, memlog, render, translate, verify

st.set_page_config(page_title="Plan de validation Vertical", page_icon="🏗️", layout="wide")


# ---------------------------------------------------------------------------
# Authentification minimale (obligatoire — l'app est exposée publiquement
# sur une URL Render). Mot de passe unique lu depuis la variable
# d'environnement APP_PASSWORD, jamais codé en dur. Si la variable n'est
# pas définie (dev local, avant tout déploiement), l'accès reste libre
# plutôt que de bloquer un poste de dev.
# ---------------------------------------------------------------------------

def _authentifie() -> bool:
    mot_de_passe_attendu = os.environ.get("APP_PASSWORD")
    if not mot_de_passe_attendu:
        return True
    if st.session_state.get("authentifie"):
        return True

    st.title("🏗️ Plan de validation Vertical")
    st.text_input("Mot de passe", type="password", key="mdp_saisi")
    if st.button("Se connecter"):
        if secrets.compare_digest(st.session_state.get("mdp_saisi", ""), mot_de_passe_attendu):
            st.session_state.authentifie = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    return False


if not _authentifie():
    st.stop()

st.title("🏗️ Plan de validation Vertical")

ROLES = [
    "Page de garde (vue 3D)",
    "Planche dessin (cotée)",
    "Alimente la page specs",
    "Ignorée (administrative fabricant)",
]

# Bug audit RAM Render : un PDF fabricant énorme peut saturer la mémoire
# bien avant l'étape 4 sans message clair — mieux vaut refuser tôt, avec
# une explication, que planter plus loin sans traceback (cf. OOM constaté).
LIMITE_PDF_MO = 50

# ---------------------------------------------------------------------------
# État de session
# ---------------------------------------------------------------------------

def _init_etat():
    defaut = {
        "etape": 1,
        "workdir": Path(tempfile.mkdtemp(prefix="plan_validation_")),
        "pdf_path": None,
        "apercus": None,
        "pages_sans_texte_apercu": [],
        "roles": None,
        "meta": {},
        "checklist_path": None,
        "traite": False,
        "words_par_page": {},
        "planches": None,
        "view3d": None,
        "specs_table": None,
        "champs_commerciaux_ajoutes": [],
        "pages_scannees": [],
        "hors_glossaire": [],
        "pages_ignorees": [],
        "pptx_path": None,
        "png_paths": None,
        "visuel_valide": False,
        "render_engine": None,
        "rapport_texte": None,
        "pdf_path_final": None,
    }
    for cle, valeur in defaut.items():
        if cle not in st.session_state:
            st.session_state[cle] = valeur


_init_etat()


def aller_a(n):
    st.session_state.etape = n


# ---------------------------------------------------------------------------
# Fil d'Ariane
# ---------------------------------------------------------------------------

LIBELLES_ETAPES = [
    "1. Upload & inventaire", "2. Métadonnées", "3. Traduction & assemblage",
    "4. Vérification", "5. Téléchargement",
]
st.caption(" → ".join(
    f"**{lib}**" if i + 1 == st.session_state.etape else lib
    for i, lib in enumerate(LIBELLES_ETAPES)
))
st.divider()


# ---------------------------------------------------------------------------
# ÉTAPE 1 — Upload + inventaire
# ---------------------------------------------------------------------------

def etape_1():
    st.header("1. Plan fabricant : dépôt et inventaire des pages")
    st.caption(
        "Les fabricants sont hétérogènes : le découpage proposé ci-dessous est "
        "un défaut raisonnable (1re page = garde), à corriger si besoin."
    )

    fichier = st.file_uploader("Plan fabricant (PDF vectoriel, EN/IT/TR)", type=["pdf"])
    if fichier is None:
        return

    taille_mo = fichier.size / 1e6
    if taille_mo > LIMITE_PDF_MO:
        st.error(
            f"Fichier trop volumineux ({taille_mo:.0f} Mo, limite {LIMITE_PDF_MO} Mo) : "
            "un PDF fabricant aussi lourd risque de saturer la mémoire disponible "
            "pendant le traitement, surtout sur le palier gratuit Render (512 Mo). "
            "Compressez-le ou scindez-le avant de le déposer ici."
        )
        return

    chemin_pdf = st.session_state.workdir / "plan_fabricant.pdf"
    if st.session_state.pdf_path != chemin_pdf:
        chemin_pdf.write_bytes(fichier.getvalue())
        st.session_state.pdf_path = chemin_pdf
        st.session_state.apercus = None  # force le re-rendu si nouveau fichier

    if st.session_state.apercus is None:
        with st.spinner("Rendu des aperçus de pages…"):
            st.session_state.apercus = extract.rendre_apercus(chemin_pdf, dpi=100)
            st.session_state.roles = [
                ROLES[0] if i == 0 else ROLES[1]
                for i in range(len(st.session_state.apercus))
            ]
            st.session_state.pages_sans_texte_apercu = extract.pages_sans_texte(chemin_pdf)

    apercus = st.session_state.apercus
    st.success(f"{len(apercus)} page(s) détectée(s).")
    if st.session_state.pages_sans_texte_apercu:
        st.warning(
            "Page(s) sans couche texte détectée(s) (PDF scanné probable) : "
            + ", ".join(str(p) for p in st.session_state.pages_sans_texte_apercu)
            + ". Sur ces pages, aucune traduction ni rédaction automatique ne "
            "sera possible — à vérifier avant de poursuivre."
        )

    cols = st.columns(3)
    for i, png in enumerate(apercus):
        with cols[i % 3]:
            st.image(png, caption=f"Page {i + 1}", width='stretch')
            st.session_state.roles[i] = st.selectbox(
                f"Rôle — page {i + 1}", ROLES,
                index=ROLES.index(st.session_state.roles[i]),
                key=f"role_{i}",
            )

    n_planches = st.session_state.roles.count(ROLES[1])
    n_specs = st.session_state.roles.count(ROLES[2])
    if n_planches == 0 and n_specs == 0:
        st.warning("Sélectionnez au moins une planche dessin ou une page specs pour continuer.")
    else:
        st.button("Valider l'inventaire →", type="primary", on_click=aller_a, args=(2,))


# ---------------------------------------------------------------------------
# ÉTAPE 2 — Métadonnées projet
# ---------------------------------------------------------------------------

def etape_2():
    st.header("2. Informations du dossier")
    st.caption("Obligatoires — jamais de valeur fictive dans le cartouche final.")

    meta = st.session_state.meta
    col_g, col_d = st.columns(2)
    with col_g:
        numero = st.text_input("Numéro d'affaire (LDxxxxx)", value=meta.get("numero", ""), placeholder="LD12345")
        client = st.text_input("Nom du client", value=meta.get("client", ""))
        indice = st.text_input("Indice", value=meta.get("indice", "R00"))
    with col_d:
        dessinateur = st.text_input("Dessinateur (initiales)", value=meta.get("dessinateur", ""))
        date_creation = st.date_input("Date de création", value=date.today())
        type_equipement = st.selectbox(
            "Type de monte-charge", ["Non accompagné", "Accompagné"],
            index=0 if meta.get("type_equipement", "Non accompagné") == "Non accompagné" else 1,
        )

    st.subheader("Checklist revue d'affaire (optionnel)")
    st.caption(
        "⚠️ Souvent le modèle vierge : dans ce cas elle ne sera PAS utilisée "
        "comme source de comparaison — signalé dans le rapport de vérification."
    )
    fichier_checklist = st.file_uploader("Checklist (.xlsm / .xlsx)", type=["xlsm", "xlsx"])
    if fichier_checklist is not None:
        chemin = st.session_state.workdir / "checklist.xlsm"
        chemin.write_bytes(fichier_checklist.getvalue())
        st.session_state.checklist_path = chemin

    valide = bool(re.match(r"^LD\w+$", numero.strip(), re.IGNORECASE)) and client.strip() and dessinateur.strip() and indice.strip()
    if not valide:
        st.info("Numéro d'affaire (format LDxxxxx), client, dessinateur et indice sont obligatoires.")

    col1, col2 = st.columns(2)
    col1.button("← Retour", on_click=aller_a, args=(1,))
    if col2.button("Extraire et traduire →", type="primary", disabled=not valide):
        st.session_state.meta = {
            "numero": numero.strip(), "client": client.strip(),
            "dessinateur": dessinateur.strip(), "indice": indice.strip(),
            "date": date_creation.strftime("%d/%m/%Y"),
            "equipement": "MONTE-CHARGE",
            "type_equipement": type_equipement,
        }
        st.session_state.traite = False  # force le retraitement à l'étape 3
        aller_a(3)
        st.rerun()


# ---------------------------------------------------------------------------
# ÉTAPE 3 — Traduction & assemblage
# ---------------------------------------------------------------------------

def _traiter_pipeline():
    """Extraction + traduction de toutes les pages retenues (étapes 2 et 3
    du pipeline documentaire). Idempotent tant que roles/meta ne changent
    pas (gardé par `st.session_state.traite`)."""
    wd = st.session_state.workdir
    pdf_path = st.session_state.pdf_path
    roles = st.session_state.roles

    pages_garde = [i + 1 for i, r in enumerate(roles) if r == ROLES[0]]
    pages_planches = [i + 1 for i, r in enumerate(roles) if r == ROLES[1]]
    pages_specs = [i + 1 for i, r in enumerate(roles) if r == ROLES[2]]
    pages_ignorees = [i + 1 for i, r in enumerate(roles) if r == ROLES[3]]

    # Résolution adaptée à l'usage de chaque image, pas 300 dpi partout
    # (bug audit poids/mémoire) : les planches cotées gardent 300 dpi (seul
    # endroit où la lisibilité des petites cotes compte) ; la page de garde
    # (vue 3D, visuel produit sans cotation fine) se contente de 200 dpi ;
    # les pages "specs" n'ont besoin d'AUCUNE image (seul leur texte
    # alimente le tableau reconstruit — leur rendu n'est jamais embarqué).
    DPI_GARDE = 200
    DPI_PLANCHE = 300
    with st.spinner("Extraction du PDF (cotes, rédaction)…"):
        words_par_page = {}
        if pages_garde:
            words_par_page.update(extract.extraire_pdf(pdf_path, wd, pages_garde, dpi=DPI_GARDE))
        if pages_planches:
            words_par_page.update(extract.extraire_pdf(pdf_path, wd, pages_planches, dpi=DPI_PLANCHE))
        if pages_specs:
            # Rôles mutuellement exclusifs par page (cf. ROLES/selectbox) :
            # pages_specs ne recoupe jamais pages_garde/pages_planches.
            words_par_page.update(
                extract.extraire_pdf(pdf_path, wd, pages_specs, generer_image=False)
            )
    memlog.logger_etape("extraction PDF")

    # PDF scanné (page sans couche texte exploitable) : aucune traduction ni
    # rédaction n'a pu s'appliquer sur ces pages — à signaler explicitement
    # plutôt que de laisser passer un échec silencieux (bug audit).
    pages_scannees = [n for n, wd_page in words_par_page.items() if extract.est_pdf_scanne(wd_page)]

    hors_glossaire_total = []

    planches = []
    for n in pages_planches:
        labels, hg = translate.traduire_labels_planche(words_par_page[n])
        hors_glossaire_total += hg
        planches.append({"image": f"page_{n}_redacted.png", "page_n": n, "labels": labels})

    specs_table = []
    for n in pages_specs:
        table_fr, hg = translate.extraire_tableau_specs(words_par_page[n])
        hors_glossaire_total += hg
        specs_table += table_fr
    specs_table, ajouts = translate.completer_champs_commerciaux(specs_table)

    view3d = None
    if pages_garde:
        n = pages_garde[0]
        if len(pages_garde) > 1:
            st.warning(
                f"{len(pages_garde)} pages marquées « {ROLES[0]} » : seule la "
                f"page {n} est utilisée pour la vue 3D déposée sur la page specs."
            )
        try:
            image_redigee = wd / f"page_{n}_redacted.png"
            crop_path = wd / "cover_3d_full.png"
            info = cover.extraire_vue_3d(image_redigee, crop_path, words_data=words_par_page[n], dpi=DPI_GARDE)
            view3d = {"image": "cover_3d_full.png", "image_page_w_pt": info["width_pt"], "callouts": []}
        except ValueError as e:
            st.warning(f"Vue 3D (page {n}) : {e} — elle sera omise de la page specs.")

    st.session_state.words_par_page = words_par_page
    st.session_state.planches = planches
    st.session_state.specs_table = specs_table
    st.session_state.champs_commerciaux_ajoutes = ajouts
    st.session_state.view3d = view3d
    st.session_state.hors_glossaire = hors_glossaire_total
    st.session_state.pages_ignorees = pages_ignorees
    st.session_state.pages_scannees = pages_scannees
    st.session_state.traite = True
    memlog.logger_etape("traduction")


def _nettoyer_fichiers_intermediaires():
    """Supprime les PNG/JSON intermédiaires d'extraction (page_N_redacted.png,
    page_N_words.json, cover_3d_full.png) une fois le PPTX assemblé : plus
    jamais relus après ce point (le rendu de vérification n'ouvre que le
    PPTX déjà assemblé ; le contrôle de cotes utilise
    st.session_state.words_par_page déjà en mémoire, jamais les JSON sur
    disque). Sans ça, ils s'accumuleraient sur le disque éphémère de Render
    au fil des sessions (bug audit poids) — le PPTX/PDF final n'en dépend
    plus une fois généré."""
    wd = st.session_state.workdir
    for motif in ("page_*_redacted.png", "page_*_words.json", "cover_3d_full.png"):
        for f in wd.glob(motif):
            f.unlink(missing_ok=True)


def etape_3():
    st.header("3. Traduction & assemblage")

    if not st.session_state.traite:
        _traiter_pipeline()

    if st.session_state.pages_scannees:
        st.warning(
            "Page(s) sans couche texte exploitable (PDF scanné) parmi les pages "
            "retenues : " + ", ".join(str(p) for p in st.session_state.pages_scannees)
            + ". Aucune traduction/rédaction automatique n'a pu être appliquée sur "
            "ces pages — à traiter manuellement. Le contrôle de cotes de l'étape 4 "
            "sera marqué « NON VÉRIFIABLE » pour ce dossier."
        )

    if st.session_state.hors_glossaire:
        vus = {}
        for t in st.session_state.hors_glossaire:
            vus[t["source"]] = t["traduction"]
        with st.expander(f"⚠️ {len(vus)} terme(s) hors glossaire (à valider par Marin)", expanded=False):
            for source, trad in vus.items():
                st.write(f"- « {source} » → proposition : « {trad} » (non modifié, à valider)")

    st.subheader("Tableau specs (éditable)")
    st.caption(
        "Reconstruction automatique à partir du plan fabricant — imparfaite sur "
        "des mises en page tabulaires complexes : corrigez les lignes nécessaires "
        "avant l'assemblage. Les rubriques commerciales absentes du plan ont été "
        "ajoutées avec la mention « à confirmer (service commercial) »."
    )
    if st.session_state.champs_commerciaux_ajoutes:
        st.info(
            "Rubriques commerciales ajoutées (jamais sur le plan fabricant) : "
            + ", ".join(st.session_state.champs_commerciaux_ajoutes)
        )

    lignes = [{"Libellé": lab, "Valeur": val} for lab, val in st.session_state.specs_table]
    lignes_editees = st.data_editor(lignes, num_rows="dynamic", width='stretch', key="editeur_specs")

    st.subheader("Planches dessin")
    st.caption(f"{len(st.session_state.planches)} planche(s) à assembler, étiquettes FR superposées automatiquement.")
    for p in st.session_state.planches:
        st.write(f"- Page {p['page_n']} → {len(p['labels'])} étiquette(s)")

    if st.session_state.view3d:
        st.success("Vue 3D fournisseur extraite, sera déposée sur la page specs.")

    col1, col2 = st.columns(2)
    col1.button("← Retour", on_click=aller_a, args=(2,))
    if col2.button("Assembler le PPTX →", type="primary"):
        specs_table_final = [(l["Libellé"], l["Valeur"]) for l in lignes_editees if l.get("Libellé")]
        meta = st.session_state.meta
        base = assemble.base_pour_type(meta["type_equipement"].lower())
        nom_fichier = f"Plan de validation {meta['numero']}-{meta['indice']}.pptx"
        out_path = st.session_state.workdir / nom_fichier
        proj = {
            "base": base, "out": out_path, "workdir": st.session_state.workdir,
            "meta": meta, "specs": {"table": specs_table_final},
            "view3d": st.session_state.view3d, "planches": st.session_state.planches,
        }
        with st.spinner("Montage du PPTX…"):
            try:
                assemble.assembler(proj)
            except ValueError as e:
                st.error(f"Assemblage impossible : {e}")
                st.stop()
        memlog.logger_etape("assemblage PPTX")
        _nettoyer_fichiers_intermediaires()
        st.session_state.pptx_path = out_path
        st.session_state.specs_table = specs_table_final
        aller_a(4)
        st.rerun()


# ---------------------------------------------------------------------------
# ÉTAPE 4 — Vérification (portail obligatoire)
# ---------------------------------------------------------------------------

def etape_4():
    st.header("4. Vérification — portail obligatoire avant export PDF")
    st.caption(
        "Le plan de validation devient la référence contractuelle de fabrication. "
        "Le PDF n'est exporté qu'après votre accord explicite ci-dessous, même si "
        "tous les contrôles sont PASS."
    )

    pptx_path = st.session_state.pptx_path
    meta = st.session_state.meta

    with st.spinner("Contrôle des cotes…"):
        rapport_cotes = verify.controle_cotes(
            st.session_state.words_par_page, pptx_path, st.session_state.checklist_path,
            pages_scannees=st.session_state.pages_scannees,
        )
    alertes_completude = verify.controle_completude(pptx_path, meta)
    for rubrique in st.session_state.champs_commerciaux_ajoutes:
        alertes_completude.append(
            f"Rubrique « {rubrique} » non fournie par le plan fabricant, "
            f"marquée « {translate.CHAMP_A_CONFIRMER} »."
        )

    col1, col2, col3 = st.columns(3)
    col1.metric("1. Cotes", rapport_cotes["statut_cotes"])
    col2.metric("2. Visuel", "validé" if st.session_state.visuel_valide else "en attente")
    col3.metric("3. Complétude", "PASS" if not alertes_completude else f"{len(alertes_completude)} manque(s)")

    if rapport_cotes.get("pages_scannees"):
        st.warning(
            "Contrôle de cotes non vérifiable : page(s) "
            + ", ".join(str(p) for p in rapport_cotes["pages_scannees"])
            + " sans couche texte exploitable (PDF scanné) — à vérifier manuellement."
        )

    if rapport_cotes["deck_absents_de_la_source"]:
        st.error(
            "BLOQUANT — valeur(s) dans le document absente(s) de la source "
            "(jamais acceptable, remonter à l'origine) : "
            + ", ".join(rapport_cotes["deck_absents_de_la_source"])
        )
    if rapport_cotes["source_absents_du_deck"]:
        with st.expander(f"{len(rapport_cotes['source_absents_du_deck'])} cote(s) source absente(s) du texte du deck"):
            st.caption(rapport_cotes["nota"])
            st.write(", ".join(rapport_cotes["source_absents_du_deck"]))

    if rapport_cotes.get("checklist"):
        cl = rapport_cotes["checklist"]
        if cl.get("invalide"):
            st.warning(cl["message"])
        elif cl.get("vierge"):
            st.info(cl["message"])
        elif cl.get("valeurs_checklist_absentes_du_dossier"):
            st.warning(
                "Checklist — valeurs présentes dans la checklist mais absentes "
                "du dossier (à arbitrer) : "
                + ", ".join(cl["valeurs_checklist_absentes_du_dossier"])
            )

    if alertes_completude:
        with st.expander(f"Complétude — {len(alertes_completude)} point(s)", expanded=True):
            for a in alertes_completude:
                st.write(f"- {a}")

    st.subheader("2. Contrôle visuel — rendu réel")
    if not render.disponible():
        st.warning(
            "Ni PowerPoint ni LibreOffice ne sont disponibles sur ce poste : le "
            "rendu visuel réel ne peut pas être généré ici. Ouvrez le PPTX "
            "téléchargé dans l'un de ces logiciels pour effectuer cette "
            "vérification manuellement."
        )
    else:
        if st.session_state.png_paths is None:
            if st.button("Générer le rendu réel"):
                with st.spinner("Rendu en cours…"):
                    try:
                        resultat = render.rendre_pngs(pptx_path, st.session_state.workdir / "render")
                        st.session_state.png_paths = resultat["pngs"]
                        st.session_state.render_engine = resultat["moteur"]
                    except RuntimeError as e:
                        st.error(str(e))
                    memlog.logger_etape("rendu de vérification")
                st.rerun()
        else:
            if st.session_state.render_engine == render.MOTEUR_POWERPOINT:
                st.caption("Rendu généré via PowerPoint (référence visuelle).")
            else:
                st.warning(
                    "Rendu généré via LibreOffice — PowerPoint indisponible sur "
                    "ce poste. Ce rendu peut différer légèrement du rendu "
                    "PowerPoint de référence (polices, interlignes, "
                    "positionnement fin des étiquettes) : vérifiez avec une "
                    "attention particulière."
                )
            st.caption(
                "Vérifiez chaque planche : vue complète, aucune étiquette ne masque "
                "une cote, aucun résidu anglais/turc non traduit, page de garde "
                "correcte (deux contacts, image générique)."
            )
            for i, png in enumerate(st.session_state.png_paths):
                st.image(str(png), caption=f"Slide {i + 1}", width='stretch')
            st.session_state.visuel_valide = st.checkbox(
                "Je confirme avoir vérifié le rendu réel ci-dessus, il est conforme.",
                value=st.session_state.visuel_valide,
            )

    rapport_texte = verify.construire_rapport(
        pptx_path.name, rapport_cotes, alertes_completude,
        st.session_state.pages_ignorees, st.session_state.hors_glossaire,
        st.session_state.visuel_valide,
    )
    st.session_state.rapport_texte = rapport_texte

    st.subheader("Rapport de vérification")
    st.text(rapport_texte)
    st.download_button("Télécharger le rapport (.txt)", rapport_texte,
                        file_name=f"Rapport verification {meta['numero']}-{meta['indice']}.txt")

    col1, col2 = st.columns(2)
    col1.button("← Retour (corriger)", on_click=lambda: (aller_a(3), st.session_state.__setitem__("traite", False)))
    accord = st.checkbox("Le rapport est validé — j'autorise le passage au téléchargement.",
                          disabled=not st.session_state.visuel_valide)
    if col2.button("Continuer →", type="primary", disabled=not accord):
        aller_a(5)
        st.rerun()


# ---------------------------------------------------------------------------
# ÉTAPE 5 — Téléchargement
# ---------------------------------------------------------------------------

def etape_5():
    st.header("5. Téléchargement")
    meta = st.session_state.meta
    pptx_path = st.session_state.pptx_path

    st.subheader("Document de travail (PPTX, éditable)")
    with open(pptx_path, "rb") as f:
        st.download_button(
            "Télécharger le PPTX", data=f.read(), file_name=pptx_path.name,
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )

    st.subheader("Export PDF (diffusion)")
    if not st.session_state.visuel_valide:
        st.warning("Le rapport de vérification n'a pas été validé — retournez à l'étape 4.")
    elif not render.disponible():
        st.warning("Ni PowerPoint ni LibreOffice disponibles sur ce poste : export PDF impossible ici. Utilisez l'un de ces logiciels directement (Fichier → Exporter en PDF) sur le PPTX téléchargé.")
    elif st.session_state.pdf_path_final is None:
        # Réutilise IMPÉRATIVEMENT le même moteur que celui qui a produit le
        # rendu de vérification déjà validé à l'étape 4 (jamais un moteur
        # différent choisi au hasard entre les deux étapes) : sans bascule,
        # le bouton échoue explicitement plutôt que de livrer un PDF produit
        # par un moteur différent de ce qui a été validé visuellement.
        if st.button("Générer le PDF", type="primary"):
            moteur_libelle = "PowerPoint" if st.session_state.render_engine == render.MOTEUR_POWERPOINT else "LibreOffice"
            with st.spinner(f"Export PDF via {moteur_libelle}…"):
                try:
                    resultat = render.exporter_pdf(pptx_path, moteur=st.session_state.render_engine)
                    st.session_state.pdf_path_final = resultat["pdf"]
                except RuntimeError as e:
                    st.error(str(e))
            st.rerun()
    else:
        moteur_libelle = "PowerPoint" if st.session_state.render_engine == render.MOTEUR_POWERPOINT else "LibreOffice"
        st.caption(f"PDF exporté via {moteur_libelle} — même moteur que le rendu de vérification validé.")
        with open(st.session_state.pdf_path_final, "rb") as f:
            st.download_button(
                "Télécharger le PDF", data=f.read(),
                file_name=st.session_state.pdf_path_final.name, mime="application/pdf",
            )

    st.divider()
    if st.button("Nouveau dossier"):
        for cle in list(st.session_state.keys()):
            del st.session_state[cle]
        st.rerun()


# ---------------------------------------------------------------------------
# Routage
# ---------------------------------------------------------------------------

{1: etape_1, 2: etape_2, 3: etape_3, 4: etape_4, 5: etape_5}[st.session_state.etape]()
