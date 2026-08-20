# Multi-stage: one base layer, one target per service, so an image contains only what
# that service needs and a change to the dashboard does not rebuild the worker.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AFTERCARE_MODE=cloud

WORKDIR /app

# Dependencies first, so the layer cache survives a source change.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[cloud]"

COPY packages/ ./packages/
COPY services/ ./services/
COPY scripts/ ./scripts/

# Runs as a non-root user. Cloud Run does not require it; a security reviewer does.
RUN useradd --create-home --uid 1001 aftercare && chown -R aftercare:aftercare /app
USER aftercare

# --- services ------------------------------------------------------------------------

FROM base AS api
EXPOSE 8000
CMD ["python", "-m", "services.api.main"]

FROM base AS orchestrator
EXPOSE 8000
CMD ["python", "-m", "services.orchestrator.root"]

FROM base AS inbox
EXPOSE 8000
CMD ["python", "-m", "services.inbox.handler"]

FROM base AS worker
EXPOSE 8000
CMD ["python", "-m", "services.worker.agent"]

# --- jobs ----------------------------------------------------------------------------
# Discovery is a Cloud Run Job, not a service: it runs once per estate and exits. It also
# carries the demo corpus, because a fresh deployment with nothing to discover is a
# deployment nobody can evaluate.

FROM base AS discovery
COPY demo/ ./demo/
CMD ["python", "-m", "services.discovery.job"]
