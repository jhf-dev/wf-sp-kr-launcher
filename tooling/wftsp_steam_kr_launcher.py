#!/usr/bin/env python3
"""Apply and launch the Korean WFTSP data overlay on the Taiwan Steam Win10 client.

The Steam build runs through ``WindConfig.exe`` and then ``wf_sp_win10.exe``.
This launcher keeps those executables intact, overlays the repaired Korean data
files, and patches the Win10 locale shim ``wind.dll`` from CP936 to CP949.
"""

from __future__ import annotations

import argparse
import ctypes
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
CREATED_RUNTIME_MARKER_NAME = "wftsp_kr_patch_created_runtime.json"
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

# The Steam Win10 TextOutA shim recalculates strlen and overwrites the nCount
# argument when the game deliberately draws only the first half of a wrapped
# string. Korean title-menu helper labels rely on that substring length.
WIND_DLL_TEXTOUT_LENGTH_PATCHES = [
    (0x0534, bytes.fromhex("89 45 18"), bytes.fromhex("90 90 90"), "TextOutA preserve caller nCount"),
]

WINDSP_REGISTRY_KEY = "WindSP"

DDRAW_CONFIG_SECTION = "wftsp_ddraw"
FONT_PROFILE_SYSTEM = "system"
FONT_PROFILE_GULIM = "gulim"
FONT_PROFILE_DOTUM = "dotum"
FONT_PROFILE_OPTIONS = (
    FONT_PROFILE_SYSTEM,
    FONT_PROFILE_GULIM,
    FONT_PROFILE_DOTUM,
)
DDRAW_RUNTIME_FILES = ("ddraw.dll", "wftsp_ddraw.ini")
STANDARD_4_3_RESOLUTIONS: tuple[tuple[int, int], ...] = (
    (640, 480),
    (800, 600),
    (1024, 768),
    (1152, 864),
    (1280, 960),
    (1400, 1050),
    (1440, 1080),
    (1600, 1200),
    (1920, 1440),
    (2048, 1536),
    (2560, 1920),
    (3200, 2400),
    (3840, 2880),
)


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
    root = repo_root_from_script()
    candidates = [root / "payload" / "ddraw.dll"]
    if getattr(sys, "frozen", False):
        candidates.append(root.parent / "payload" / "ddraw.dll")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    kr_root = args.kr_root.resolve()
    tw_root = args.tw_root.resolve()
    return kr_root, tw_root


def backup_path_for(tw_root: Path, relative_path: str) -> Path:
    return tw_root / BACKUP_DIR_NAME / relative_path


def state_path(tw_root: Path) -> Path:
    return tw_root / BACKUP_DIR_NAME / STATE_FILE_NAME


def created_runtime_marker_path(tw_root: Path) -> Path:
    return tw_root / BACKUP_DIR_NAME / CREATED_RUNTIME_MARKER_NAME


