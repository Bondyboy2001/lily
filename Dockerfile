# syntax=docker/dockerfile:1

# Requires BuildKit (docker buildx). Examples:
#   docker buildx build --tag lily:dev --build-arg VERSION=2.1.0-test .
#   docker buildx build --push --platform linux/amd64,linux/arm64 \
#     --build-arg BUILD_DATE="$(date '+%d-%m-%Y %H:%M')" --build-arg VERSION=2.1.0 --tag lily:latest .

# ==========================================================================
# Pinned third-party inputs
# ==========================================================================
#
# Every download is verified against a SHA-256 below, and base images are pinned
# by digest. To bump a version, change the version ARG AND the matching hash ARG(s)
# together, in this block only.
#
# Base images (tag kept for readability, digest is what is actually pulled).
# Bump: TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:linuxserver/<repo>:pull" | jq -r .token)
#       curl -sI -H "Authorization: Bearer $TOKEN" \
#         -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json" \
#         https://ghcr.io/v2/linuxserver/<repo>/manifests/<tag> | grep -i docker-content-digest
#   (or: docker buildx imagetools inspect ghcr.io/linuxserver/<repo>:<tag>)
ARG BASE_IMAGE=ghcr.io/linuxserver/baseimage-ubuntu:noble@sha256:e3c0ef35fa0beae613f5571236325b147c7fff647da0e7ccd5aa1af7c46ca093

# Calibre. Bump: SHA-512s are published at https://calibre-ebook.com/signatures/calibre-<ver>-<arch>.txz.sha512
# (cross-check the download against those), then record: sha256sum calibre-<ver>-{x86_64,arm64}.txz
ARG CALIBRE_RELEASE=9.1.0
ARG CALIBRE_SHA256_X86_64=93a2d3104933366a50b03f8a2ff3889dc16b9c1d739ad22055e95ea35b03c1df
ARG CALIBRE_SHA256_ARM64=ca1261b71030390d6316fe6e3f722247f05670eec845bcefb62d8c0b1d1478cb

# deadsnakes PPA signing key (Python 3.13). The key is fetched at build time and must match
# this fingerprint: https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa
ARG DEADSNAKES_KEY=F23C5A6CF475977595C89F51BA6932366A755776

# lsof release tarball. Bump: GitHub publishes the digest on the release asset:
# curl -s https://api.github.com/repos/lsof-org/lsof/releases/tags/<ver> | jq -r '.assets[] | select(.name|endswith(".tar.gz")) | .digest'
ARG LSOF_VERSION=4.99.5
ARG LSOF_SHA256=4682c2491ec8b3d62f84e135afc1d9ead1bad5f034b50716f0c3826a4ee7d229

# The build is split into small stages so BuildKit can run them in parallel
# (Calibre download, lsof compile and pip install all overlap), and
# apt / pip use cache mounts so a cold rebuild doesn't re-download everything.

# --------------------------------------------------------------------------
# runtime-base: deadsnakes Python 3.13 + every runtime apt package.
# Both the Python build stage and the final image start from here, so the
# PPA is added and the runtime packages are installed only once.
# --------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS runtime-base

ARG DEADSNAKES_KEY

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Keep downloaded .debs so the apt cache mount below is actually reused
RUN rm -f /etc/apt/apt.conf.d/docker-clean

