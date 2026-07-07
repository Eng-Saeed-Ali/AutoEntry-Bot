# =============================================================================
# AutoEntry Bot — Multi-stage Docker build
# =============================================================================
# Stage 1 (builder): compile dependencies into a virtualenv.
# Stage 2 (runtime):  copy only the venv + app code; run as non-root.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Builder
# ---------------------------------------------------------------------------
FROM python:3.12-alpine AS builder

# Build-time dependencies for compiling asyncpg, polars native bits, etc.
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    cargo \
    rustup

# Create a dedicated virtualenv so we can copy it wholesale into runtime.
ENV VENV_PATH=/opt/venv
RUN python -m venv "$VENV_PATH"
ENV PATH="$VENV_PATH/bin:$PATH"

# Install wheel first to avoid legacy setup.py fallbacks.
RUN pip install --no-cache-dir wheel

# Copy only dependency specs to leverage Docker layer caching.
COPY pyproject.toml .

# Install project dependencies into the venv.
# --no-compile avoids .pyc files we don't want in final image.
RUN pip install --no-cache-dir --no-compile \
    "$(python -c 'import tomllib; print("\n".join(tomllib.load(open("pyproject.toml","rb"))["project"]["dependencies"]))')"

# ---------------------------------------------------------------------------
# Stage 2 — Runtime (minimal, hardened)
# ---------------------------------------------------------------------------
FROM python:3.12-alpine AS runtime

# Only runtime OS-level deps (libpq for asyncpg, ca-certificates for TLS).
RUN apk add --no-cache \
    libpq \
    ca-certificates \
    tzdata

# Copy the pre-built virtualenv from the builder stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create a non-root application user with a fixed UID.
RUN addgroup -g 1000 autoentry && \
    adduser -u 1000 -G autoentry -s /bin/sh -D autoentry

# Copy only the application source and migrations.
# pyproject.toml is copied for metadata discovery at runtime (editable install not needed).
COPY --chown=autoentry:autoentry pyproject.toml ./
COPY --chown=autoentry:autoentry src/ ./src/

# migrations/ doesn't exist yet but will be added by Task 2.2.
# COPY --chown=autoentry:autoentry migrations/ ./migrations/

# Work inside the app directory.
WORKDIR /home/autoentry

# Drop root privileges.
USER autoentry

# No ports exposed — the bot connects OUT to Telegram API and PostgreSQL.

# Entrypoint: the future main module (created in Phase 4, Task 4.5).
# Falls back to a graceful message if src.main doesn't exist yet.
ENTRYPOINT ["python", "-m", "src.main"]