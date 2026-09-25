# -*- coding: utf-8 -*-
"""
mcp_server/server.py — serveur MCP « Plan de Validation Vertical », pour
exposer le pipeline (core/) comme des outils utilisables par un agent Dust.

Chaque outil reçoit et renvoie ses données métier explicitement (jamais
d'état business caché entre deux appels) — SAUF deux exceptions, mises en
cache côté serveur le temps d'un pipeline : le PDF fabricant en entrée
(mcp_server/pdf_cache.py) et les données de mots extraits par page
(mcp_server/extraction_cache.py). Dans les deux cas, un agent Dust réel n'a
pas les moyens de relayer le JSON/base64 complet d'un appel d'outil à
l'autre (constaté en conditions réelles : base64 impossible à produire pour
le PDF, JSON trop volumineux à retransmettre pour les mots extraits d'une
page dense) — d'où le point d'entrée d'upload direct `POST /pdfs` (hors
canal MCP) pour le PDF, et les identifiants `pdf_id`/`extraction_id` à
réutiliser tels quels pour tout le reste.

DEUX jetons Bearer DISTINCTS (mcp_server/auth.py), vérifiés sur CHAQUE
requête — le serveur refuse de démarrer si l'un des deux est absent/vide :
`MCP_AUTH_TOKEN` protège `/mcp` (tous les outils), `PDF_UPLOAD_TOKEN`
protège UNIQUEMENT `POST /pdfs`. Portées volontairement séparées :
`PDF_UPLOAD_TOKEN` est destiné à apparaître en clair dans les instructions
d'un agent Dust (aucun mécanisme de secret injectable trouvé côté Dust), sa
fuite est donc une éventualité réaliste — un jeton à portée réduite limite
les dégâts à « peut téléverser des PDF », jamais à « peut appeler
exporter_pdf ». `MCP_AUTH_TOKEN`, lui, n'apparaît jamais dans un texte
destiné à être collé où que ce soit.
"""
import functools
import os
from urllib.parse import urlparse

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import fichiers, pdf_cache, util
from .auth import BearerAuthMiddleware, jeton_attendu, jeton_upload_attendu
from .tools import assemblage, export, extraction, inventaire, traduction, verification

_INSTRUCTIONS = (
    "Pipeline Plan de Validation Vertical : transforme un plan de production "
    "fabricant (PDF, anglais/italien/turc) en Plan de validation Vertical "
    "(PPTX puis PDF), à la charte de l'entreprise. Règles absolues, non "
    "négociables : (1) les dessins ne sont JAMAIS redessinés, seulement "
    "extraits en image et annotés par-dessus ; (2) aucune valeur numérique "
    "n'est retapée de mémoire, toujours extraite programmatiquement ; "
    "(3) le PDF final n'est généré qu'après validation EXPLICITE de "
    "l'utilisateur du rendu visuel — jamais d'export automatique ; "
    "(4) le PDF fabricant fourni par l'utilisateur ne s'encode PAS en base64 "
    "à la main : téléversez-le d'abord par une commande shell dans votre bac "
    f"à sable, ex. curl -X POST {fichiers.base_url_publique()}{pdf_cache.PREFIXE_ROUTE} "
    "-H 'Authorization: Bearer <PDF_UPLOAD_TOKEN>' -F pdf=@<chemin_du_fichier>.pdf "
    "(jeton DISTINCT de celui des appels d'outils, portée réduite à "
    "l'upload), qui retourne {pdf_id, expire_dans_s} ; réutilisez ce pdf_id tel quel pour "
    "inventaire_pdf PUIS chaque extraire_page, jamais de base64 manuel ; "
    "(5) aucune sortie d'outil ne se recopie à la main dans les appels "
    "suivants — ni mots (words), ni images, ni PPTX : chaque outil renvoie un "
    "identifiant à réutiliser tel quel (extraction_id, pptx_id). Ne "
    "téléchargez JAMAIS une image ou un PPTX pour le ré-encoder en base64 "
    "(une planche A3 à 300 dpi ≈ 165k jetons : un pipeline réel a déjà "
    "échoué ainsi) ; les URLs de téléchargement servent seulement à montrer "
    "un fichier à l'utilisateur. "
    "Ordre d'appel attendu : POST /pdfs (obtenir pdf_id) -> "
    "inventaire_pdf(pdf_id=...) (classer les pages : identifiez la page qui "
    "porte la vue 3D fournisseur et le tableau specs — souvent la même, "
    "généralement la page 1 — ET les planches cotées ; ne traitez JAMAIS que "
    "les planches cotées) -> "
    "extraire_page(pdf_id=...) UNE fois par page retenue (role='specs' pour "
    "la page vue 3D/specs, role='planche' pour chaque planche cotée), obtenir "
    "extraction_id -> traduire_mots(extraction_id=..., même role) sur chacune "
    "-> assembler_pptx(planches=[{extraction_id}, ...], specs={extraction_id} "
    "OBLIGATOIRE — la vue 3D de cette page est reprise automatiquement ; "
    "view3d={extraction_id} seulement si la vue 3D est sur une AUTRE page) "
    "(obtenir pptx_id ; recopiez à l'utilisateur chaque message de "
    "a_signaler_a_l_utilisateur AVANT d'appeler verifier_rendu) "
    "-> verifier_rendu(pptx_id=..., words_par_page={page: extraction_id, ...} "
    "pour TOUTES les pages extraites, page specs comprise — sinon les valeurs "
    "du tableau specs remontent à tort comme absentes de la source) "
    "(montrer les images produites à l'utilisateur et obtenir son accord "
    "explicite) -> exporter_pdf(pptx_id=..., valide=True seulement "
    "après cet accord)."
)

