# syntax=docker/dockerfile:1

# Simple Example Build Command:
# docker build \
# --tag lily:dev \
# --build-arg="BUILD_DATE=27-09-2024 12:06" \
# --build-arg="VERSION=2.1.0-test-5" .

# Good guide on how to set up a buildx builder here:
# https://a-berahman.medium.com/simplifying-docker-multiplatform-builds-with-buildx-3d7efd670f58

# Multi-Platform Example Build & Push Command:
# docker buildx build \
# --push \
# --platform linux/amd64,linux/arm64, \
# --build-arg="BUILD_DATE=02-08-2024 20:52" \
# --build-arg="VERSION=2.1.0" \
# --tag lily:latest .

# ==========================================================================
# STAGE 1: Dependencies - Install system packages and Python dependencies
# ==========================================================================
#
# Pinned third-party inputs. Every download is verified against a SHA-256 below,
# and base images are pinned by digest. To bump a version, change the version ARG
# AND the matching hash ARG(s) together, in this block only.
#
# Base images (tag kept for readability, digest is what is actually pulled).
# Bump: TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:linuxserver/<repo>:pull" | jq -r .token)
#       curl -sI -H "Authorization: Bearer $TOKEN" \
#         -H "Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json" \
#         https://ghcr.io/v2/linuxserver/<repo>/manifests/<tag> | grep -i docker-content-digest
#   (or: docker buildx imagetools inspect ghcr.io/linuxserver/<repo>:<tag>)
ARG BASE_IMAGE=ghcr.io/linuxserver/baseimage-ubuntu:noble@sha256:e3c0ef35fa0beae613f5571236325b147c7fff647da0e7ccd5aa1af7c46ca093
ARG UNRAR_IMAGE=ghcr.io/linuxserver/unrar:latest@sha256:48c0ce2609bb3c35764956bbd9395e95b177421e3c74b3628c3676029f24f2e0

# Calibre. Bump: SHA-512s are published at https://calibre-ebook.com/signatures/calibre-<ver>-<arch>.txz.sha512
# (cross-check the download against those), then record: sha256sum calibre-<ver>-{x86_64,arm64}.txz
ARG CALIBRE_RELEASE=9.1.0
ARG CALIBRE_SHA256_X86_64=93a2d3104933366a50b03f8a2ff3889dc16b9c1d739ad22055e95ea35b03c1df
ARG CALIBRE_SHA256_ARM64=ca1261b71030390d6316fe6e3f722247f05670eec845bcefb62d8c0b1d1478cb

# kepubify (upstream publishes no checksums). Bump: download
# https://github.com/pgaskin/kepubify/releases/download/<ver>/kepubify-linux-{64bit,arm64} and sha256sum them.
ARG KEPUBIFY_RELEASE=v4.0.4
ARG KEPUBIFY_SHA256_X86_64=37d7628d26c5c906f607f24b36f781f306075e7073a6fe7820a751bb60431fc5
ARG KEPUBIFY_SHA256_ARM64=5a15b8f6f6a96216c69330601bca29638cfee50f7bf48712795cff88ae2d03a3

# lsof release tarball. Bump: GitHub publishes the digest on the release asset:
# curl -s https://api.github.com/repos/lsof-org/lsof/releases/tags/<ver> | jq -r '.assets[] | select(.name|endswith(".tar.gz")) | .digest'
ARG LSOF_VERSION=4.99.5
ARG LSOF_SHA256=4682c2491ec8b3d62f84e135afc1d9ead1bad5f034b50716f0c3826a4ee7d229

FROM ${BASE_IMAGE} AS dependencies

ARG CALIBRE_RELEASE
ARG CALIBRE_SHA256_X86_64
ARG CALIBRE_SHA256_ARM64
ARG KEPUBIFY_RELEASE
ARG KEPUBIFY_SHA256_X86_64
ARG KEPUBIFY_SHA256_ARM64
ARG LSOF_VERSION
ARG LSOF_SHA256

