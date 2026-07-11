#!/usr/bin/env python3
"""Assemble and checksum the standalone WFTSP launcher release bundle."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_NAME = "WFTSP_KR_Steam_Patch_GUI_package"
PACKAGE_ROOT = DIST / PACKAGE_NAME
ROOT_FILES = [
    "README.md",
    "THIRD_PARTY_NOTICES.txt",
    "WFTSP_KR_Steam_Patch_GUI.cmd",
    "launcher_version.json",
]
DIST_FILES = [
    "WFTSP_KR_Steam_Patch_GUI.exe",
    "WFTSP_KR_Steam_Patch_Updater.exe",
]


def copy_required(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SystemExit(f"required release file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main() -> int:
    metadata = json.loads((ROOT / "launcher_version.json").read_text(encoding="utf-8"))
    version = str(metadata["version"])
    if PACKAGE_ROOT.exists():
        shutil.rmtree(PACKAGE_ROOT)
    PACKAGE_ROOT.mkdir(parents=True)
    for name in ROOT_FILES:
        copy_required(ROOT / name, PACKAGE_ROOT / name)
    for name in DIST_FILES:
        copy_required(DIST / name, PACKAGE_ROOT / name)
    copy_required(ROOT / "payload" / "ddraw.dll", PACKAGE_ROOT / "payload" / "ddraw.dll")
    artwork = ROOT / "assets" / "launcher_art.png"
    if artwork.is_file():
        copy_required(artwork, PACKAGE_ROOT / "assets" / artwork.name)

    archive = DIST / f"{PACKAGE_NAME}-{version}.zip"
    if archive.exists():
        archive.unlink()
    shutil.make_archive(str(archive.with_suffix("")), "zip", PACKAGE_ROOT)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(archive)
    print(checksum)
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
