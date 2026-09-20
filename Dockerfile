# Hosted mode image (PRD sections 7.3 and 11).
#
# The GalaxyGate guide's build step takes a repo URL, a subdirectory and a
# tag, and runs a Docker build there. This is that build for Vault, in place
# of the guide's demos/tts demo app.
#
#   docker build -t 127.0.0.1:5000/hackathon:base .
#   docker run -p 8000:8000 -v /data/vault:/data 127.0.0.1:5000/hackathon:base

FROM python:3.11-slim

# Keep the image quiet and unbuffered so `get_app_logs` shows output as it
# happens rather than when a buffer fills.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements first: this layer is cached across rebuilds that only touch
# source, which matters when the build runs on the server itself.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps -e .

# The container's writable state lives on the mounted volume, never in the
# image layer -- that is what makes a redeploy keep the memory. The guide
# mounts the host's /data/<app> at /data.
ENV VAULT_HOME=/data/vault \
    VAULT_PROJECT=/data/vault-project \
    VAULT_PROVIDER=runpod \
    VAULT_BUDGET=700
VOLUME ["/data"]

# Run as a non-root user, and give it the volume so SQLite can write.
RUN useradd --create-home --uid 10001 vault \
    && mkdir -p /data/vault /data/vault-project \
    && chown -R vault:vault /data /app
USER vault

EXPOSE 8000

# The guide maps host 8000 to container 8000 and fronts it with HTTPS, so
# bind all interfaces on 8000 and let the proxy terminate TLS.
CMD ["uvicorn", "vault.web:app", "--host", "0.0.0.0", "--port", "8000"]
