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
import re
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
STEAM_GAME_DIR_NAMES = ("Wind Fantasy SP", "Wind Fantasy SP_TW")

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

DDRAW_RUNTIME_FILES = ("ddraw.dll", "wftsp_ddraw.ini")
DDRAW_CONFIG_SECTION = "wftsp_ddraw"


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


def ddraw_payload_path() -> Path:
    return repo_root_from_script() / "payload" / "ddraw.dll"


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


def has_target_layout(tw_root: Path) -> bool:
    return all((tw_root / name).exists() for name in TARGET_EXECUTABLES)


def ensure_sources(kr_root: Path) -> None:
    missing = [name for name in KR_OVERLAY_FILES if not (kr_root / name).exists()]
    if missing:
        raise SystemExit(f"KR source is missing overlay files: {', '.join(missing)}")


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.expanduser().resolve()).lower()
        except OSError:
            key = str(path.expanduser().absolute()).lower()
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def steam_roots_from_registry() -> list[Path]:
    if winreg is None:
        return []

    locations = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", ("SteamPath", "InstallPath")),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", ("InstallPath", "SteamPath")),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", ("InstallPath", "SteamPath")),
    ]
    roots: list[Path] = []
    for hive, key_name, value_names in locations:
        try:
            with winreg.OpenKey(hive, key_name) as key:
                for value_name in value_names:
                    try:
                        value, _value_type = winreg.QueryValueEx(key, value_name)
                    except OSError:
                        continue
                    if isinstance(value, str) and value.strip():
                        roots.append(Path(value.replace("/", "\\")))
        except OSError:
            continue
    return _dedupe_paths(roots)


def default_steam_roots() -> list[Path]:
    roots = steam_roots_from_registry()
    for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value) / "Steam")
    return _dedupe_paths(roots)


_VDF_PATH_RE = re.compile(r'"path"\s*"((?:\\.|[^"\\])*)"')


def _unescape_vdf_string(value: str) -> str:
    return value.replace(r"\\", "\\").replace(r"\"", '"')


def steam_library_roots(steam_root: Path) -> list[Path]:
    roots = [steam_root]
    libraryfolders = steam_root / "steamapps" / "libraryfolders.vdf"
    if libraryfolders.exists():
        text = libraryfolders.read_text(encoding="utf-8", errors="replace")
        for match in _VDF_PATH_RE.finditer(text):
            roots.append(Path(_unescape_vdf_string(match.group(1))))
    return _dedupe_paths(roots)


def candidate_wftsp_roots(steam_root: Path) -> list[Path]:
    candidates: list[Path] = []
    if has_target_layout(steam_root):
        candidates.append(steam_root)
    for library_root in steam_library_roots(steam_root):
        for dirname in STEAM_GAME_DIR_NAMES:
            candidates.append(library_root / "steamapps" / "common" / dirname)
    return _dedupe_paths(candidates)


def detect_steam_wftsp_root(steam_roots: list[Path] | None = None) -> Path | None:
    for steam_root in steam_roots if steam_roots is not None else default_steam_roots():
        for candidate in candidate_wftsp_roots(steam_root):
            if has_target_layout(candidate):
                return candidate.resolve()
    return None


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


def normalize_display_config(
    display_mode: str | None,
    width: int | None,
    height: int | None,
) -> dict[str, int | str] | None:
    if display_mode is None and width is None and height is None:
        return None
    if display_mode is None:
        raise SystemExit("--width/--height require --display-mode")
    if display_mode not in {"fullscreen", "windowed", "borderless"}:
        raise SystemExit(f"unsupported display mode: {display_mode}")
    return {
        "mode": display_mode,
        "width": width if width is not None else 640,
        "height": height if height is not None else 480,
        "debug": 0,
        "input_fix": 1,
        "audio_focus_fix": 1,
    }


def render_ddraw_config(config: dict[str, int | str]) -> bytes:
    lines = [
        f"[{DDRAW_CONFIG_SECTION}]",
        f"mode={config['mode']}",
        f"width={config['width']}",
        f"height={config['height']}",
        f"debug={config['debug']}",
        f"input_fix={config['input_fix']}",
        f"audio_focus_fix={config['audio_focus_fix']}",
        "",
    ]
    return "\n".join(lines).encode("ascii")


