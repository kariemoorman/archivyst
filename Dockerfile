# ---------------------------------------------------------------------------
# Archivyst — Vectorless RAG for Obsidian Vaults
# ---------------------------------------------------------------------------
# Multi-stage build: build → prod (hardened) → dev (test tools)
# ---------------------------------------------------------------------------

# ===========================================================================
# Stage 1: Build — install all dependencies
# ===========================================================================
FROM python:3.14-slim@sha256:bc389f7dfcb21413e72a28f491985326994795e34d2b86c8ae2f417b4e7818aa AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY README.md* ./
COPY src/ src/

RUN pip install --no-cache-dir ".[code]"


# ===========================================================================
# Stage 2: Prod — minimal hardened runtime
# ===========================================================================
FROM python:3.14-slim@sha256:bc389f7dfcb21413e72a28f491985326994795e34d2b86c8ae2f417b4e7818aa AS prod

LABEL maintainer="archivyst"
LABEL description="Vectorless, reasoning-based RAG for Obsidian vaults"

# Runtime-only system deps (no compilers, no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Copy only installed packages + entrypoints from build stage
COPY --from=build /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=build /usr/local/bin/archivyst /usr/local/bin/archivyst
COPY --from=build /usr/local/bin/archive /usr/local/bin/archive

# Copy application source (needed for editable install references)
WORKDIR /app
COPY src/ src/

# Create non-root user
RUN groupadd -r archivyst && useradd -r -g archivyst -m archivyst

# Create directories owned by non-root user
RUN mkdir -p /home/archivyst/.config/archivyst \
    /home/archivyst/obsidian-vaults/agent-memory \
    /home/archivyst/obsidian-vaults \
    && chown -R archivyst:archivyst /home/archivyst /app

USER archivyst
ENV HOME=/home/archivyst
ENV ARCHIVYST_CONFIG=/home/archivyst/.config/archivyst

# Remove pip from runtime (prevent package installation in prod)
# pip is in the build stage only
RUN python -m pip uninstall -y pip setuptools 2>/dev/null || true

ENTRYPOINT ["archivyst"]
CMD ["--help"]


# ===========================================================================
# Stage 3: Dev — testing and linting tools (runs as root)
# ===========================================================================
FROM build AS dev

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ".[dev,code]"

WORKDIR /app

COPY tests/ tests/
COPY Makefile .
COPY config.example.yaml .

ENTRYPOINT ["/bin/bash"]
CMD ["-c", "echo 'Archivyst dev container. Run: make test, make lint, make type'"]
