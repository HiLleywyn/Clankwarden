# Clanksimus Prime -- single-image deploy (Railway-ready).
#
# Build args:
#   FRAMEWORK_REF  git ref of hilleywyn/framework to install (default: main)
#   GITHUB_TOKEN   token with read access to the private framework repo
#
# Example:
#   docker build --build-arg GITHUB_TOKEN=ghp_xxx \
#                --build-arg FRAMEWORK_REF=main -t clanksimus .
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PREFIX=. \
    API_PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 1. Install the shared framework from its (private) repo. The token is only
#    used at build time and is never baked into the final layers' env.
ARG FRAMEWORK_REF=main
ARG GITHUB_TOKEN=
RUN pip install "bot-framework @ git+https://${GITHUB_TOKEN:+${GITHUB_TOKEN}@}github.com/hilleywyn/framework.git@${FRAMEWORK_REF}"

# 2. App-level dependencies.
COPY requirements.txt .
RUN pip install -r requirements.txt

# 3. Application source.
COPY . .

# 4. Fail the build if the test suite is red.
RUN pip install pytest pytest-asyncio \
    && python -m pytest -q tests/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf "http://localhost:${API_PORT:-8080}/health" || exit 1

CMD ["python", "main.py"]
