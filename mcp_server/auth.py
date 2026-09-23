# -*- coding: utf-8 -*-
"""
mcp_server/auth.py — vérification du jeton Bearer sur chaque requête HTTP.
Même durcissement que app.py::_authentifie (LOT E, app Streamlit) : le
serveur refuse de démarrer sans jeton défini, et la comparaison se fait sur
des octets UTF-8 (secrets.compare_digest refuse les `str` non-ASCII — un
jeton accentué planterait sinon).

Deux jetons DISTINCTS, à portée différente (cf. mcp_server/pdf_cache.py) :
`MCP_AUTH_TOKEN` protège `/mcp` (tous les outils), `PDF_UPLOAD_TOKEN`
protège UNIQUEMENT `POST /pdfs`. Raison : `PDF_UPLOAD_TOKEN` doit apparaître
en clair dans les instructions d'un agent Dust (aucun mécanisme de secret
injectable trouvé côté Dust au moment d'écrire ceci — texte visible par
quiconque édite l'agent), donc sa fuite est assumée comme éventualité
réaliste. Un jeton à portée réduite limite les dégâts d'une fuite à
« peut téléverser des PDF », jamais à « peut appeler assembler_pptx /
exporter_pdf / tout le reste » — MCP_AUTH_TOKEN, lui, n'apparaît jamais
dans un texte destiné à être collé où que ce soit.
"""
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def _jeton_depuis_env(nom_variable: str) -> str:
    """Lit `nom_variable` depuis l'environnement. Lève RuntimeError si
    absent/vide : ce serveur est exposé publiquement (Dust), jamais d'accès
    libre par défaut sur aucune route protégée."""
    jeton = os.environ.get(nom_variable, "").strip()
    if not jeton:
        raise RuntimeError(
            f"Configuration invalide : la variable d'environnement "
            f"{nom_variable} est absente ou vide. Le serveur refuse de "
            f"démarrer sans jeton défini (il est exposé publiquement)."
        )
    return jeton


def jeton_attendu() -> str:
    """Lit MCP_AUTH_TOKEN (protège `/mcp`, jamais exposé en dehors d'une
    configuration d'outil Dust)."""
    return _jeton_depuis_env("MCP_AUTH_TOKEN")


def jeton_upload_attendu() -> str:
    """Lit PDF_UPLOAD_TOKEN (protège UNIQUEMENT `POST /pdfs`, cf. docstring
    de ce module — portée volontairement réduite car destiné à apparaître en
    clair dans les instructions d'un agent Dust)."""
    return _jeton_depuis_env("PDF_UPLOAD_TOKEN")


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Exige `Authorization: Bearer <jeton>` sur CHAQUE requête, sauf sous
    `prefixes_exemptes` (ex. mcp_server/fichiers.py : liens de téléchargement
    à capacité, destinés à être cliqués directement par l'utilisateur final
    dans son navigateur — leur sécurité vient du jeton imprévisible dans
    l'URL elle-même, pas de ce jeton Bearer serveur-à-serveur).

    Le jeton exigé dépend du chemin : `jetons_par_prefixe` mappe un préfixe
    à SON jeton (ex. `/pdfs` -> `PDF_UPLOAD_TOKEN`) ; tout chemin non
    exempté et ne matchant aucun préfixe de cette table utilise `jeton` (le
    jeton par défaut, `MCP_AUTH_TOKEN`). Chaque jeton attendu est fixé une
    fois à l'instanciation (pas relu à chaque requête) : modifier la
    variable d'environnement sans redémarrer le serveur n'a pas d'effet —
    comportement explicite plutôt qu'une bascule silencieuse en cours de
    service."""

    def __init__(
        self, app, jeton: str, prefixes_exemptes: tuple[str, ...] = (),
        jetons_par_prefixe: dict[str, str] | None = None,
    ):
        super().__init__(app)
        self._jeton = jeton
        self._prefixes_exemptes = prefixes_exemptes
        self._jetons_par_prefixe = jetons_par_prefixe or {}

    def _jeton_attendu_pour(self, path: str) -> str:
        for prefixe, jeton in self._jetons_par_prefixe.items():
            if path.startswith(prefixe):
                return jeton
        return self._jeton

    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in self._prefixes_exemptes):
            return await call_next(request)

        entete = request.headers.get("authorization", "")
        if not entete.startswith("Bearer "):
            return JSONResponse({"error": "en-tête Authorization Bearer manquant"}, status_code=401)
        fourni = entete[len("Bearer "):]
        attendu = self._jeton_attendu_pour(request.url.path)
        if not secrets.compare_digest(fourni.encode("utf-8"), attendu.encode("utf-8")):
            return JSONResponse({"error": "jeton invalide"}, status_code=401)
        return await call_next(request)