def read_ddraw_config(path: Path) -> dict[str, int | str] | None:
    if not path.exists():
        return None
    values: dict[str, int | str] = {}
    for raw_line in path.read_text(encoding="ascii", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("[") or "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if key in {"width", "height", "debug", "input_fix", "audio_focus_fix"}:
            try:
                values[key] = int(value)
            except ValueError:
                values[key] = value
        elif key == "mode":
            values[key] = value
    return values


def runtime_file_report(
    tw_root: Path,
    relative_path: str,
    desired_data: bytes,
    dry_run: bool,
) -> dict[str, object]:
    target = tw_root / relative_path
    target_exists_before = target.exists()
    target_hash_before = sha256_file(target) if target_exists_before else None
    desired_hash = sha256_bytes(desired_data)
    changed = target_hash_before != desired_hash
    if changed and not dry_run:
        if target_exists_before:
            backup_file_once(tw_root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(desired_data)
    return {
        "path": relative_path,
        "changed": changed,
        "target_exists_before": target_exists_before,
        "target_sha256_before": target_hash_before,
        "target_sha256_after": desired_hash if changed and not dry_run else target_hash_before,
        "desired_sha256": desired_hash,
        "size": len(desired_data),
    }


def install_display_runtime(
    tw_root: Path,
    display_mode: str | None,
    width: int | None,
    height: int | None,
    dry_run: bool,
) -> dict[str, object]:
    config = normalize_display_config(display_mode, width, height)
    if config is None:
        return {"changed": False, "supported": True, "reason": "no display runtime options requested"}

    payload = ddraw_payload_path()
    if not payload.exists():
        raise SystemExit(
            f"DirectDraw runtime payload is missing: {payload}. "
            "Build it with tooling\\build_ddraw_proxy.py before using display options."
        )

    dll_data = payload.read_bytes()
    config_data = render_ddraw_config(config)
    file_reports = [
        runtime_file_report(tw_root, "ddraw.dll", dll_data, dry_run),
        runtime_file_report(tw_root, "wftsp_ddraw.ini", config_data, dry_run),
    ]
    return {
        "changed": any(bool(item["changed"]) for item in file_reports),
        "supported": True,
        "dry_run": dry_run,
        "payload": str(payload),
        "config": config,
        "files": file_reports,
    }


def display_runtime_status(tw_root: Path) -> dict[str, object]:
    payload = ddraw_payload_path()
    target_dll = tw_root / "ddraw.dll"
    target_config = tw_root / "wftsp_ddraw.ini"
    payload_hash = sha256_file(payload) if payload.exists() else None
    target_hash = sha256_file(target_dll) if target_dll.exists() else None
    return {
        "payload": str(payload),
        "payload_exists": payload.exists(),
        "payload_sha256": payload_hash,
        "ddraw_dll": {
            "exists": target_dll.exists(),
            "sha256": target_hash,
            "matches_payload": (payload_hash == target_hash) if payload_hash and target_hash else None,
        },
        "config": {
            "exists": target_config.exists(),
            "values": read_ddraw_config(target_config),
        },
    }


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
    display_report = install_display_runtime(
        tw_root,
        args.display_mode,
        args.width,
        args.height,
        dry_run=args.dry_run,
    )

    report: dict[str, object] = {
        "action": "apply",
        "dry_run": args.dry_run,
        "kr_root": str(kr_root),
        "tw_root": str(tw_root),
        "overlay": overlay_reports,
        "wind_dll": wind_report,
        "display_runtime": display_report,
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
    removed_created_runtime: list[str] = []
    skipped_created_runtime: list[dict[str, object]] = []
    for backup in backup_root.rglob("*"):
        if not backup.is_file() or backup.name == STATE_FILE_NAME:
            continue
        relative_path = str(backup.relative_to(backup_root))
        target = tw_root / relative_path
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        restored.append(relative_path)

    state_file = state_path(tw_root)
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        runtime_report = state.get("display_runtime") if isinstance(state, dict) else None
        if not runtime_report and isinstance(state, dict) and state.get("action") == "display_runtime":
            runtime_report = state
        if isinstance(runtime_report, dict):
            for item in runtime_report.get("files", []):
                if not isinstance(item, dict):
                    continue
                relative_path = str(item.get("path", ""))
                if relative_path not in DDRAW_RUNTIME_FILES or relative_path in restored:
                    continue
                if item.get("target_exists_before"):
                    continue
                target = tw_root / relative_path
                if not target.exists():
                    continue
                expected_hash = item.get("target_sha256_after") or item.get("desired_sha256")
                current_hash = sha256_file(target)
                if expected_hash and current_hash != expected_hash:
                    skipped_created_runtime.append(
                        {
                            "path": relative_path,
                            "reason": "current file differs from launcher-created runtime file",
                            "current_sha256": current_hash,
                            "expected_sha256": expected_hash,
                        }
                    )
                    continue
                if not args.dry_run:
                    target.unlink()
                removed_created_runtime.append(relative_path)

    return {
        "action": "restore",
        "dry_run": args.dry_run,
        "tw_root": str(tw_root),
        "restored": sorted(restored),
        "removed_created_runtime": sorted(removed_created_runtime),
        "skipped_created_runtime": skipped_created_runtime,
    }


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
        "display_runtime": display_runtime_status(tw_root),
        "windconfig_registry_observed": registry_status(),
        "steam_indicators": scan_steam_indicators(tw_root),
        "backup_dir_exists": (tw_root / BACKUP_DIR_NAME).exists(),
    }


def launch_executable(args: argparse.Namespace, executable_name: str, action: str) -> dict[str, object]:
    _kr_root, tw_root = resolve_paths(args)
    exe = tw_root / executable_name
    if not exe.exists():
        raise SystemExit(f"{executable_name} not found: {exe}")

    if not args.no_apply:
        apply_report = apply_patch(args)
        display_report = apply_report.get("display_runtime", {"changed": False})
    else:
        _kr_root, tw_root_for_check = resolve_paths(args)
        apply_report = {"skipped": True, "wind_dll_state": wind_dll_patch_state(tw_root_for_check / "wind.dll")}
        display_report = install_display_runtime(
            tw_root_for_check,
            args.display_mode,
            args.width,
            args.height,
            dry_run=args.dry_run,
        )
        if display_report.get("changed") and not args.dry_run:
            write_state(
                tw_root_for_check,
                {
                    "action": "display_runtime",
                    "dry_run": False,
                    "tw_root": str(tw_root_for_check),
                    **display_report,
                },
            )

    if args.dry_run:
        return {
            "action": action,
            "dry_run": True,
            "would_run": str(exe),
            "apply": apply_report,
            "display_runtime": display_report,
        }

    subprocess.Popen([str(exe)], cwd=str(tw_root))
    return {"action": action, "launched": str(exe), "apply": apply_report, "display_runtime": display_report}


def launch(args: argparse.Namespace) -> dict[str, object]:
    return launch_executable(args, "WindConfig.exe", "launch")


def launch_win10(args: argparse.Namespace) -> dict[str, object]:
    return launch_executable(args, "wf_sp_win10.exe", "launch_win10")


def print_report(report: dict[str, object]) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2))


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kr-root", type=Path, default=repo_root_from_script(), help="patched KR data root")
    parser.add_argument("--tw-root", type=Path, default=TARGET_DEFAULT, help="TW Steam Win10 game root")
    parser.add_argument(
        "--display-mode",
        choices=["fullscreen", "windowed", "borderless"],
        help="install DirectDraw runtime mode before launch/apply",
    )
    parser.add_argument("--width", type=int, help="windowed DirectDraw output width")
    parser.add_argument("--height", type=int, help="windowed DirectDraw output height")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing files")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_status = sub.add_parser("status", help="show overlay, locale shim, display runtime, and Steam indicators")
    add_common_args(p_status)

    p_apply = sub.add_parser("apply", help="apply KR overlay and CP949 wind.dll patch")
    add_common_args(p_apply)

    p_restore = sub.add_parser("restore", help="restore backed-up TW files")
    add_common_args(p_restore)

    p_launch = sub.add_parser("launch", help="apply if needed, then start WindConfig.exe")
    add_common_args(p_launch)
    p_launch.add_argument("--no-apply", action="store_true", help="launch without applying files first")
    p_launch_win10 = sub.add_parser("launch-win10", help="apply if needed, then start wf_sp_win10.exe directly")
    add_common_args(p_launch_win10)
    p_launch_win10.add_argument("--no-apply", action="store_true", help="launch without applying files first")
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
    elif args.command == "launch-win10":
        report = launch_win10(args)
    else:  # pragma: no cover
        raise SystemExit(f"unknown command: {args.command}")

    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