def read_created_runtime_marker(tw_root: Path) -> dict[str, list[str]]:
    """Map launcher-created runtime files to every SHA256 the launcher wrote.

    The per-apply state report only remembers the most recent apply, so repeat
    applies would otherwise lose track of which runtime files never existed in
    the original TW install.
    """
    path = created_runtime_marker_path(tw_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    marker: dict[str, list[str]] = {}
    for key, value in data.items():
        if key in DDRAW_RUNTIME_FILES and isinstance(value, list):
            marker[key] = [item for item in value if isinstance(item, str)]
    return marker


def record_created_runtime_file(tw_root: Path, relative_path: str, desired_hash: str) -> None:
    marker = read_created_runtime_marker(tw_root)
    hashes = marker.setdefault(relative_path, [])
    if desired_hash not in hashes:
        hashes.append(desired_hash)
    path = created_runtime_marker_path(tw_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def has_original_runtime_backup(
    tw_root: Path,
    relative_path: str,
    marker: dict[str, list[str]] | None = None,
) -> bool:
    backup = backup_path_for(tw_root, relative_path)
    if not backup.exists():
        return False
    known_created_hashes = (marker or read_created_runtime_marker(tw_root)).get(relative_path, [])
    return not known_created_hashes or sha256_file(backup) not in known_created_hashes


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
    return wind_dll_patch_group_state(path, WIND_DLL_CP949_PATCHES, "cp936", "cp949")


def wind_dll_textout_length_patch_state(path: Path) -> str:
    return wind_dll_patch_group_state(path, WIND_DLL_TEXTOUT_LENGTH_PATCHES, "original", "patched")


def wind_dll_patch_group_state(
    path: Path,
    patches: list[tuple[int, bytes, bytes, str]],
    original_name: str,
    patched_name: str,
) -> str:
    saw_original = False
    saw_patched = False
    for offset, original, patched, _label in patches:
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
        return patched_name
    return original_name


def ensure_wind_dll_layout(tw_root: Path) -> tuple[str, str]:
    """Validate wind.dll against the known TW Steam layout without writing."""
    path = tw_root / "wind.dll"
    state = wind_dll_patch_state(path)
    text_length_state = wind_dll_textout_length_patch_state(path)
    if state == "unexpected":
        raise SystemExit(
            "wind.dll patch offsets do not match the known TW Steam layout; "
            "refusing to patch this DLL."
        )
    if text_length_state == "unexpected":
        raise SystemExit(
            "wind.dll TextOutA length patch offsets do not match the known TW Steam layout; "
            "refusing to patch this DLL."
        )
    return state, text_length_state


def patch_wind_dll(tw_root: Path, dry_run: bool) -> dict[str, object]:
    path = tw_root / "wind.dll"
    before_hash = sha256_file(path)
    state_before, text_length_state_before = ensure_wind_dll_layout(tw_root)

    changed_offsets: list[dict[str, object]] = []
    needs_patch = state_before != "cp949" or text_length_state_before != "patched"
    if needs_patch and not dry_run:
        backup_file_once(tw_root, "wind.dll")
        data = bytearray(path.read_bytes())
        for offset, original, patched, label in [*WIND_DLL_CP949_PATCHES, *WIND_DLL_TEXTOUT_LENGTH_PATCHES]:
            current = bytes(data[offset : offset + len(original)])
            if current == original:
                data[offset : offset + len(original)] = patched
                changed_offsets.append({"offset": f"0x{offset:X}", "label": label})
            elif current == patched:
                continue
            else:
                raise SystemExit(f"unexpected wind.dll bytes at 0x{offset:X}: {current.hex(' ')}")
        path.write_bytes(bytes(data))
    elif needs_patch:
        for offset, original, _patched, label in [*WIND_DLL_CP949_PATCHES, *WIND_DLL_TEXTOUT_LENGTH_PATCHES]:
            current = read_bytes(path, offset, len(original))
            if current == original:
                changed_offsets.append({"offset": f"0x{offset:X}", "label": label})

    after_hash = sha256_file(path)
    return {
        "path": "wind.dll",
        "state_before": state_before,
        "state_after": wind_dll_patch_state(path),
        "textout_length_state_before": text_length_state_before,
        "textout_length_state_after": wind_dll_textout_length_patch_state(path),
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
        src_hash: str | None = None
        src_size: int | None = None
        if src.exists():
            src_size = src.stat().st_size
            try:
                # Compare what apply would actually write: stage/man receive
                # in-memory progression fixes before landing in the TW target.
                prepared_data, _source_patch = prepare_overlay_data(kr_root, relative_path)
            except Exception:
                prepared_data = None
            if prepared_data is not None:
                src_hash = sha256_bytes(prepared_data)
                src_size = len(prepared_data)
            else:
                src_hash = sha256_file(src)
        dst_hash = sha256_file(dst) if dst.exists() else None
        result.append(
            FileStatus(
                relative_path=relative_path,
                source_sha256=src_hash,
                target_sha256=dst_hash,
                target_matches_source=(src_hash == dst_hash) if src_hash and dst_hash else None,
                size_source=src_size,
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
    font_profile: str | None = None,
) -> dict[str, int | str] | None:
    normalized_font_profile = normalize_font_profile(font_profile)
    if display_mode is None and width is None and height is None and normalized_font_profile is None:
        return None
    if display_mode is None and normalized_font_profile is not None:
        display_mode = "fullscreen"
    if display_mode is None:
        raise SystemExit("--width/--height require --display-mode")
    if display_mode not in {"fullscreen", "windowed", "borderless"}:
        raise SystemExit(f"unsupported display mode: {display_mode}")
    if display_mode == "windowed":
        width, height = normalize_windowed_resolution(width, height)
    elif width is not None or height is not None:
        raise SystemExit("--width/--height are only supported with --display-mode windowed")
    else:
        width, height = (640, 480)
    return {
        "mode": display_mode,
        "width": width,
        "height": height,
        "debug": 0,
        "input_fix": 1,
        "audio_focus_fix": 1,
        "inactive_window_spoof": 1,
        "font_profile": normalized_font_profile or FONT_PROFILE_SYSTEM,
    }


def system_font_available(face_name: str) -> bool:
    if winreg is None:
        return False
    target = face_name.casefold()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                count = winreg.QueryInfoKey(key)[1]
                for index in range(count):
                    name, _value, _kind = winreg.EnumValue(key, index)
                    if target in name.casefold():
                        return True
        except OSError:
            continue
    return False


def normalize_font_profile(profile: str | None) -> str | None:
    if profile is None:
        return None
    if profile not in FONT_PROFILE_OPTIONS:
        raise SystemExit(f"unsupported font profile: {profile}")
    if profile == FONT_PROFILE_GULIM and not system_font_available("Gulim"):
        raise SystemExit("Gulim is not installed on this system")
    if profile == FONT_PROFILE_DOTUM and not system_font_available("Dotum"):
        raise SystemExit("Dotum is not installed on this system")
    return profile


def available_font_profiles() -> list[str]:
    profiles = [FONT_PROFILE_SYSTEM]
    if system_font_available("Gulim"):
        profiles.append(FONT_PROFILE_GULIM)
    if system_font_available("Dotum"):
        profiles.append(FONT_PROFILE_DOTUM)
    return profiles


def current_monitor_size() -> tuple[int, int]:
    try:
        user32 = ctypes.windll.user32
        return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        return (640, 480)


def available_4_3_resolutions(
    max_width: int | None = None,
    max_height: int | None = None,
) -> list[tuple[int, int]]:
    if max_width is None or max_height is None:
        max_width, max_height = current_monitor_size()
    available = [
        (width, height)
        for width, height in STANDARD_4_3_RESOLUTIONS
        if width <= max_width and height <= max_height
    ]
    return available or [(640, 480)]


def default_4_3_resolution(
    max_width: int | None = None,
    max_height: int | None = None,
) -> tuple[int, int]:
    available = available_4_3_resolutions(max_width, max_height)
    return (1024, 768) if (1024, 768) in available else available[-1]


def resolution_label(resolution: tuple[int, int]) -> str:
    return f"{resolution[0]} x {resolution[1]}"


def parse_resolution_label(value: str) -> tuple[int, int]:
    parts = value.lower().replace(" ", "").split("x", 1)
    if len(parts) != 2:
        raise ValueError(f"unsupported resolution preset: {value}")
    return int(parts[0]), int(parts[1])


def normalize_windowed_resolution(width: int | None, height: int | None) -> tuple[int, int]:
    if width is None and height is None:
        return default_4_3_resolution()
    if width is None or height is None:
        raise SystemExit("windowed mode requires both --width and --height")
    resolution = (width, height)
    if resolution not in available_4_3_resolutions():
        allowed = ", ".join(resolution_label(item) for item in available_4_3_resolutions())
        raise SystemExit(f"windowed resolution must be a 4:3 preset no larger than the current monitor: {allowed}")
    return resolution


def render_ddraw_config(config: dict[str, int | str]) -> bytes:
    lines = [
        f"[{DDRAW_CONFIG_SECTION}]",
        f"mode={config['mode']}",
        f"width={config['width']}",
        f"height={config['height']}",
        f"debug={config['debug']}",
        f"input_fix={config['input_fix']}",
        f"audio_focus_fix={config['audio_focus_fix']}",
        f"inactive_window_spoof={config['inactive_window_spoof']}",
        f"font_profile={config.get('font_profile', FONT_PROFILE_SYSTEM)}",
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
        if key in {"width", "height", "debug", "input_fix", "audio_focus_fix", "inactive_window_spoof"}:
            try:
                values[key] = int(value)
            except ValueError:
                values[key] = value
        elif key in {"mode", "font_profile"}:
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
    created_marker = read_created_runtime_marker(tw_root)
    has_original_backup = has_original_runtime_backup(tw_root, relative_path, created_marker)
    launcher_created_before = relative_path in created_marker and not has_original_backup
    should_track_as_created = not has_original_backup and (
        launcher_created_before or not target_exists_before
    )
    changed = target_hash_before != desired_hash
    if changed and not dry_run:
        # A file the launcher itself created on an earlier apply is not a TW
        # original; backing it up would make restore resurrect it later.
        if target_exists_before and not launcher_created_before:
            backup_file_once(tw_root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(desired_data)
        if should_track_as_created:
            record_created_runtime_file(tw_root, relative_path, desired_hash)
    return {
        "path": relative_path,
        "changed": changed,
        "target_exists_before": target_exists_before,
        "launcher_created": should_track_as_created,
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
    font_profile: str | None = None,
) -> dict[str, object]:
    config = normalize_display_config(display_mode, width, height, font_profile)
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
    if kr_root == tw_root:
        raise SystemExit(
            "KR source folder and TW Steam target folder must be different; "
            f"both point to: {tw_root}"
        )
    ensure_target_layout(tw_root)
    ensure_sources(kr_root)
    # Validate wind.dll before any overlay write so a layout mismatch cannot
    # leave a half-applied TW folder behind.
    ensure_wind_dll_layout(tw_root)

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
        font_profile=getattr(args, "font_profile", None),
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
    running = running_wftsp_processes()
    if running and not args.dry_run:
        raise SystemExit(
            "Close the running WFTSP processes before restoring files: " + ", ".join(running)
        )
    backup_root = tw_root / BACKUP_DIR_NAME
    if not backup_root.exists():
        raise SystemExit(f"backup directory does not exist: {backup_root}")

    created_marker = read_created_runtime_marker(tw_root)
    restored: list[str] = []
    removed_created_runtime: list[str] = []
    skipped_created_runtime: list[dict[str, object]] = []
    for backup in backup_root.rglob("*"):
        if not backup.is_file() or backup.name in {STATE_FILE_NAME, CREATED_RUNTIME_MARKER_NAME}:
            continue
        relative_path = str(backup.relative_to(backup_root))
        if relative_path in created_marker and not has_original_runtime_backup(
            tw_root, relative_path, created_marker
        ):
            # A backup of a launcher-created runtime file is a launcher
            # artifact, not a TW original; restoring it would resurrect it.
            continue
        target = tw_root / relative_path
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
        restored.append(relative_path)

    for relative_path in sorted(created_marker):
        if relative_path in restored:
            continue
        target = tw_root / relative_path
        if not target.exists():
            continue
        known_hashes = created_marker[relative_path]
        current_hash = sha256_file(target)
        if known_hashes and current_hash not in known_hashes:
            skipped_created_runtime.append(
                {
                    "path": relative_path,
                    "reason": "current file differs from launcher-created runtime file",
                    "current_sha256": current_hash,
                    "known_sha256": known_hashes,
                }
            )
            continue
        if not args.dry_run:
            target.unlink()
        removed_created_runtime.append(relative_path)

    # Legacy fallback: installs patched before the created-runtime marker
    # existed only have the last apply's state report to identify
    # launcher-created files.
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
                if (
                    relative_path not in DDRAW_RUNTIME_FILES
                    or relative_path in restored
                    or relative_path in removed_created_runtime
                    or relative_path in created_marker
                ):
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
            "textout_length_state": wind_dll_textout_length_patch_state(tw_root / "wind.dll"),
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
        apply_report = {
            "skipped": True,
            "wind_dll_state": wind_dll_patch_state(tw_root_for_check / "wind.dll"),
            "wind_dll_textout_length_state": wind_dll_textout_length_patch_state(tw_root_for_check / "wind.dll"),
        }
        display_report = install_display_runtime(
            tw_root_for_check,
            args.display_mode,
            args.width,
            args.height,
            dry_run=args.dry_run,
            font_profile=getattr(args, "font_profile", None),
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
    parser.add_argument("--width", type=int, help="windowed 4:3 preset width")
    parser.add_argument("--height", type=int, help="windowed 4:3 preset height")
    parser.add_argument(
        "--font-profile",
        choices=FONT_PROFILE_OPTIONS,
        help="font family profile for in-game GDI text",
    )
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
