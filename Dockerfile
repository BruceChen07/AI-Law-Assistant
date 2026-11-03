# syntax=docker/dockerfile:1
# =============================================================================
# AI-Law-Assistant — production image
#
#   Build : docker build -t ai-law-assistant .
#   Run   : docker compose up -d
#
# Two stages: the React frontend is built with Node, then copied into a slim
# Python runtime. Model weights (~10 GB) are NOT baked in — mount ./models or
# let the container fetch them at first start (see the deployment checklist).
# =============================================================================

# ---------------------------------------------------------------- stage: web
FROM node:22-slim AS web
WORKDIR /build
# Copy manifests first so `npm ci` is cached independently of source changes.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ------------------------------------------------------------ stage: runtime
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_CONFIG=/app/app/config.json \
    APP_PORT=8000 \
    APP_PORT_AUTO_SWITCH=0

# System libraries required by the Python stack:
#   tesseract-ocr + chi_sim/eng traineddata : OCR. cfg defaults ocr_languages to
#                                             "chi_sim+eng", so the English-only
#                                             tesseract-ocr package is not enough.
#   poppler-utils                           : pdftotext / pdfinfo (PDF text layer)
#   libgl1, libglib2.0-0, libsm6, libxext6,
#   libxrender1                             : OpenCV runtime, pulled in by
#                                             mineru / doclayout-yolo
#   libgomp1                                : torch OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-eng \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# BOTH trees are required:
#   * app/  is the backend package
#   * bin/  is imported at startup -- app/app_factory.py does
#           `from bin.ensure_local_models import ensure_models`, and app/main.py
#           puts the repo root on sys.path so bin/ must sit beside app/.
# Copying only app/ makes the container die with ModuleNotFoundError: 'bin'.
COPY app/ /app/app/
COPY bin/ /app/bin/

# requirements.txt is the pip-tools-generated runtime lock file.
RUN pip install --no-cache-dir -r /app/app/requirements.txt

# Built frontend. FastAPI serves it from /static; app/config.json sets
# static_dir = "../web/dist", which resolves to /app/web/dist from /app/app.
COPY --from=web /build/dist /app/web/dist

# Writable locations (also the docker-compose volume targets).
RUN mkdir -p /app/data /app/logs /app/models

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"

CMD ["python", "-m", "app.main"]
