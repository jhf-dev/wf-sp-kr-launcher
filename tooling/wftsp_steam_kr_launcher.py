#!/usr/bin/env python3
"""Apply and launch the Korean WFTSP data overlay on the Taiwan Steam Win10 client.

The Steam build runs through ``WindConfig.exe`` and then ``wf_sp_win10.exe``.
This launcher keeps those executables intact, overlays the repaired Korean data
files, and patches the Win10 locale shim ``wind.dll`` from CP936 to CP949.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from wftsp_dialogue_pointer_patch import scan_and_patch_stage
from wftsp_fy_city_exit_patch import patch_man as patch_fy_city_exit_man

try:
    import winreg
except ImportError:  # pragma: no cover - this tool is Windows-only in practice.
    winreg = None  # type: ignore[assignment]


TARGET_DEFAULT = Path("Wind Fantasy SP_TW")
BACKUP_DIR_NAME = "_wftsp_kr_patch_backup"
STATE_FILE_NAME = "wftsp_kr_patch_state.json"

EXPECTED_TW_WIND_DLL_SHA256 = (
    "05BC2FE4099F8430035C7A60CBDB072C0DC82393320A5F222A8C2804366336AD"
)

KR_OVERLAY_FILES = [
    "game.ini",
    "setup.ini",
    "bmp",
    "manbmp",
    "manani",
    "man",
    "stage",
]

COMPATIBILITY_CHECK_FILES = [
    "map",
    "face",
    "mapbmp",
    "Wave",
]

TARGET_EXECUTABLES = [
    "wf_sp_win10.exe",
    "wf_sp.exe",
    "WindConfig.exe",
    "wind.dll",
]

# ``wind.dll`` exports ordinal wrappers for TextOutA, MultiByteToWideChar, and
# WideCharToMultiByte. The TW Steam build forces CP936 when the game asks for
# CP_ACP, so Korean CP949 data needs only these executable-code immediates.
WIND_DLL_CP949_PATCHES = [
    (0x0410, bytes.fromhex("A8 03 00 00"), bytes.fromhex("B5 03 00 00"), "ordinal1 cmp GetACP"),
    (0x044B, bytes.fromhex("A8 03 00 00"), bytes.fromhex("B5 03 00 00"), "ordinal1 forced codepage"),
    (0x0490, bytes.fromhex("A8 03 00 00"), bytes.fromhex("B5 03 00 00"), "ordinal2 cmp GetACP"),
    (0x04DB, bytes.fromhex("A8 03 00 00"), bytes.fromhex("B5 03 00 00"), "ordinal2 forced codepage"),
    (0x0543, bytes.fromhex("A8 03 00 00"), bytes.fromhex("B5 03 00 00"), "TextOutA length conversion"),
    (0x058E, bytes.fromhex("A8 03 00 00"), bytes.fromhex("B5 03 00 00"), "TextOutA string conversion"),
]

WINDSP_REGISTRY_KEY = "WindSP"
REGISTRY_VALUES = {
    "fullscreen": {"IsFullscreen": 1},
    "windowed": {"IsFullscreen": 0},
}


@dataclasses.dataclass(frozen=True)
class FileStatus:
    relative_path: str
    source_sha256: str | None
    target_sha256: str | None
    target_matches_source: bool | None
    size_source: int | None
    size_target: int | None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def read_bytes(path: Path, offset: int, length: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(offset)
        return handle.read(length)


def prepare_overlay_data(kr_root: Path, relative_path: str) -> tuple[bytes | None, dict[str, object] | None]:
    """Return patched source bytes for files that need KR-side repairs.

    The user's KR folder can be either the already-repaired working copy or an
    untouched Korean release folder. We never mutate that source folder; known
    progression fixes are applied in memory before writing into the TW target.
    ``None`` means the caller can copy the file directly.
    """
    src = kr_root / relative_path
    if relative_path == "stage":
        original = src.read_bytes()
        patched, report = scan_and_patch_stage(original)
        return patched, {
            "kind": "stage_dialogue_pointer_fix",
            "changed": patched != original,
            "report": report,
        }
    if relative_path == "man":
        original = src.read_bytes()
        patched, report = patch_fy_city_exit_man(original)
        return patched, {
            "kind": "fy_city_exit_fix",
            "changed": patched != original,
            "report": dataclasses.asdict(report),
        }
    return None, None


def repo_root_from_script() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    kr_root = args.kr_root.resolve()
    tw_root = args.tw_root.resolve()
    return kr_root, tw_root


def backup_path_for(tw_root: Path, relative_path: str) -> Path:
    return tw_root / BACKUP_DIR_NAME / relative_path


def state_path(tw_root: Path) -> Path:
    return tw_root / BACKUP_DIR_NAME / STATE_FILE_NAME


def ensure_target_layout(tw_root: Path) -> None:
    missing = [name for name in TARGET_EXECUTABLES if not (tw_root / name).exists()]
    if missing:
        raise SystemExit(f"TW Steam target is missing required files: {', '.join(missing)}")


def ensure_sources(kr_root: Path) -> None:
    missing = [name for name in KR_OVERLAY_FILES if not (kr_root / name).exists()]
    if missing:
        raise SystemExit(f"KR source is missing overlay files: {', '.join(missing)}")


def running_wftsp_processes() -> list[str]:
    names = {"wf_sp.exe", "wf_sp_win10.exe", "windconfig.exe"}
    try:
        proc = subprocess.run(
            ["tasklist", "/fo", "csv", "/nh"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="mbcs",
            errors="replace",
        )
    except OSError:
        return []

    running: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.startswith('"'):
            continue
        image = line.split('","', 1)[0].strip('"').lower()
        if image in names:
            running.append(image)
    return sorted(set(running))


def backup_file_once(tw_root: Path, relative_path: str) -> None:
    src = tw_root / relative_path
    dst = backup_path_for(tw_root, relative_path)
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_overlay_file(kr_root: Path, tw_root: Path, relative_path: str, dry_run: bool) -> dict[str, object]:
    src = kr_root / relative_path
    dst = tw_root / relative_path
    prepared_data, source_patch = prepare_overlay_data(kr_root, relative_path)
    source_hash_before = sha256_file(src)
    source_hash = sha256_bytes(prepared_data) if prepared_data is not None else source_hash_before
    target_hash = sha256_file(dst) if dst.exists() else None
    target_size_before = dst.stat().st_size if dst.exists() else None
    changed = source_hash != target_hash
    if changed and not dry_run:
        if dst.exists():
            backup_file_once(tw_root, relative_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if prepared_data is None:
            shutil.copy2(src, dst)
        else:
            dst.write_bytes(prepared_data)
    return {
        "path": relative_path,
        "changed": changed,
        "source_sha256_before_source_patch": source_hash_before,
        "source_sha256": source_hash,
        "target_sha256_before": target_hash,
        "target_sha256_after": source_hash if changed and not dry_run else target_hash,
        "size_source": src.stat().st_size,
        "size_prepared_source": len(prepared_data) if prepared_data is not None else src.stat().st_size,
        "size_target_before": target_size_before,
        "source_patch": source_patch,
    }


def wind_dll_patch_state(path: Path) -> str:
    saw_original = False
    saw_patched = False
    for offset, original, patched, _label in WIND_DLL_CP949_PATCHES:
        current = read_bytes(path, offset, len(original))
        if current == original:
            saw_original = True
        elif current == patched:
            saw_patched = True
        else:
            return "unexpected"
    if saw_original and saw_patched:
        return "partial"
    if saw_patched:
        return "cp949"
    return "cp936"


def patch_wind_dll(tw_root: Path, dry_run: bool) -> dict[str, object]:
    path = tw_root / "wind.dll"
    before_hash = sha256_file(path)
    state_before = wind_dll_patch_state(path)
    if state_before == "unexpected":
        raise SystemExit(
            "wind.dll patch offsets do not match the known TW Steam layout; "
            "refusing to patch this DLL."
        )

    changed_offsets: list[dict[str, object]] = []
    if state_before != "cp949" and not dry_run:
        backup_file_once(tw_root, "wind.dll")
        data = bytearray(path.read_bytes())
        for offset, original, patched, label in WIND_DLL_CP949_PATCHES:
            current = bytes(data[offset : offset + len(original)])
            if current == original:
                data[offset : offset + len(original)] = patched
                changed_offsets.append({"offset": f"0x{offset:X}", "label": label})
            elif current == patched:
                continue
            else:
                raise SystemExit(f"unexpected wind.dll bytes at 0x{offset:X}: {current.hex(' ')}")
        path.write_bytes(bytes(data))
    elif state_before != "cp949":
        for offset, original, _patched, label in WIND_DLL_CP949_PATCHES:
            current = read_bytes(path, offset, len(original))
            if current == original:
                changed_offsets.append({"offset": f"0x{offset:X}", "label": label})

    after_hash = sha256_file(path)
    return {
        "path": "wind.dll",
        "state_before": state_before,
        "state_after": wind_dll_patch_state(path),
        "sha256_before": before_hash,
        "sha256_after": after_hash,
        "expected_tw_original_sha256": EXPECTED_TW_WIND_DLL_SHA256,
        "changed": before_hash != after_hash,
        "dry_run": dry_run,
        "offsets": changed_offsets,
    }


def overlay_status(kr_root: Path, tw_root: Path, files: list[str]) -> list[FileStatus]:
    result: list[FileStatus] = []
    for relative_path in files:
        src = kr_root / relative_path
        dst = tw_root / relative_path
        src_hash = sha256_file(src) if src.exists() else None
        dst_hash = sha256_file(dst) if dst.exists() else None
        result.append(
            FileStatus(
                relative_path=relative_path,
                source_sha256=src_hash,
                target_sha256=dst_hash,
                target_matches_source=(src_hash == dst_hash) if src_hash and dst_hash else None,
                size_source=src.stat().st_size if src.exists() else None,
                size_target=dst.stat().st_size if dst.exists() else None,
            )
        )
    return result


def registry_status() -> dict[str, object]:
    if winreg is None:
        return {"available": False}
    values: dict[str, object] = {"available": True, "key": f"HKCU\\{WINDSP_REGISTRY_KEY}"}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDSP_REGISTRY_KEY) as key:
            for name in ["IsFullscreen", "CreationWidth", "CreationHeight"]:
                try:
                    value, value_type = winreg.QueryValueEx(key, name)
                    values[name] = {"value": int(value), "type": int(value_type)}
                except FileNotFoundError:
                    values[name] = None
    except FileNotFoundError:
        values["exists"] = False
    else:
        values["exists"] = True
    return values


def set_registry_options(
    display_mode: str | None,
    width: int | None,
    height: int | None,
    dry_run: bool,
) -> dict[str, object]:
    if display_mode is None and width is None and height is None:
        return {"changed": False, "reason": "no display options requested"}
    if winreg is None:
        raise SystemExit("winreg is unavailable; cannot write WindConfig registry settings")
    if display_mode == "borderless":
        return {
            "changed": False,
            "deferred": True,
            "reason": "WindConfig exposes fullscreen/windowed registry values, not borderless window style.",
        }

    changed: dict[str, int] = {}
    planned: dict[str, int] = {}
    if display_mode in REGISTRY_VALUES:
        planned.update(REGISTRY_VALUES[display_mode])
    if width is not None:
        planned["CreationWidth"] = width
    if height is not None:
        planned["CreationHeight"] = height
    if dry_run:
        return {"changed": bool(planned), "dry_run": True, "values": planned, "key": f"HKCU\\{WINDSP_REGISTRY_KEY}"}

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDSP_REGISTRY_KEY) as key:
        for name, value in planned.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
            changed[name] = value
    return {"changed": bool(changed), "values": changed, "key": f"HKCU\\{WINDSP_REGISTRY_KEY}"}


def scan_steam_indicators(tw_root: Path) -> dict[str, object]:
    indicators = {}
    needles = [b"steam", b"SteamAPI", b"appid"]
    for name in ["wf_sp_win10.exe", "wf_sp.exe", "WindConfig.exe", "wind.dll"]:
        path = tw_root / name
        data = path.read_bytes()
        hits = [needle.decode("ascii") for needle in needles if needle.lower() in data.lower()]
        indicators[name] = {"sha256": sha256_file(path), "steam_string_hits": hits}
    return indicators


def write_state(tw_root: Path, report: dict[str, object]) -> None:
    path = state_path(tw_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def apply_patch(args: argparse.Namespace) -> dict[str, object]:
    kr_root, tw_root = resolve_paths(args)
    ensure_target_layout(tw_root)
    ensure_sources(kr_root)

    running = running_wftsp_processes()
    if running and not args.dry_run:
        raise SystemExit(
            "Close the running WFTSP processes before applying files: " + ", ".join(running)
        )

    overlay_reports = [
        copy_overlay_file(kr_root, tw_root, relative_path, dry_run=args.dry_run)
        for relative_path in KR_OVERLAY_FILES
    ]
    wind_report = patch_wind_dll(tw_root, dry_run=args.dry_run)
    reg_report = set_registry_options(args.display_mode, args.width, args.height, dry_run=args.dry_run)

    report: dict[str, object] = {
        "action": "apply",
        "dry_run": args.dry_run,
        "kr_root": str(kr_root),
        "tw_root": str(tw_root),
        "overlay": overlay_reports,
        "wind_dll": wind_report,
        "registry": reg_report,
        "compatible_files": [dataclasses.asdict(item) for item in overlay_status(kr_root, tw_root, COMPATIBILITY_CHECK_FILES)],
        "steam_indicators": scan_steam_indicators(tw_root),
        "backup_dir": str(tw_root / BACKUP_DIR_NAME),
    }
    if not args.dry_run:
        write_state(tw_root, report)
    return report


def restore_patch(args: argparse.Namespace) -> dict[str, object]:
    _kr_root, tw_root = resolve_paths(args)
    backup_root = tw_root / BACKUP_DIR_NAME
    if not backup_root.exists():
        raise SystemExit(f"backup directory does not exist: {backup_root}")

    restored: list[str] = []
    for backup in backup_root.rglob("*"):
        if not backup.is_file() or backup.name == STATE_FILE_NAME:
            continue
        relative_path = str(backup.relative_to(backup_root))
        target = tw_root / relative_path
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        restored.append(relative_path)

    return {"action": "restore", "dry_run": args.dry_run, "tw_root": str(tw_root), "restored": sorted(restored)}


def status(args: argparse.Namespace) -> dict[str, object]:
    kr_root, tw_root = resolve_paths(args)
    ensure_target_layout(tw_root)
    return {
        "action": "status",
        "kr_root": str(kr_root),
        "tw_root": str(tw_root),
        "overlay": [dataclasses.asdict(item) for item in overlay_status(kr_root, tw_root, KR_OVERLAY_FILES)],
        "compatible_files": [dataclasses.asdict(item) for item in overlay_status(kr_root, tw_root, COMPATIBILITY_CHECK_FILES)],
        "wind_dll": {
            "state": wind_dll_patch_state(tw_root / "wind.dll"),
            "sha256": sha256_file(tw_root / "wind.dll"),
            "expected_tw_original_sha256": EXPECTED_TW_WIND_DLL_SHA256,
        },
        "registry": registry_status(),
        "steam_indicators": scan_steam_indicators(tw_root),
        "backup_dir_exists": (tw_root / BACKUP_DIR_NAME).exists(),
    }


def launch(args: argparse.Namespace) -> dict[str, object]:
    if not args.no_apply:
        apply_report = apply_patch(args)
    else:
        _kr_root, tw_root_for_check = resolve_paths(args)
        apply_report = {"skipped": True, "wind_dll_state": wind_dll_patch_state(tw_root_for_check / "wind.dll")}

    _kr_root, tw_root = resolve_paths(args)
    exe = tw_root / "WindConfig.exe"
    if not exe.exists():
        raise SystemExit(f"WindConfig.exe not found: {exe}")

    if args.dry_run:
        return {"action": "launch", "dry_run": True, "would_run": str(exe), "apply": apply_report}

    subprocess.Popen([str(exe)], cwd=str(tw_root))
    return {"action": "launch", "launched": str(exe), "apply": apply_report}


def print_report(report: dict[str, object]) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2))


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kr-root", type=Path, default=repo_root_from_script(), help="patched KR data root")
    parser.add_argument("--tw-root", type=Path, default=TARGET_DEFAULT, help="TW Steam Win10 game root")
    parser.add_argument(
        "--display-mode",
        choices=["fullscreen", "windowed", "borderless"],
        help="write WindConfig display mode before launch/apply",
    )
    parser.add_argument("--width", type=int, help="write WindConfig CreationWidth")
    parser.add_argument("--height", type=int, help="write WindConfig CreationHeight")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing files")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="show overlay, locale shim, registry, and Steam indicators")
    add_common_args(p_status)

    p_apply = sub.add_parser("apply", help="apply KR overlay and CP949 wind.dll patch")
    add_common_args(p_apply)

    p_restore = sub.add_parser("restore", help="restore backed-up TW files")
    add_common_args(p_restore)

    p_launch = sub.add_parser("launch", help="apply if needed, then start WindConfig.exe")
    add_common_args(p_launch)
    p_launch.add_argument("--no-apply", action="store_true", help="launch without applying files first")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.width is not None and args.width <= 0:
        raise SystemExit("--width must be positive")
    if args.height is not None and args.height <= 0:
        raise SystemExit("--height must be positive")

    if args.command == "status":
        report = status(args)
    elif args.command == "apply":
        report = apply_patch(args)
    elif args.command == "restore":
        report = restore_patch(args)
    elif args.command == "launch":
        report = launch(args)
    else:  # pragma: no cover
        raise SystemExit(f"unknown command: {args.command}")

    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
