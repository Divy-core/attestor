# Multi-stage, non-root, PORT from environment.
#
# Built by Cloud Build via `gcloud run deploy --source .` -- never by a local
# `docker build`. That is a deliberate decision: local Docker on Windows needs Docker
# Desktop, a running daemon, and a WSL2 backend, and it produces a build environment
# that differs from the deploy target. This file exists so Cloud Build has something to
# consume and so the build is reproducible and readable, not so anyone runs it by hand.

# ---- builder -------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Only what the service needs at runtime. Pinned so a rebuild months from now
# produces the same image.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir \
        "fastapi==0.121.2" \
        "uvicorn[standard]==0.52.3" \
        "google-cloud-firestore==2.28.1"

# ---- runtime -------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080

# Non-root. Cloud Run does not require it, but running as root in a container that
# handles untrusted vendor documents is an unforced error.
RUN groupadd --system --gid 1001 attestor \
 && useradd --system --uid 1001 --gid attestor --no-create-home attestor

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=attestor:attestor src/ /app/src/

USER attestor

EXPOSE 8080

# PORT is supplied by Cloud Run at runtime; the default above covers local use.
CMD exec uvicorn control_plane.main:app \
        --host 0.0.0.0 \
        --port "${PORT}" \
        --app-dir /app/src \
        --workers 1
