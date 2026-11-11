#!/usr/bin/env python3
"""Collects the licences of everything that ends up inside the packaged app.

Handing out a program that contains other people's software means handing out their licence texts and notices with it.
This reads them from what was really bundled, so the list cannot drift from the build:

* the Python packages ThrashDash needs (requirements.txt and everything those need in turn): the LICENSE and NOTICE files
  each one ships in its own metadata;
* the native libraries PyInstaller copied next to the program (each is recorded below by name, and on Debian/Ubuntu the
  distribution's own copyright file for the package that supplied it is copied too);
* PyInstaller's own licence (its bootloader is part of every packaged program) and the bundled fonts' licences.

Run by packaging/build.sh with the build environment's Python. It writes
    <out>/                      the licence tree that goes into the AppImage (usr/share/licenses/thrashdash/)
    THIRD_PARTY_NOTICES.md      the readable summary, kept in the repository (only with --update-repo-file)

Usage: collect_licenses.py --internal DIR --out DIR [--update-repo-file]
"""
import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Native libraries PyInstaller bundles, by file name prefix: (component, licence, where it comes from).
NATIVE = {
    "libpython3": ("Python runtime (libpython)", "PSF-2.0", "https://www.python.org/"),
    "libssl.so": ("OpenSSL 3", "Apache-2.0", "https://www.openssl.org/"),
    "libcrypto.so": ("OpenSSL 3", "Apache-2.0", "https://www.openssl.org/"),
    "libffi.so": ("libffi", "MIT", "https://sourceware.org/libffi/"),
    "libexpat.so": ("Expat", "MIT", "https://libexpat.github.io/"),
    "libbz2.so": ("bzip2", "bzip2-1.0.6 (BSD-style)", "https://sourceware.org/bzip2/"),
    "liblzma.so": ("XZ Utils (liblzma)", "Public domain (0BSD in newer versions)", "https://tukaani.org/xz/"),
    "libmpdec.so": ("mpdecimal", "BSD-2-Clause", "https://www.bytereef.org/mpdecimal/"),
    "libz.so": ("zlib", "Zlib", "https://zlib.net/"),
    "libuuid.so": ("libuuid (util-linux)", "BSD-3-Clause", "https://github.com/util-linux/util-linux"),
    "libgcc_s.so": ("GCC runtime library (libgcc)", "GPL-3.0-or-later WITH GCC-exception-3.1", "https://gcc.gnu.org/"),
    "libstdc++.so": ("GNU C++ runtime library (libstdc++)", "GPL-3.0-or-later WITH GCC-exception-3.1", "https://gcc.gnu.org/"),
}

LICENCE_FILE = re.compile(r"^(licen[cs]e|copying|notice|authors)([-_.].*)?$", re.I)


def read_text(path):
    return Path(path).read_text(encoding="utf-8", errors="replace").strip("\n")


def requirement_names(lines, env):
    """Distribution names from requirement lines, dropping those whose marker (python version, extras) rules them out."""
    names = []
    try:
        from packaging.requirements import Requirement
    except ImportError:  # (the build environment always has it: PyInstaller needs it)
        Requirement = None
    for line in lines:
        line = line.split("#")[0].strip()
        if not line:
            continue
        if Requirement:
            req = Requirement(line)
            if req.marker is not None and not req.marker.evaluate({**env, "extra": ""}):
                continue
            names.append(req.name)
        else:
            if "extra ==" in line:
                continue
            names.append(re.match(r"[A-Za-z0-9_.-]+", line).group(0))
    return names


def runtime_packages():
    """The distributions the program needs at run time: those in requirements.txt and, recursively, what they require."""
    from_requirements = re.findall(r"^([A-Za-z0-9_.-]+)", (ROOT / "requirements.txt").read_text(), re.M)
    env = {"python_version": "%d.%d" % sys.version_info[:2], "python_full_version": sys.version.split()[0], "sys_platform": sys.platform,
           "platform_system": "Linux", "implementation_name": sys.implementation.name, "os_name": "posix"}
    seen, queue = {}, list(from_requirements)
    while queue:
        name = queue.pop()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            print(f"warning: {name} is required but not installed here", file=sys.stderr)
            continue
        seen[key] = dist
        queue.extend(requirement_names(dist.requires or [], env))
    return sorted(seen.values(), key=lambda d: d.metadata["Name"].lower())