RUN \
  --mount=type=cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
  echo "**** add deadsnakes PPA for Python 3.13 ****" && \
  # Written directly rather than with add-apt-repository, which would install
  # software-properties-common (and a second Python) just to add one source
  curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x${DEADSNAKES_KEY}" -o /tmp/deadsnakes.asc && \
  gpg --show-keys --with-colons /tmp/deadsnakes.asc | grep -q "^fpr:::::::::${DEADSNAKES_KEY}:" && \
  gpg --dearmor -o /etc/apt/keyrings/deadsnakes.gpg /tmp/deadsnakes.asc && \
  echo "deb [signed-by=/etc/apt/keyrings/deadsnakes.gpg] https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu noble main" \
    > /etc/apt/sources.list.d/deadsnakes.list && \
  apt-get update && \
  echo "**** install runtime packages ****" && \
  apt-get install -y --no-install-recommends \
  imagemagick \
  ghostscript \
  qpdf \
  libmagic1 \
  libxi6 \
  libxslt1.1 \
  xdg-utils \
  inotify-tools \
  python3.13 \
  sqlite3 \
  libasound2t64 \
  libxtst6 \
  libxrandr2 \
  libxkbfile1 \
  libxcomposite1 \
  libxcursor1 \
  libxfixes3 \
  libxrender1 \
  libopengl0 \
  libnss3 \
  libxkbcommon0 \
  libegl1 \
  libxdamage1 \
  libgl1 \
  libglx-mesa0 \
  curl && \
  # Create python3 symlink to point to python3.13
  ln -sf /usr/bin/python3.13 /usr/bin/python3 && \
  rm -rf /tmp/* /var/tmp/* /root/.cache

# --------------------------------------------------------------------------
# build-base: compilers and headers, used by the pip and lsof stages only
# --------------------------------------------------------------------------
FROM runtime-base AS build-base

RUN \
  --mount=type=cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
  apt-get update && \
  apt-get install -y --no-install-recommends \
  build-essential \
  python3.13-dev \
  python3.13-venv

# --------------------------------------------------------------------------
# python-deps: the /lsiopy virtualenv
# --------------------------------------------------------------------------
FROM build-base AS python-deps

# Copy only requirements files so code changes don't invalidate the pip layer.
# requirements.txt holds the allowed ranges; requirements.lock pins every package
# (transitive deps included) so rebuilding the same commit gives the same image.
COPY requirements.txt requirements.lock /tmp/requirements/

# Packages come from linuxserver's Ubuntu wheel index first: precompiled wheels for
# the popular C/C++ packages on x86_64, armv7l and aarch64 (https://realpython.com/python-wheels/).
# The cache mount keeps downloaded wheels between builds, so only changed pins are fetched.
# pip and wheel are only needed here, so they are removed before /lsiopy is copied out.
RUN \
  --mount=type=cache,target=/root/.cache/pip \
  python3.13 -m venv /lsiopy && \
  /lsiopy/bin/pip install -U pip wheel && \
  /lsiopy/bin/pip install -U --find-links https://wheel-index.linuxserver.io/ubuntu/ \
  -r /tmp/requirements/requirements.txt \
  -c /tmp/requirements/requirements.lock && \
  /lsiopy/bin/python -m pip uninstall -y pip wheel

# --------------------------------------------------------------------------
# lsof: built from source to fix the hanging issue with 4.95 (issue #654)
# --------------------------------------------------------------------------
FROM build-base AS lsof

ARG LSOF_VERSION
ARG LSOF_SHA256

RUN \
  echo "**** install lsof ${LSOF_VERSION} from source ****" && \
  curl -fsSL "https://github.com/lsof-org/lsof/releases/download/${LSOF_VERSION}/lsof-${LSOF_VERSION}.tar.gz" -o /tmp/lsof.tar.gz && \
  echo "${LSOF_SHA256}  /tmp/lsof.tar.gz" | sha256sum -c - && \
  cd /tmp && \
  tar -xzf lsof.tar.gz && \
  cd "lsof-${LSOF_VERSION}" && \
  # Release tarballs use autotools. Build only the static binary (the full "make"
  # also renders the man page, which needs soelim/groff).
  ./configure --disable-shared && \
  make lsof && \
  install -m 755 lsof /usr/bin/lsof

# --------------------------------------------------------------------------
# calibre: download and unpack into /app/calibre
# --------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS calibre

ARG CALIBRE_RELEASE
ARG CALIBRE_SHA256_X86_64
ARG CALIBRE_SHA256_ARM64

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN rm -f /etc/apt/apt.conf.d/docker-clean

RUN \
  --mount=type=cache,target=/var/cache/apt,sharing=locked \
  --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
  apt-get update && \
  apt-get install -y --no-install-recommends xz-utils && \
  mkdir -p /app/calibre && \
  if [ "$(uname -m)" == "x86_64" ]; then \
  CALIBRE_ARCH="x86_64"; CALIBRE_SHA256="${CALIBRE_SHA256_X86_64}"; \
  elif [ "$(uname -m)" == "aarch64" ]; then \
  CALIBRE_ARCH="arm64"; CALIBRE_SHA256="${CALIBRE_SHA256_ARM64}"; \
  else \
  echo "Unsupported architecture: $(uname -m)" >&2; exit 1; \
  fi && \
  curl -fsSL -o /tmp/calibre.txz \
  "https://download.calibre-ebook.com/${CALIBRE_RELEASE}/calibre-${CALIBRE_RELEASE}-${CALIBRE_ARCH}.txz" && \
  echo "${CALIBRE_SHA256}  /tmp/calibre.txz" | sha256sum -c - && \
  tar xf /tmp/calibre.txz -C /app/calibre && \
  rm /tmp/calibre.txz
# (No libQt6* ABI-tag strip: it broke Calibre's Qt6 features and was dropped in V3.1.4;
#  the cwa-init service does a kernel check instead.)

# --------------------------------------------------------------------------
# app: the application as it lands on / (code, bytecode, s6 services, versions),
# laid out under /out. Built on runtime-base for the same Python 3.13 and abc user,
# without calibre or the pip packages; application code changes most often.
# --------------------------------------------------------------------------
FROM runtime-base AS app

ARG VERSION
ARG CALIBRE_RELEASE

# /out becomes / and /out/app becomes /app, so they are made root's before the copy
# (whose --chown would otherwise give abc the parent directories it creates)
RUN mkdir -p /out/app
COPY --chown=abc:abc . /out/app/calibre-web-automated/

WORKDIR /out/app/calibre-web-automated

RUN \
  # s6 services, writable dirs, permissions and CLI aliases
  bash scripts/setup-cwa.sh /out && \
  # The install stays owned by the build-time abc, so when PUID differs abc can't
  # write bytecode caches next to the code; compile them here instead
  python3 -m compileall -q -j 0 cps scripts cps.py && \
  # Versions shown on the About/Admin pages and read by the init scripts
  echo "$VERSION" > /out/app/CWA_RELEASE && \
  echo "$CALIBRE_RELEASE" > /out/CALIBRE_RELEASE

# ============================================================================
# Final runtime image
# ============================================================================
FROM runtime-base

# Set first: after the --link copies below it would make BuildKit unpack the whole image
# on every build (~7 s in CI) just to check that /config exists
WORKDIR /config

# --link copies are independent layers: they don't get redone when an earlier layer changes
COPY --link --from=calibre /app/calibre /app/calibre

# Calibre's installer links its tools (calibredb, ebook-convert...) into /usr/bin and
# adds shell completions. Done here, the calibre-binaries-setup service finds them and
# skips it, rather than spending over a minute on it in every new container. It must
# stay above the ARGs: BUILD_DATE changes on every build and would rebuild it. Its
# desktop-menu step fails without a desktop and only warns.
RUN /app/calibre/calibre_postinstall > /dev/null && \
  calibredb --version

ARG BUILD_DATE
ARG VERSION

LABEL build_version="Version:- ${VERSION}" \
  build_date="${BUILD_DATE}" \
  maintainer="Bondyboy2001"

COPY --link --from=lsof /usr/bin/lsof /usr/bin/lsof
# Python 3.13 itself comes from the deadsnakes package in runtime-base; /lsiopy's venv links to it
COPY --link --from=python-deps /lsiopy /lsiopy

# The app layer is assembled in the app stage above and lands in one --link copy, so
# building it never needs this stage's calibre and Python layers: with the layer cache
# in a registry, a code-only build no longer downloads ~500 MB just to run one step.
COPY --link --from=app /out/ /

ENV CALIBRE_CONFIG_DIR=/config/.config/calibre
ENV S6_LOGGING=1
ENV S6_CATCHALL_USER=abc
# The default port Lily listens on. Can be overridden with the CWA_PORT_OVERRIDE environment variable.
EXPOSE 8083
VOLUME ["/config", "/cwa-book-ingest", "/calibre-library"]

# Health check for container orchestration
# Uses shell form to support environment variable substitution for CWA_PORT_OVERRIDE
# /health returns 503 when metadata.db can't be read or the ingest / metadata services are down,
# so a broken library marks the container unhealthy; curl's own time limits keep a wedged app
# from leaving probes hanging until Docker's timeout. During the start period it probes every
# 2 s (Docker 25+; older engines ignore it), so after an update the NAS shows healthy as soon
# as the app answers rather than up to 30 s later.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --start-interval=2s --retries=3 \
  CMD curl -fs --connect-timeout 2 --max-time 4 http://localhost:${CWA_PORT_OVERRIDE:-8083}/health || curl -fs -k --connect-timeout 2 --max-time 4 https://localhost:${CWA_PORT_OVERRIDE:-8083}/health || exit 1