# Set the default shell for the following RUN instructions to bash instead of sh
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# STEP 1 - Install Required Packages
RUN \
  # STEP 1.1 - Add deadsnakes PPA for Python 3.13 and install required apt packages
  echo "**** add deadsnakes PPA for Python 3.13 ****" && \
  apt-get update && \
  apt-get install -y --no-install-recommends software-properties-common && \
  add-apt-repository ppa:deadsnakes/ppa && \
  apt-get update && \
  echo "**** install build packages ****" && \
  apt-get install -y --no-install-recommends \
  build-essential \
  libldap2-dev \
  libsasl2-dev \
  gettext \
  python3.13-dev \
  python3.13-venv \
  curl && \
  echo "**** install runtime packages ****" && \
  apt-get install -y --no-install-recommends \
  imagemagick \
  ghostscript \
  libldap2 \
  libmagic1 \
  libsasl2-2 \
  libxi6 \
  libxslt1.1 \
  xdg-utils \
  inotify-tools \
  python3.13 \
  nano \
  sqlite3 \
  zip && \
  # STEP 1.2 - Install additional Calibre required packages
  apt-get install -y --no-install-recommends \
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
  xz-utils \
  binutils && \
  # Install lsof from source to fix hanging issue with 4.95 (issue #654)
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
  cp lsof /usr/bin/lsof && \
  chmod 755 /usr/bin/lsof && \
  cd / && \
  rm -rf /tmp/lsof* && \
  # Create python3 symlink to point to python3.13
  # (pip comes from ensurepip via python3.13-venv when the /lsiopy venv is created below;
  #  no system-wide pip / get-pip.py is needed)
  ln -sf /usr/bin/python3.13 /usr/bin/python3

# STEP 2 - Set up Python virtual environment
RUN \
  python3.13 -m venv /lsiopy && \
  /lsiopy/bin/pip install -U --no-cache-dir \
  pip \
  wheel

# STEP 3 - Copy requirements files and install Python packages
# Copy only requirements files first to leverage Docker layer caching
COPY --chown=abc:abc requirements.txt optional-requirements.txt /app/calibre-web-automated/

RUN \
  # STEP 3.1 - Installing the required python packages listed in 'requirements.txt' and 'optional-requirements.txt'
  # HOWEVER, they are not pulled from PyPi directly, they are pulled from linuxserver's Ubuntu Wheel Index
  # This is essentially a repository of precompiled some of the most popular packages with C/C++ source code
  # This provides the install maximum compatibility with multiple different architectures including: x86_64, armv71 and aarch64
  # You can read more about python wheels here: https://realpython.com/python-wheels/
  /lsiopy/bin/pip install -U --no-cache-dir --find-links https://wheel-index.linuxserver.io/ubuntu/ -r \
  /app/calibre-web-automated/requirements.txt -r /app/calibre-web-automated/optional-requirements.txt

# STEP 4 - Install kepubify
RUN \
  echo "**** install kepubify ${KEPUBIFY_RELEASE} ****" && \
  if [ "$(uname -m)" == "x86_64" ]; then \
  KEPUBIFY_ASSET="kepubify-linux-64bit"; KEPUBIFY_SHA256="${KEPUBIFY_SHA256_X86_64}"; \
  elif [ "$(uname -m)" == "aarch64" ]; then \
  KEPUBIFY_ASSET="kepubify-linux-arm64"; KEPUBIFY_SHA256="${KEPUBIFY_SHA256_ARM64}"; \
  else \
  echo "Unsupported architecture: $(uname -m)" >&2; exit 1; \
  fi && \
  curl -fsSL -o /usr/bin/kepubify \
  "https://github.com/pgaskin/kepubify/releases/download/${KEPUBIFY_RELEASE}/${KEPUBIFY_ASSET}" && \
  echo "${KEPUBIFY_SHA256}  /usr/bin/kepubify" | sha256sum -c - && \
  chmod +x /usr/bin/kepubify

