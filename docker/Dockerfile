# syntax=docker/dockerfile:1
# il2ks for Docker (doc 07 option A): `il2ks run` (web + watch + bundled Caddy) as one container, data in /data.
# Build from the repository root:   docker build -f docker/Dockerfile -t il2ks:local .
# Admin guide: docs/install-docker.md. Nothing here is pushed anywhere; the image is built on the admin's machine.
#
# Pinned on purpose: the Python base, uv, and Caddy (the official image's binary, tag and digest). Python dependencies
# come from uv.lock with hashes, so a build in a year installs what CI tested today.

ARG PYTHON_IMAGE=python:3.13-slim-bookworm
ARG UV_VERSION=0.12.22
# Caddy 2.10+ is needed for IP-address certificates (design doc 16). Bump tag and digest together.
ARG CADDY_IMAGE=caddy:2.11.6@sha256:907efba736324e43f891ccb9d760fe5abe545e313419b3d18d63d4ec670dad8d

FROM ${CADDY_IMAGE} AS caddy
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python \
    UV_NO_CACHE=1
WORKDIR /src
# Dependencies first: this layer is rebuilt only when the lock file changes.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
RUN uv export --frozen --no-dev --no-emit-project --no-header -o /tmp/requirements.txt \
 && uv venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --require-hashes -r /tmp/requirements.txt
# Then il2ks itself, as the wheel (what PyPI users get), not an editable checkout.
COPY src ./src
RUN uv build --wheel --out-dir /tmp/dist \
 && uv pip install --python /opt/venv/bin/python --no-deps /tmp/dist/*.whl

FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="il2ks" \
      org.opencontainers.image.description="Stats website for IL-2 Sturmovik: Korea dedicated servers" \
      org.opencontainers.image.source="https://github.com/FGlazov/il2_korea_stats" \
      org.opencontainers.image.licenses="MIT"

# Non-root. UID/GID 1000 is the usual first Linux user, which is also who usually owns a Wine prefix: matching it lets
# il2ks read (and, with after_archive = "move" or "delete", change) the game's log folder. Override with `user:`.
RUN groupadd --gid 1000 il2ks \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /data --shell /usr/sbin/nologin il2ks \
 && mkdir /data \
 && chown 1000:1000 /data

COPY --from=build /opt/venv /opt/venv
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# The official Caddy binary carries a file capability (cap_net_bind_service). compose.yaml drops ALL capabilities, and
# exec of a binary whose file capability is outside the bounding set fails with "operation not permitted". Copying the
# bytes with `cat` (not COPY, which keeps the xattr) gives a plain binary; low ports come from the net.ipv4 sysctl.
RUN --mount=type=bind,from=caddy,source=/usr/bin/caddy,target=/tmp/caddy \
    cat /tmp/caddy > /usr/local/bin/caddy \
 && chmod 0755 /usr/local/bin/caddy /usr/local/bin/docker-entrypoint.sh

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    IL2KS_DATA_DIR=/data

USER il2ks
WORKDIR /data
VOLUME /data
# 80 redirects to HTTPS and answers the certificate challenge; 443 is the site.
EXPOSE 80 443

# The web server answers on loopback (Django believes the proxy header, so ask as the proxy would). Caddy and the
# ingester are restarted by `il2ks run` itself when they crash; this check is about the web server, which is the
# one thing a visitor needs; `/healthz` also reads one row of the database. The long start period covers the first
# database migration.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request as u; u.urlopen(u.Request('http://127.0.0.1:' + os.environ.get('IL2KS_WEB_PORT', '8000') + '/healthz', headers={'X-Forwarded-Proto': 'https'}), timeout=8)"]

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["run"]
