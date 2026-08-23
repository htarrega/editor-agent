# syntax=docker/dockerfile:1

# One image, one origin: the same process serves the HTML front (templates/,
# static/) and the JSON API, so the browser never talks to a second host and
# neither side needs CORS. One stage is enough for that now the front is
# server-rendered rather than a separate build artefact to produce and copy
# in — `templates/` and `static/` are just more files this image ships,
# the same as `api/` and `corrector/` already were.

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
#
# `.[drive]` and not `.`, at +168 MB on a 329 MB image. The front in here ships
# a Google Docs tab, and a tab that answers 501 is a worse thing to hand someone
# than a larger download. Without the extra, mounting the token would not be
# enough either — the image would have to be rebuilt to use a feature its own UI
# offers.
COPY pyproject.toml ./
COPY corrector/ ./corrector/
COPY evals/ ./evals/
RUN pip install ".[drive]"

# `api/` is not an installed package — it is the entry point, and uvicorn finds
# it because the working directory is on the path. `templates/` and `static/`
# are what `api/web.py` renders and serves; there is no build step left to run
# over them first.
COPY api/ ./api/
COPY templates/ ./templates/
COPY static/ ./static/

# Nothing in here needs to write, and the endpoint is unauthenticated.
RUN useradd --create-home --uid 10001 amanuense
USER amanuense

EXPOSE 8000

# `/api/health` answers without building a corrector or reaching a provider,
# so polling it costs nothing. urllib rather than curl: the slim image has no
# curl and this saves installing one.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