# STEP 5 - Install Calibre
RUN \
  # STEP 5.1 - Make the /app/calibre directory for the installed files
  mkdir -p /app/calibre && \
  # STEP 5.2 - Download the desired version of Calibre, determined by the CALIBRE_RELEASE variable and the architecture of the build environment
  if [ "$(uname -m)" == "x86_64" ]; then \
  CALIBRE_ARCH="x86_64"; CALIBRE_SHA256="${CALIBRE_SHA256_X86_64}"; \
  elif [ "$(uname -m)" == "aarch64" ]; then \
  CALIBRE_ARCH="arm64"; CALIBRE_SHA256="${CALIBRE_SHA256_ARM64}"; \
  else \
  echo "Unsupported architecture: $(uname -m)" >&2; exit 1; \
  fi && \
  curl -fsSL -o /calibre.txz \
  "https://download.calibre-ebook.com/${CALIBRE_RELEASE}/calibre-${CALIBRE_RELEASE}-${CALIBRE_ARCH}.txz" && \
  echo "${CALIBRE_SHA256}  /calibre.txz" | sha256sum -c - && \
  # STEP 5.3 - Extract the downloaded file to /app/calibre
  tar xf \
  /calibre.txz -C \
  /app/calibre && \
  # STEP 5.3.1 - Remove the ABI tag from the extracted libQt6* files to allow them to be used on older kernels
  # Removed in V3.1.4 because it was breaking Calibre features that require Qt6. Replaced with a kernel check in the cwa-init service
  # STEP 5.4 - Delete the extracted calibre.txz to save space in final image
  rm /calibre.txz

# ============================================================================
# STAGE 2: Final - Build the final runtime image
# ============================================================================
FROM ${UNRAR_IMAGE} AS unrar

FROM ${BASE_IMAGE}

ARG BUILD_DATE
ARG VERSION
ARG CALIBRE_RELEASE
ARG KEPUBIFY_RELEASE

LABEL build_version="Version:- ${VERSION}"
LABEL build_date="${BUILD_DATE}"
LABEL maintainer="Bondyboy2001"

# Set the default shell for the following RUN instructions to bash instead of sh
SHELL ["/bin/bash", "-c"]

# Copy installed dependencies from the dependencies stage
COPY --from=dependencies /lsiopy /lsiopy
COPY --from=dependencies /usr/bin/kepubify /usr/bin/kepubify
COPY --from=dependencies /app/calibre /app/calibre
COPY --from=dependencies /usr/bin/lsof /usr/bin/lsof
COPY --from=dependencies /usr/bin/python3.13 /usr/bin/python3.13
COPY --from=dependencies /usr/lib/python3.13 /usr/lib/python3.13

