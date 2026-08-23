# syntax=docker/dockerfile:1

# One image, one origin: the front is built here and the API process serves it,
# so the browser never talks to a second host and neither side needs CORS. It
# is the same shape `web/vite.config.ts` fakes with a proxy in development, so
# the two situations cannot drift onto different assumptions.

# --- the front ---------------------------------------------------------------
FROM node:24-alpine AS web

WORKDIR /web

# The lockfile alone first, so that changing a component does not reinstall
# node_modules on every build.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build


# --- the API, and the build it serves ----------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# The pipeline is installed rather than copied onto the path: that is what makes
# `corrector` importable from anywhere, and it pins the provider SDKs to the
# versions in pyproject.toml — the one thing here that costs money when it
# misbehaves. `evals/` comes along because it is a declared package; nothing in
# the image runs it.
COPY pyproject.toml ./
COPY corrector/ ./corrector/
COPY evals/ ./evals/
RUN pip install .

# `api/` is not an installed package — it is the entry point, and uvicorn finds
# it because the working directory is on the path.
COPY api/ ./api/
COPY --from=web /web/dist ./web/dist

ENV EDITOR_AGENT_WEB_DIST=/app/web/dist

# Nothing in here needs to write, and the endpoint is unauthenticated.
RUN useradd --create-home --uid 10001 amanuense
USER amanuense

EXPOSE 8000

# `/health` answers without building a corrector or reaching a provider, so
# polling it costs nothing. urllib rather than curl: the slim image has no curl
# and this saves installing one.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
