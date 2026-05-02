# Multi-stage Dockerfile for Cloud Run.
# Stage 1: build wheels for the Python deps (needs build-essential + dev headers).
# Stage 2: slim runtime with only the shared libraries we load at runtime
# (psycopg, WeasyPrint cairo/pango, pikepdf qpdf, Pillow).

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libffi-dev \
        libcairo2-dev \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libqpdf-dev \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip wheel --wheel-dir=/wheels .


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app

# Runtime libs only — no compilers.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libcairo2 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libqpdf28 \
        libjpeg62-turbo \
        zlib1g \
        fonts-dejavu-core \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN pip install --upgrade pip && pip install --no-index --find-links=/wheels sands-sorter \
    && rm -rf /wheels

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/cloud-run/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
