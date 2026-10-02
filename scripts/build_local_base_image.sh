#!/usr/bin/env bash
# Build a sandbox base image WITHOUT Docker Hub, from the Ubuntu archive (debootstrap).
# Use when `docker pull python:3.12-slim` is blocked (corporate proxies, air-gapped CI).
#
#   sudo scripts/build_local_base_image.sh            # -> swe-agent/ubuntu-python:noble
#   export SANDBOX_BASE_IMAGE=swe-agent/ubuntu-python:noble SANDBOX_PYTHON_VERSION=3.12 \
#          SANDBOX_MANYLINUX_MAX=2_39
set -euo pipefail
TAG="${1:-swe-agent/ubuntu-python:noble}"
MIRROR="${MIRROR:-http://archive.ubuntu.com/ubuntu/}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
command -v debootstrap >/dev/null || { echo "install debootstrap first" >&2; exit 1; }
debootstrap --variant=minbase --components=main,universe \
  --include=python3,python3-venv,python3-pip,ca-certificates noble "$WORK/rootfs" "$MIRROR"
rm -rf "$WORK/rootfs/var/cache/apt/archives/"*.deb "$WORK/rootfs/var/lib/apt/lists/"*
tar -C "$WORK/rootfs" -c . | docker import - "$TAG"
docker run --rm --network none "$TAG" python3 -c "import sys, venv; print(sys.version)"
echo "built $TAG"