server = MCPServer(name="plan-validation-vertical", instructions=_INSTRUCTIONS)

def _erreurs_explicites(outil):
    """Le SDK mcp (2.x) ne transmet au client QUE le texte d'une `ToolError` :
    toute autre exception arrive réduite à « Error executing tool <nom> »,
    sans le message (constaté en conditions réelles : assembler_pptx et
    traduire_mots échouaient « sans message exploitable » côté Dust, alors
    que nos ValueError nommaient bien le champ en cause). Nos outils lèvent
    ValueError pour toute entrée refusée — convertie ici en ToolError pour
    que l'agent lise la cause. Les autres exceptions (vrais plantages)
    restent masquées côté client, comme le veut le SDK."""
    @functools.wraps(outil)
    def enveloppe(*args, **kwargs):
        try:
            return outil(*args, **kwargs)
        except ValueError as e:
            raise ToolError(str(e)) from e
    return enveloppe


# Chaque outil est une fonction Python ordinaire, testable directement sans
# passer par le protocole MCP (cf. tests/mcp/test_tools_local.py) ; enregistrée
# ici explicitement plutôt que par décorateur, pour ne pas dépendre d'un ordre
# d'import fragile entre server.py et tools/*.py.
for _outil in (
    inventaire.inventaire_pdf, extraction.extraire_page, traduction.traduire_mots,
    assemblage.assembler_pptx, verification.verifier_rendu, export.exporter_pdf,
):
    server.add_tool(_erreurs_explicites(_outil))


@server.custom_route(f"{fichiers.PREFIXE_ROUTE}/{{jeton}}", methods=["GET"])
async def telecharger_fichier(request: Request) -> Response:
    """Téléchargement à usage unique d'un fichier publié par un outil
    (PPTX, image, PDF) — cf. mcp_server/fichiers.py pour le modèle de
    sécurité. EXEMPTÉ du jeton Bearer MCP_AUTH_TOKEN (cf. construire_app) :
    la sécurité vient du jeton imprévisible dans l'URL elle-même."""
    resultat = fichiers.recuperer_et_invalider(request.path_params["jeton"])
    if resultat is None:
        return JSONResponse({"error": "lien invalide, expiré ou déjà utilisé"}, status_code=404)
    return Response(
        content=resultat["contenu"],
        media_type=resultat["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{resultat["nom_fichier"]}"'},
    )


@server.custom_route(pdf_cache.PREFIXE_ROUTE, methods=["POST"])
async def televerser_pdf(request: Request) -> Response:
    """Upload direct d'un PDF fabricant, HORS du canal MCP (pas de limite
    ~1 Mio de réponse d'outil, pas de base64 à produire côté agent) — cf.
    mcp_server/pdf_cache.py pour pourquoi ce point d'entrée existe. Reçoit
    un `multipart/form-data` avec un champ `pdf` (le fichier). PROTÉGÉ par
    PDF_UPLOAD_TOKEN — un jeton DISTINCT de MCP_AUTH_TOKEN (mcp_server/auth.py),
    à portée réduite à ce seul point d'entrée (contrairement à
    `/fichiers/...`, lui volontairement EXEMPTÉ de tout jeton : ceci reste un
    point d'entrée serveur-à-serveur, jamais cliqué par l'utilisateur final,
    mais avec son propre jeton parce que PDF_UPLOAD_TOKEN est destiné à être
    collé en clair dans les instructions d'un agent Dust). Retourne
    {"pdf_id", "expire_dans_s"} à passer tel quel à `inventaire_pdf`/
    `extraire_page` à la place de `pdf_base64`."""
    longueur = request.headers.get("content-length")
    if longueur is not None and int(longueur) > TAILLE_MAX_REQUETE_OCTETS:
        return JSONResponse({"error": "corps de requête trop volumineux"}, status_code=413)
    form = await request.form()
    televerse = form.get("pdf")
    if televerse is None or not hasattr(televerse, "read"):
        return JSONResponse({"error": "champ multipart 'pdf' manquant"}, status_code=400)
    contenu = await televerse.read()
    await televerse.close()
    try:
        util.verifier_taille_pdf(contenu)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=413)
    return JSONResponse(pdf_cache.mettre_en_cache(contenu))

