# [1.1] #1. Same shape as the auth service image.
#
# **The build context is `backend/`, not this directory.** A context cannot
# reach outside itself, which is exactly why `backend/shared/` could not exist
# before [F-6] #76 — ADR 0005 named this the binding constraint on extracting
# it. docker-compose.yml therefore sets `context: ./backend` and points
# `dockerfile` at this file, and every COPY below is written relative to
# `backend/`.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The image mirrors the repository layout: the service directory is the working
# directory and `shared/` sits beside it, so `/app` on the path makes
# `import shared` resolve exactly as it does from a checkout. pytest.ini says
# the same thing with `pythonpath = . ..`.
ENV PYTHONPATH=/app

WORKDIR /app/market_service

# Dependencies first so a source-only change reuses the cached layer.
COPY market_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared next, service source last: `shared/` changes far less often, so an
# ordinary edit to this service reuses this layer as well as the wheel cache.
COPY shared/ /app/shared/
COPY market_service/ ./

# Non-root, so a container escape does not land on uid 0.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
