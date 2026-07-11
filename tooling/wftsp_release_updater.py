#!/usr/bin/env python3
"""Safe out-of-process updater for the WFTSP Korean patch launcher."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

APP_TITLE = "Wind Fantasy SP 한국어 패치 런처 업데이트"
VERSION_FILE = "launcher_version.json"
DEFAULT_REPOSITORY = "jhf-dev/wf-sp-kr-launcher"
DEFAULT_ASSET_PATTERN = "WFTSP_KR_Steam_Patch_GUI_package"
DEFAULT_LAUNCHER = "WFTSP_KR_Steam_Patch_GUI.exe"
USER_AGENT = "WFTSP-KR-Patch-Updater"
MANAGED_ROOT_FILES = {
    "README.md", "THIRD_PARTY_NOTICES.txt", "WFTSP_KR_Steam_Patch_GUI.cmd",
    DEFAULT_LAUNCHER, "WFTSP_KR_Steam_Patch_Updater.exe", VERSION_FILE,
}
MANAGED_ROOT_DIRS = {"assets", "payload"}


@dataclass(frozen=True)
class VersionInfo:
    version: str
    repository: str
    asset_pattern: str
    launcher: str
    updater: str


def load_version_info(path: Path) -> VersionInfo:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not str(data.get("version", "")).strip():
        raise ValueError(f"버전 정보가 올바르지 않습니다: {path}")
    return VersionInfo(
        version=str(data["version"]).strip(),
        repository=str(data.get("repository") or DEFAULT_REPOSITORY).strip(),
        asset_pattern=str(data.get("asset_pattern") or DEFAULT_ASSET_PATTERN).strip(),
        launcher=str(data.get("launcher") or DEFAULT_LAUNCHER).strip(),
        updater=str(data.get("updater") or "WFTSP_KR_Steam_Patch_Updater.exe").strip(),
    )


def fetch_latest_release(repository: str) -> dict[str, object]:
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"GitHub 저장소 이름이 올바르지 않습니다: {repository}")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{parts[0]}/{parts[1]}/releases/latest",
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if not isinstance(data, dict) or data.get("draft"):
        raise RuntimeError("사용 가능한 최신 안정 릴리즈를 찾지 못했습니다.")
    return data


def select_release_asset(release: dict[str, object], pattern: str) -> dict[str, object]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("릴리즈에 다운로드 파일이 없습니다.")
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        if name.lower().endswith(".zip") and pattern.lower() in name.lower():
            return asset
    raise RuntimeError(f"릴리즈 ZIP을 찾지 못했습니다: {pattern}")


def release_tag_key(tag: str) -> tuple[int, int, int, int] | None:
    match = re.fullmatch(r"beta-(\d{4})-(\d{2})-(\d{2})-v(\d+)", tag)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def is_remote_newer(local_tag: str, remote_tag: str) -> bool:
    if local_tag == remote_tag:
        return False
    local_key = release_tag_key(local_tag)
    remote_key = release_tag_key(remote_tag)
    if local_key is None or remote_key is None:
        raise RuntimeError(
            f"버전 순서를 안전하게 비교할 수 없습니다: {local_tag} / {remote_tag}"
        )
    return remote_key > local_key


def download_asset(asset: dict[str, object], destination: Path) -> None:
    url = str(asset.get("browser_download_url") or "")
    if not url.startswith("https://"):
        raise RuntimeError("릴리즈 다운로드 주소가 올바르지 않습니다.")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def safe_extract_zip(archive_path: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            target = (root / normalized).resolve()
            if Path(normalized).is_absolute() or ".." in Path(normalized).parts:
                raise RuntimeError(f"안전하지 않은 ZIP 경로입니다: {info.filename}")
            if target != root and root not in target.parents:
                raise RuntimeError(f"안전하지 않은 ZIP 경로입니다: {info.filename}")
        archive.extractall(root)


def validate_candidate(candidate: Path, expected_tag: str) -> VersionInfo:
    version_path = candidate / VERSION_FILE
    if not version_path.is_file():
        raise RuntimeError(f"업데이트 패키지에 {VERSION_FILE}이 없습니다.")
    info = load_version_info(version_path)
    if info.version != expected_tag:
        raise RuntimeError(f"릴리즈 태그와 패키지 버전이 다릅니다: {expected_tag} / {info.version}")
    if not (candidate / info.launcher).is_file():
        raise RuntimeError(f"업데이트 패키지에 런처가 없습니다: {info.launcher}")
    if not (candidate / "payload" / "ddraw.dll").is_file():
        raise RuntimeError("업데이트 패키지에 payload/ddraw.dll이 없습니다.")
    return info


def iter_managed_sources(candidate: Path):
    for name in sorted(MANAGED_ROOT_FILES):
        source = candidate / name
        if source.is_file():
            yield source, Path(name)
    for name in sorted(MANAGED_ROOT_DIRS):
        source_root = candidate / name
        if source_root.is_dir():
            for source in sorted(source_root.rglob("*")):
                if source.is_file():
                    yield source, source.relative_to(candidate)


def apply_candidate(candidate: Path, bundle_root: Path) -> Path:
    if not (bundle_root / VERSION_FILE).is_file():
        raise RuntimeError("배포 패키지 루트가 아닙니다. launcher_version.json 옆에서 실행해 주세요.")
    backup_root = bundle_root / "_launcher_updates" / "backup" / datetime.now().strftime("%Y%m%d_%H%M%S")
    for source, relative in iter_managed_sources(candidate):
        target = bundle_root / relative
        if target.is_file():
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return backup_root


def wait_for_parent(parent_pid: int) -> None:
    if parent_pid <= 0:
        return
    for _ in range(300):
        try:
            os.kill(parent_pid, 0)
        except OSError:
            return
        time.sleep(0.1)


def restart_launcher(bundle_root: Path, launcher: str) -> None:
    launcher_path = bundle_root / launcher
    if launcher_path.is_file():
        subprocess.Popen([str(launcher_path)], cwd=str(bundle_root))


def run_update(bundle_root: Path, parent_pid: int) -> str:
    local = load_version_info(bundle_root / VERSION_FILE)
    release = fetch_latest_release(local.repository)
    remote_tag = str(release.get("tag_name") or "").strip()
    if not remote_tag:
        raise RuntimeError("최신 릴리즈 태그를 확인하지 못했습니다.")
    if not is_remote_newer(local.version, remote_tag):
        return f"이미 최신 버전입니다.\n현재: {local.version}"
    asset = select_release_asset(release, local.asset_pattern)
    with tempfile.TemporaryDirectory(prefix="wftsp_update_") as temp_dir:
        temp = Path(temp_dir)
        archive = temp / "release.zip"
        candidate = temp / "candidate"
        candidate.mkdir()
        download_asset(asset, archive)
        safe_extract_zip(archive, candidate)
        remote = validate_candidate(candidate, remote_tag)
        wait_for_parent(parent_pid)
        backup = apply_candidate(candidate, bundle_root)
    return f"런처 업데이트를 적용했습니다.\n이전: {local.version}\n현재: {remote.version}\n백업: {backup}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", type=Path)
    parser.add_argument("--parent-pid", type=int, default=0)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        if release_tag_key("beta-2026-07-11-v1") is None:
            raise SystemExit("release tag parser failed")
        return 0
    if args.bundle_root is None:
        parser.error("--bundle-root is required")
    bundle_root = args.bundle_root.resolve()
    launcher = DEFAULT_LAUNCHER
    try:
        launcher = load_version_info(bundle_root / VERSION_FILE).launcher
        messagebox.showinfo(APP_TITLE, run_update(bundle_root, args.parent_pid))
        result = 0
    except Exception as exc:
        messagebox.showerror(APP_TITLE, str(exc))
        result = 1
    if args.restart:
        restart_launcher(bundle_root, launcher)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