def licence_files(dist):
    """(name, text) of each licence/notice file the distribution ships."""
    found = []
    for f in dist.files or []:
        if LICENCE_FILE.match(f.name) and ".dist-info" in str(f):
            found.append((f.name, read_text(dist.locate_file(f))))
    return sorted(found)


def licence_of(dist):
    meta = dist.metadata
    return meta.get("License-Expression") or (meta.get("License") or "").splitlines()[0][:60] or "see its licence file"


def home_of(dist):
    """A web address for the project: its home page, else its source or first project link."""
    meta = dist.metadata
    if meta.get("Home-page"):
        return meta["Home-page"]
    urls = [u.split(",", 1)[-1].strip() for u in (meta.get_all("Project-URL") or [])]
    labelled = {u.split(",", 1)[0].strip().lower(): u.split(",", 1)[-1].strip() for u in (meta.get_all("Project-URL") or [])}
    return labelled.get("homepage") or labelled.get("source") or labelled.get("repository") or (urls[0] if urls else "")


def copyright_file_for(filename):
    """The Debian/Ubuntu package that ships `filename` (a library's file name) and that package's copyright file.

    dpkg is asked by file name, not by full path: on systems where /lib is a link to /usr/lib it records files under the
    path they were installed to, which is not always the one a lookup by resolved path would use. Returns (None, None)
    where dpkg does not exist, (package, None) if the package has no copyright file.
    """
    if not shutil.which("dpkg"):
        return None, None
    try:
        out = subprocess.run(["dpkg", "-S", f"*/{filename}"], capture_output=True, text=True).stdout
    except OSError:
        return None, None
    packages = [line.split(":")[0].strip() for line in out.splitlines() if ":" in line]
    packages = [p for p in packages if not p.endswith("-dev")] or packages
    if not packages:
        return None, None
    doc = Path("/usr/share/doc") / packages[0] / "copyright"
    return (packages[0], doc) if doc.is_file() else (packages[0], None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--internal", required=True, help="PyInstaller's bundle folder (dist/<name>/_internal): the native libraries are found there")
    ap.add_argument("--out", required=True, help="folder to fill with the licence tree")
    ap.add_argument("--update-repo-file", action="store_true", help="also rewrite THIRD_PARTY_NOTICES.md in the repository")
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    (out / "python-packages").mkdir(parents=True)
    (out / "system-libraries").mkdir()
    (out / "fonts").mkdir()
    problems = []

    # ---- Python packages ----
    packages = runtime_packages()
    rows, texts = [], {}  # texts: hash -> (text, [package names])
    notices = []
    for dist in packages:
        name, version = dist.metadata["Name"], dist.version
        files = licence_files(dist)
        if not files:
            problems.append(f"no licence file found for {name}")
        folder = out / "python-packages" / f"{name}-{version}"
        folder.mkdir()
        for fname, text in files:
            (folder / fname).write_text(text + "\n", encoding="utf-8")
            if fname.lower().startswith("notice"):
                notices.append((name, text))
            else:
                digest = hashlib.sha256(re.sub(r"\s+", " ", text).encode()).hexdigest()
                texts.setdefault(digest, (text, []))[1].append(name)
        rows.append((name, version, licence_of(dist), home_of(dist)))

    # ---- native libraries ----
    internal = Path(args.internal)
    native_rows, seen_components = [], set()
    for lib in sorted(p for p in internal.iterdir() if p.is_file() and ".so" in p.name):
        match = next((v for k, v in NATIVE.items() if lib.name.startswith(k)), None)
        if match is None:
            problems.append(f"UNKNOWN native library {lib.name}: add it to NATIVE in packaging/collect_licenses.py with its licence")
            native_rows.append((lib.name, "UNKNOWN", "UNKNOWN", ""))
            continue
        component, licence, url = match
        native_rows.append((lib.name, component, licence, url))
        if component in seen_components:
            continue
        seen_components.add(component)
        package, doc = copyright_file_for(lib.name)
        if doc:
            shutil.copy(doc, out / "system-libraries" / f"{package}.copyright")
        elif shutil.which("dpkg"):
            # On Debian and Ubuntu every bundled library has an owning package with a copyright file: if not, say so
            problems.append(f"no copyright file found for {lib.name} (package: {package or 'unknown'})")
    if not any((out / "system-libraries").iterdir()):
        (out / "system-libraries" / "README.txt").write_text(
            "The libraries listed in THIRD_PARTY_NOTICES.md come from the operating system the program was built on; on Debian and "
            "Ubuntu their copyright files are copied here when the program is built. This build was not made on such a system.\n")

    # ---- PyInstaller, fonts ----
    try:
        for fname, text in licence_files(metadata.distribution("pyinstaller")):
            (out / f"pyinstaller-{fname}").write_text(text + "\n", encoding="utf-8")
    except metadata.PackageNotFoundError:
        problems.append("PyInstaller's licence was not found (is this the build environment?)")
    for source in [ROOT / "web" / "fonts" / "NOTICE.txt", *sorted((ROOT / "web" / "fonts" / "licenses").glob("*.txt"))]:
        shutil.copy(source, out / "fonts" / source.name)
    shutil.copy(ROOT / "LICENSE", out / "LICENSE")

    # ---- the readable summary ----
    md = ["# Third-party notices", "",
          "ThrashDash itself is released under the MIT licence (see [LICENSE](LICENSE)). The packaged program (the AppImage)",
          "also contains the software listed here, which has its own licences. This file is generated by",
          "`packaging/collect_licenses.py` from what was actually bundled; the complete licence texts, and the Linux distribution's",
          "own copyright files for the system libraries, are inside the AppImage under `usr/share/licenses/thrashdash/`",
          "(`./ThrashDash-<version>-x86_64.AppImage --appimage-extract 'usr/share/licenses/*'` unpacks them).", "",
          "## Python packages", "", "| Package | Version | Licence | Home |", "|:---|:---|:---|:---|"]
    md += [f"| {n} | {v} | {l} | {h} |" for n, v, l, h in rows]
    md += ["", "## Native libraries bundled with the Python runtime", "",
           "| File | Component | Licence | Home |", "|:---|:---|:---|:---|"]
    md += [f"| `{f}` | {c} | {l} | {u} |" for f, c, l, u in native_rows]
    md += ["",
           "The GCC runtime libraries are licensed under the GPL with the GCC Runtime Library Exception, which allows them to be "
           "distributed with programs under any licence; their source is available from <https://gcc.gnu.org/>. Python's own "
           "licence (PSF-2.0) is in the Python packages section below under `aiohappyeyeballs`/`typing_extensions` and at "
           "<https://docs.python.org/3/license.html>.", "",
           "## PyInstaller", "",
           "The program is packaged with [PyInstaller](https://pyinstaller.org/). Its bootloader is part of every packaged program; "
           "it is licensed under the GPL-2.0-or-later **with an exception that permits distributing programs that include it under "
           "any licence**. The licence text is `pyinstaller-COPYING.txt` in the AppImage's licence folder.", "",
           "## Fonts", "",
           "Jost, Barlow Condensed and DSEG7 Classic are used under the SIL Open Font License 1.1. See "
           "[web/fonts/NOTICE.txt](web/fonts/NOTICE.txt) and [web/fonts/licenses/](web/fonts/licenses/).", "",
           "## Licence texts", "",
           "Each distinct licence text shipped by the Python packages above, once, with the packages it applies to.", ""]
    for text, owners in sorted(texts.values(), key=lambda t: t[1][0].lower()):
        md += [f"### {', '.join(sorted(owners, key=str.lower))}", "", "```text", text, "```", ""]
    if notices:
        md += ["## Notices", ""]
        for name, text in notices:
            md += [f"### {name}", "", "```text", text, "```", ""]
    summary = "\n".join(md) + "\n"
    (out / "THIRD_PARTY_NOTICES.md").write_text(summary, encoding="utf-8")
    if args.update_repo_file:
        (ROOT / "THIRD_PARTY_NOTICES.md").write_text(summary, encoding="utf-8")

    print(f"licences collected: {len(rows)} Python packages, {len(native_rows)} native libraries, {len(texts)} distinct licence texts")
    for p in problems:
        print(f"PROBLEM: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
