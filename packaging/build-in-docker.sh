#!/usr/bin/env bash
# Builds the AppImage inside an Ubuntu 22.04 container, so that it runs on any Linux from about 2022 onwards.
# Needs a running Docker (or compatible) daemon that your user may use. Result: dist/ThrashDash-<version>-x86_64.AppImage
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# --security-opt label=disable: on systems with SELinux (Fedora, openSUSE, ...) a container may not read an ordinary folder
# mounted into it ("Permission denied") unless its labelling is switched off for this one container; the folder is not relabelled.
docker run --rm --security-opt label=disable -v "$ROOT":/src -w /src -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" ubuntu:22.04 bash -c '
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip binutils curl ca-certificates file >/dev/null
    bash packaging/build.sh
    chown -R "$HOST_UID:$HOST_GID" build dist THIRD_PARTY_NOTICES.md
'
