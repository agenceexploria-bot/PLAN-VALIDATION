# syntax=docker/dockerfile:1
FROM python:3.11-slim

# LibreOffice headless — moteur de secours pour le rendu de vérification et
# l'export PDF (core/render.py) quand PowerPoint/COM est indisponible (ex.
# Render, qui n'est pas Windows). Pas un paquet pip : installé ici via apt.
# `libreoffice-impress` (+ sa dépendance `libreoffice-core`, qui fournit le
# binaire `soffice`) suffit à la conversion headless PPTX -> PDF
# (`soffice --headless --convert-to pdf`) ; on évite volontairement le
# métapaquet `libreoffice` complet (Writer/Calc/Base/Draw non utilisés ici,
# image bien plus lourde). `--no-install-recommends` écarte en plus l'aide,
# l'intégration bureau et les paquets de langue, non nécessaires en conteneur.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libreoffice-core \
        libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dépendances Python d'abord (couche mise en cache par Docker tant que
# requirements.txt ne change pas — build plus rapide sur les itérations
# suivantes).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code de l'app + gabarits PPTX / photos de contacts / glossaire FR : tout
# est embarqué dans l'image, rien ne dépend d'un upload manuel après
# déploiement (le disque de Render, palier gratuit, est éphémère — cf.
# README, section Déploiement sur Render).
COPY app.py .
COPY core/ core/
COPY data/ data/
COPY templates/ templates/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8501

EXPOSE 8501

# Render fournit le port réel via la variable d'environnement PORT au
# démarrage du conteneur (forme shell du CMD pour que $PORT soit résolu à
# l'exécution, pas au build).
CMD streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true
