# -*- coding: utf-8 -*-
"""
mcp_server/auth.py — vérification du jeton Bearer (MCP_AUTH_TOKEN) sur
chaque requête HTTP. Même durcissement que app.py::_authentifie (LOT E,
app Streamlit) : le serveur refuse de démarrer sans jeton défini, et la
comparaison se fait sur des octets UTF-8 (secrets.compare_digest refuse les
`str` non-ASCII — un jeton accentué planterait sinon).
"""
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def jeton_attendu() -> str:
    """Lit MCP_AUTH_TOKEN depuis l'environnement. Lève RuntimeError si
    absent/vide : ce serveur est exposé publiquement (Dust), jamais d'accès
    libre par défaut."""
    jeton = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if not jeton:
        raise RuntimeError(
            "Configuration invalide : la variable d'environnement "
            "MCP_AUTH_TOKEN est absente ou vide. Le serveur refuse de "
            "démarrer sans jeton défini (il est exposé publiquement)."
        )
    return jeton


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Exige `Authorization: Bearer <jeton>` sur CHAQUE requête, sauf sous
    `prefixes_exemptes` (ex. mcp_server/fichiers.py : liens de téléchargement
    à capacité, destinés à être cliqués directement par l'utilisateur final
    dans son navigateur — leur sécurité vient du jeton imprévisible dans
    l'URL elle-même, pas de ce jeton Bearer serveur-à-serveur). Le jeton
    Bearer attendu est fixé une fois à l'instanciation (pas relu à chaque
    requête) : modifier la variable d'environnement sans redémarrer le
    serveur n'a pas d'effet — comportement explicite plutôt qu'une bascule
    silencieuse en cours de service."""

    def __init__(self, app, jeton: str, prefixes_exemptes: tuple[str, ...] = ()):
        super().__init__(app)
        self._jeton = jeton
        self._prefixes_exemptes = prefixes_exemptes

    async def dispatch(self, request: Request, call_next):
        if any(request.url.path.startswith(p) for p in self._prefixes_exemptes):
            return await call_next(request)

        entete = request.headers.get("authorization", "")
        if not entete.startswith("Bearer "):
            return JSONResponse({"error": "en-tête Authorization Bearer manquant"}, status_code=401)
        fourni = entete[len("Bearer "):]
        if not secrets.compare_digest(fourni.encode("utf-8"), self._jeton.encode("utf-8")):
            return JSONResponse({"error": "jeton invalide"}, status_code=401)
        return await call_next(request)