# Taille max d'une requête HTTP acceptée par le serveur MCP. Un PDF fabricant
# de 50 Mo (mcp_server.util.LIMITE_PDF_MO) inflate à ~67 Mo une fois encodé en
# base64, plus l'enveloppe JSON-RPC — la valeur par défaut du SDK (4 Mo)
# rejetterait tout PDF réel avant même d'atteindre notre propre contrôle de
# taille. Généreux à dessein : une réponse peut aussi transporter plusieurs
# images de vérification en base64 dans le même échange.
TAILLE_MAX_REQUETE_OCTETS = 90_000_000


def _parametres_securite_transport() -> TransportSecuritySettings:
    """Hôtes/Origins autorisés pour la protection anti DNS-rebinding du SDK
    mcp. `streamable_http_app()` ne l'auto-active QUE quand `host` vaut son
    défaut "127.0.0.1" (cf. mcp.server.lowlevel.server) — comme nous ne lui
    passons jamais ce paramètre, elle se déclenchait quand même avec une
    liste d'hôtes limitée à localhost, rejetant en production toute requête
    dont le Host réel (ex: plan-validation-mcp.onrender.com) n'y figurait
    pas ("Invalid Host header"). On la déclare nous-mêmes pour couvrir à la
    fois les tests locaux (127.0.0.1/localhost) et le domaine public réel
    (cf. fichiers.base_url_publique), sans désactiver la protection."""
    hotes = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    origines = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    url_publique = fichiers.base_url_publique().rstrip("/")
    hote_public = urlparse(url_publique).netloc
    if hote_public and hote_public not in hotes:
        hotes.append(hote_public)
        origines.append(url_publique)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hotes,
        allowed_origins=origines,
    )


def construire_app():
    """Construit l'app ASGI (Starlette) du serveur MCP, protégée par DEUX
    jetons Bearer distincts (mcp_server/auth.py). Lève RuntimeError si
    MCP_AUTH_TOKEN OU PDF_UPLOAD_TOKEN est absent/vide — appelé au
    démarrage, jamais de service exposé sans les deux jetons définis."""
    jeton = jeton_attendu()
    jeton_upload = jeton_upload_attendu()
    # stateless_http=False (défaut) : le protocole MCP garde une session
    # légère (Mcp-Session-Id) pendant la durée d'UNE connexion client — c'est
    # le mécanisme de transport (achemine les réponses SSE), pas un état
    # métier. `stateless_http=True` cassait le suivi des réponses (« SSE
    # stream ended without a response » dès le premier appel d'outil, cf.
    # tests/mcp/test_server_http.py). Sans rapport avec le cache du PDF
    # fabricant (mcp_server/pdf_cache.py), qui vit dans son propre registre
    # en mémoire, indépendant de cette session de transport.
    app = server.streamable_http_app(
        max_request_body_size=TAILLE_MAX_REQUETE_OCTETS,
        transport_security=_parametres_securite_transport(),
    )
    app.add_middleware(
        BearerAuthMiddleware, jeton=jeton, prefixes_exemptes=(fichiers.PREFIXE_ROUTE,),
        jetons_par_prefixe={pdf_cache.PREFIXE_ROUTE: jeton_upload},
    )
    return app


def main():
    app = construire_app()
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