# Install only runtime packages (no build tools)
RUN \
  echo "**** add deadsnakes PPA for Python 3.13 runtime ****" && \
  apt-get update && \
  apt-get install -y --no-install-recommends software-properties-common && \
  add-apt-repository ppa:deadsnakes/ppa && \
  apt-get update && \
  echo "**** install runtime packages ****" && \
  apt-get install -y --no-install-recommends \
  imagemagick \
  ghostscript \
  libldap2 \
  libmagic1 \
  libsasl2-2 \
  libxi6 \
  libxslt1.1 \
  xdg-utils \
  inotify-tools \
  python3.13 \
  nano \
  sqlite3 \
  zip \
  gettext \
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
  xz-utils \
  curl && \
  # Create python3 symlink to point to python3.13
  ln -sf /usr/bin/python3.13 /usr/bin/python3 && \
  # Cleanup
  apt-get -y purge software-properties-common && \
  apt-get -y autoremove && \
  rm -rf \
  /tmp/* \
  /var/lib/apt/lists/* \
  /var/tmp/* \
  /root/.cache

# STEP 6 - Copy application files
# Copy the rest of the application code (changes most frequently)
COPY --chown=abc:abc . /app/calibre-web-automated/

# STEP 7 - Configure application
RUN \
  # STEP 7.1 - Move contents of /app/calibre-web-automated/root to / and delete the /app/calibre-web-automated/root directory
  cp -R /app/calibre-web-automated/root/* / && \
  rm -R /app/calibre-web-automated/root/ && \
  # STEP 7.2 - Run Lily install script to make required dirs, set script permissions and add aliases for CLI commands  ect.
  chmod +x /app/calibre-web-automated/scripts/setup-cwa.sh && \
  /app/calibre-web-automated/scripts/setup-cwa.sh && \
  # STEP 7.3 - Create koplugin.zip from KOReader plugin folder
  echo "~~~~ Creating koplugin.zip from KOReader plugin folder... ~~~~" && \
  if [ -d "/app/calibre-web-automated/koreader/plugins/cwasync.koplugin" ]; then \
  cd /app/calibre-web-automated/koreader/plugins && \
  # Calculate digest of all files in the plugin for debugging purposes
  echo "Calculating digest of plugin files..." && \
  PLUGIN_DIGEST=$(find cwasync.koplugin -type f -name "*.lua" -o -name "*.json" | sort | xargs sha256sum | sha256sum | cut -d' ' -f1) && \
  echo "Plugin digest: $PLUGIN_DIGEST" && \
  # Create a file named after the digest inside the plugin folder
  echo "Plugin files digest: $PLUGIN_DIGEST" > cwasync.koplugin/${PLUGIN_DIGEST}.digest && \
  echo "Build date: $(date)" >> cwasync.koplugin/${PLUGIN_DIGEST}.digest && \
  echo "Files included:" >> cwasync.koplugin/${PLUGIN_DIGEST}.digest && \
  find cwasync.koplugin -type f -name "*.lua" -o -name "*.json" | sort >> cwasync.koplugin/${PLUGIN_DIGEST}.digest && \
  zip -r koplugin.zip cwasync.koplugin/ && \
  echo "Created koplugin.zip from cwasync.koplugin folder with digest file: ${PLUGIN_DIGEST}.digest"; \
  else \
  echo "Warning: cwasync.koplugin folder not found, skipping zip creation"; \
  fi && \
  # STEP 7.4 - Move koplugin.zip to static directory
  if [ -f "/app/calibre-web-automated/koreader/plugins/koplugin.zip" ]; then \
  mkdir -p /app/calibre-web-automated/cps/static && \
  cp /app/calibre-web-automated/koreader/plugins/koplugin.zip /app/calibre-web-automated/cps/static/ && \
  echo "Moved koplugin.zip to static directory"; \
  else \
  echo "Warning: koplugin.zip not found, skipping move to static directory"; \
  fi && \
  # STEP 7.5 - ADD files referencing the versions of the installed main packages
  echo "$VERSION" >| /app/CWA_RELEASE && \
  echo "$KEPUBIFY_RELEASE" >| /app/KEPUBIFY_RELEASE && \
  echo "$CALIBRE_RELEASE" > /CALIBRE_RELEASE

# Add unrar from unrar stage
COPY --from=unrar /usr/bin/unrar-ubuntu /usr/bin/unrar

# Set calibre environment variable
ENV CALIBRE_CONFIG_DIR=/config/.config/calibre

# Ports and volumes
WORKDIR /config
# The default port Lily listens on. Can be overridden with the CWA_PORT_OVERRIDE environment variable.
EXPOSE 8083
VOLUME /config
VOLUME /cwa-book-ingest
VOLUME /calibre-library

# Health check for container orchestration
# Uses shell form to support environment variable substitution for CWA_PORT_OVERRIDE
# -L follows redirects so the 302 to /login on the root path is treated as healthy
HEALTHCHECK --interval=30s --timeout=3s --start-period=120s --retries=3 \
  CMD curl -fsL http://localhost:${CWA_PORT_OVERRIDE:-8083}/ || curl -fsL -k https://localhost:${CWA_PORT_OVERRIDE:-8083}/ || exit 1
