#!/usr/bin/env bash
# Builds dist/ThrashDash-<version>-x86_64.AppImage.
#
# An AppImage runs on any Linux that is not OLDER than the one it was built on (it uses that system's C library), so
# build it on an old system. packaging/build-in-docker.sh does exactly that inside an Ubuntu 22.04 container; running this
# script directly builds on whatever you have, which is fine to try it but may not run on older distributions.
#
# Needs: python3 with venv and pip, binutils (objdump), file, and curl (or wget). Everything it makes is under build/ and dist/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
APPDIR="$BUILD/AppDir"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
TOOL="$BUILD/appimagetool-x86_64.AppImage"
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64"
RUNTIME="$BUILD/runtime-x86_64"

mkdir -p "$BUILD" "$ROOT/dist"
export PYINSTALLER_CONFIG_DIR="$BUILD/pyinstaller-cache"  # keep its cache here rather than in the home folder

echo "== Python environment"
# one environment per Python version, so a build on this machine and one in a container (another Python) never share one
PYTAG="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
VENV="$BUILD/venv-$PYTAG"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$ROOT/requirements.txt" -r "$ROOT/packaging/requirements-build.txt"
VERSION="$("$VENV/bin/python" -c "import sys; sys.path.insert(0, '$ROOT/relay'); import thrashdash; print(thrashdash.__version__)")"

echo "== Freezing ThrashDash $VERSION with PyInstaller"
cd "$ROOT"
"$VENV/bin/pyinstaller" --noconfirm --clean --distpath "$BUILD/pyi-dist" --workpath "$BUILD/pyi-work" packaging/thrashdash.spec

echo "== Assembling the AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib"
cp -r "$BUILD/pyi-dist/thrashdash" "$APPDIR/usr/lib/thrashdash"
cp packaging/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp packaging/thrashdash.desktop packaging/thrashdash.svg packaging/thrashdash.png "$APPDIR/"
ln -s thrashdash.png "$APPDIR/.DirIcon"

download() {  # download URL FILE
    echo "downloading $1"
    if command -v curl >/dev/null; then curl -fsSL --max-time 300 -o "$2" "$1"; else wget -q -T 300 -O "$2" "$1"; fi
}

echo "== appimagetool"
[ -x "$TOOL" ] || { download "$TOOL_URL" "$TOOL"; chmod +x "$TOOL"; }
# (fetched here and handed over: appimagetool's own download of it can stall on systems whose certificates it cannot find)
[ -s "$RUNTIME" ] || download "$RUNTIME_URL" "$RUNTIME"
echo "appimagetool sha256: $(sha256sum "$TOOL" | cut -d' ' -f1)"
echo "runtime      sha256: $(sha256sum "$RUNTIME" | cut -d' ' -f1)"
OUT="$ROOT/dist/ThrashDash-$VERSION-x86_64.AppImage"
rm -f "$OUT"
# APPIMAGE_EXTRACT_AND_RUN lets appimagetool run without FUSE (containers usually have none)
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 timeout 300 "$TOOL" --no-appstream --runtime-file "$RUNTIME" "$APPDIR" "$OUT"

GLIBC="$(ldd --version | head -1 | grep -o '[0-9]*\.[0-9]*$' || true)"
echo
echo "Built $OUT ($(du -h "$OUT" | cut -f1)) on glibc ${GLIBC:-unknown}."
case "$GLIBC" in
    2.3[6-9]|2.[4-9]*) echo "NOTE: this system is newer than Ubuntu 22.04 (glibc 2.35): the AppImage may not run on older distributions. Use packaging/build-in-docker.sh for one to hand out." ;;
esac
