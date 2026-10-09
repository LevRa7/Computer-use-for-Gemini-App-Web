#!/usr/bin/env python3
"""tools/build-payload-zip.py - build the archive a node updates itself from.

The release publishes ``mesh-payload-<version>.zip``: exactly the trees the
updater swaps in (``core/``, ``skills/``, ``ops/``) plus ``package.json``, and a
``.sha256`` beside it. The updater refuses to install a payload whose hash does
not match the published one, so this script is the only supported producer.

Why not ``Compress-Archive`` / ``zip -r``:

* **Separators.** ``Compress-Archive`` writes Windows backslashes into the entry
  names. Python's ``zipfile`` treats a backslash as an ordinary character on
  POSIX, so such an archive unpacks into literal files called ``core\\agent.py``
  and the node would then fail to find its own code.
* **Bytecode.** A ``__pycache__`` directory built before the release carries
  timestamps that can match the sources it was compiled from, so the updated node
  would run the OLD bytecode under the NEW file names. It is excluded here, and
  the updater clears any that still arrive.
* **Reproducibility.** Entries are sorted and timestamps are fixed, so the same
  tree on the same host family produces the same bytes - which makes a published
  checksum mean something across rebuilds. Two measured caveats: a zip built on
  Windows and one built on Linux differ in exactly one byte per entry, the "version
  made by" host byte of each central-directory record (0 = MS-DOS, 3 = Unix), and a
  checkout with ``core.autocrlf=true`` holds CRLF while the committed blobs hold LF,
  so a Windows working tree is not the tree CI builds from. Entry contents and CRCs
  are identical in the first case only. A node therefore always verifies the
  checksum computed from the *published* archive (see docs/CI.md).

Usage::

    python tools/build-payload-zip.py                       # dist/mesh-payload-<version>.zip
    python tools/build-payload-zip.py --out /tmp/payload.zip
    python tools/build-payload-zip.py --version 0.3.0 --print-hash
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Directories the updater replaces, plus the one file it also carries.
PAYLOAD_DIRS = ("core", "skills", "ops")
PAYLOAD_FILES = ("package.json",)

#: Never shipped: generated, per-machine or local-only.
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".state", "node_modules"}
SKIP_SUFFIXES = (".pyc", ".pyo")
#: A fixed timestamp makes the archive byte-for-byte reproducible.
FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def version_from_module(root: str = REPO) -> str:
    path = os.path.join(root, "core", "version.py")
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("core/version.py declares no __version__")


def iter_tree(root: str, relative: str):
    """Yield (absolute path, archive name) for one payload tree, sorted."""
    base = os.path.join(root, relative)
    if os.path.isfile(base):
        yield base, relative.replace(os.sep, "/")
        return
    if not os.path.isdir(base):
        raise SystemExit("payload item is missing: %s" % base)
    for current, directories, files in os.walk(base):
        directories[:] = sorted(d for d in directories if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.endswith(SKIP_SUFFIXES) or name.startswith("."):
                continue
            absolute = os.path.join(current, name)
            archive = os.path.relpath(absolute, root).replace(os.sep, "/")
            yield absolute, archive


def build(out_path: str, *, root: str = REPO, version: str = "") -> tuple[str, str, int]:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    count = 0
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for item in list(PAYLOAD_DIRS) + list(PAYLOAD_FILES):
            for absolute, archive in iter_tree(root, item):
                info = zipfile.ZipInfo(archive, date_time=FIXED_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                with open(absolute, "rb") as handle:
                    bundle.writestr(info, handle.read())
                count += 1
    digest = hashlib.sha256()
    with open(out_path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(block)
    sha = digest.hexdigest()
    return out_path, sha, count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the updatable node payload archive.")
    parser.add_argument("--out", default="", help="output zip (default dist/mesh-payload-<version>.zip)")
    parser.add_argument("--root", default=REPO, help="repository root (default: this checkout)")
    parser.add_argument("--version", default="", help="version to name the archive after")
    parser.add_argument("--no-checksum", action="store_true",
                        help="do not write the .sha256 file next to the archive")
    parser.add_argument("--print-hash", action="store_true", help="print only the SHA-256")
    args = parser.parse_args(argv)

    version = args.version or version_from_module(args.root)
    out = args.out or os.path.join(args.root, "dist", "mesh-payload-%s.zip" % version)
    out, sha, count = build(out, root=args.root, version=version)
    if not args.no_checksum:
        checksum = out + ".sha256"
        with open(checksum, "w", encoding="ascii", newline="\n") as handle:
            handle.write("%s  %s\n" % (sha, os.path.basename(out)))
    if args.print_hash:
        print(sha)
        return 0
    print("payload : %s" % out)
    print("entries : %d" % count)
    print("sha256  : %s" % sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
