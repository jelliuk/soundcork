FROM python:3.12-slim-trixie

# ── Build arguments ────────────────────────────────────────────────────────
# PUID/PGID allow the container user to be aligned with the host user that
# owns the mounted data directory, avoiding permission errors on volume mounts.
# Override at build time:  docker build --build-arg PUID=1001 --build-arg PGID=1001
# Or set at runtime via docker-compose environment variables (see entrypoint.sh).
ARG PUID=1000
ARG PGID=1000

# ── Environment ────────────────────────────────────────────────────────────
# The app imports "from soundcork.bmx import ..." so /app must be on PYTHONPATH.
# The app reads bmx_services.json, swupdate.xml, and media/ from CWD.
ENV PYTHONPATH=/app \
    # Prevent Python from writing .pyc files into the image layers
    PYTHONDONTWRITEBYTECODE=1 \
    # Ensure Python output is sent straight to the container log
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── System dependencies ────────────────────────────────────────────────────
# gosu: privilege-drop helper used by entrypoint.sh for runtime PUID/PGID
# remapping. Installed in a single layer and apt cache cleared immediately.
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu openssh-client && \
    rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────────────
# Copied and installed before application code so this layer is only
# invalidated when requirements.txt changes, not on every code change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Application code ───────────────────────────────────────────────────────
COPY soundcork/ soundcork/

# ── Entrypoint ─────────────────────────────────────────────────────────────
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ── User and directory setup ───────────────────────────────────────────────
# Create the group and user using the build-arg values. The data and log
# directories are created here and ownership assigned so they are writable
# by the runtime user regardless of which UID/GID is supplied at runtime.
RUN groupadd --gid ${PGID} appgroup && \
    useradd --uid ${PUID} --gid ${PGID} --no-create-home --shell /sbin/nologin appuser && \
    mkdir -p /soundcork/data /soundcork/logs && \
    chown -R appuser:appgroup /soundcork

# ── Volumes ────────────────────────────────────────────────────────────────
# data: persistent speaker account and device data (set data_dir=/soundcork/data)
# logs: traffic logs (used in proxy mode via soundcork_log_dir=/soundcork/logs)
VOLUME ["/soundcork/data", "/soundcork/logs"]

# ── Ports ──────────────────────────────────────────────────────────────────
# 8000: Gunicorn / FastAPI application
EXPOSE 8000

WORKDIR /app/soundcork

ENTRYPOINT ["/entrypoint.sh"]

# Gunicorn with uvicorn workers, bound to all interfaces.
# Access and error logs redirected to stdout/stderr for container log drivers.
CMD ["gunicorn", "-c", "gunicorn_conf.py", "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "--workers", "1", \
     "main:app"]
