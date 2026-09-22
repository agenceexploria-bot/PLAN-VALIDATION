# syntax=docker/dockerfile:1
# Image du serveur MCP « Plan de Validation Vertical » — service DISTINCT de
# l'app Streamlit (Dockerfile à la racine) : même pipeline (core/), même
# approche LibreOffice headless, mais processus, port et jeton d'accès
# différents. Se construit depuis la RACINE du dépôt (contexte de build =
# racine, pas mcp_server/) pour pouvoir copier core/ :
#   docker build -f mcp_server/Dockerfile -t plan-validation-mcp .
FROM python:3.11-slim

# LibreOffice headless — SEUL moteur de rendu ici (jamais PowerPoint/COM,
# indisponible sur Linux et de toute façon exclu par les contraintes du
# serveur MCP — cf. core/render.py, bascule automatique). `fonts-liberation`
# (métriquement compatibles Arial/Times/Courier) pour un rendu fidèle, cf.
# Dockerfile racine (même correctif LOT E, même raison).
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libreoffice-core \
        libreoffice-impress \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code du pipeline (core/), gabarits PPTX (templates/), glossaire (data/) et
# code du serveur MCP lui-même — jamais app.py (app Streamlit, non utilisée
# ici) ni tests/.
COPY core/ core/
COPY data/ data/
COPY templates/ templates/
COPY mcp_server/ mcp_server/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

# MCP_AUTH_TOKEN et MCP_PUBLIC_BASE_URL sont OBLIGATOIRES (le serveur refuse
# de démarrer sans jeton défini, cf. mcp_server/auth.py ; sans URL publique
# configurée, les liens de téléchargement pointent vers 127.0.0.1 et sont
# inutilisables par Dust/l'utilisateur final) — à définir dans l'environnement
# du service (cf. mcp_server/README.md), jamais dans l'image.
CMD ["python", "-m", "mcp_server.server"]
