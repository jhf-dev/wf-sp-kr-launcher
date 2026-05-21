# WFTSP Steam Win10 Korean Overlay Notes

## Goal

Run the repaired Korean Wind Fantasy Tactics SP data on top of the Taiwan Steam
Win10 client while preserving the Steam-era launcher/executable stack:

- `WindConfig.exe`
- `wf_sp_win10.exe`
- `wind.dll`

## Confirmed Facts

- The Taiwan Steam directory contains both `wf_sp.exe` and `wf_sp_win10.exe`,
  plus `WindConfig.exe` and `wind.dll`.
- Simple static scans of `wf_sp_win10.exe`, `wf_sp.exe`, `WindConfig.exe`, and
  `wind.dll` found no `steam`, `SteamAPI`, or `appid` strings.
- `WindConfig.exe` launches `WF_SP_win10.exe` and stores display options under
  `HKCU\WindSP`:
  - `IsFullscreen`
  - `CreationWidth`
  - `CreationHeight`
- Korean and Taiwan files with matching hashes:
  - `map`
  - `face`
  - `mapbmp`
  - `Wave`
- Korean localized overlay files that differ and are copied:
  - `game.ini`
  - `setup.ini`
  - `bmp`
  - `manbmp`
  - `manani`
  - `man`
  - `stage`

## Locale Shim Finding

`wind.dll` exports ordinal wrappers used by the Win10 executable. The relevant
wrappers force CP936 for ANSI text conversion/output paths:

- ordinal 1: `MultiByteToWideChar` wrapper
- ordinal 2: `WideCharToMultiByte` wrapper
- ordinal 3: `TextOutA` to `TextOutW` conversion path

For Korean data, the launcher patches only the six executable-code immediates
that force CP936 (`0x03A8`) to CP949 (`0x03B5`):

- `0x0410`
- `0x044B`
- `0x0490`
- `0x04DB`
- `0x0543`
- `0x058E`

The CRT codepage table entries later in the file are intentionally left
untouched.

## Implemented Tool

`tooling/wftsp_steam_kr_launcher.py`

Commands:

- `status`: report overlay, `wind.dll` CP state, registry state, and Steam string indicators.
- `apply`: back up original TW files, copy KR overlay, patch `wind.dll` to CP949.
- `restore`: restore backed-up TW files.
- `launch`: apply if needed, then start `WindConfig.exe`.

Convenience entry:

- `WFTSP_KR_Win10_Launcher.cmd`

GUI entry:

- `WFTSP_KR_Steam_Patch_GUI.exe`
- `WFTSP_KR_Steam_Patch_GUI.cmd`
- `tooling/wftsp_steam_kr_patch_gui.py`

`WFTSP_KR_Steam_Patch_GUI.exe` is the distribution entrypoint. It is built with
PyInstaller and includes the local Python/Tk runtime, so the user's machine does
not need Python installed. The `.cmd` file is only a convenience shim that
prefers the bundled exe.

The GUI lets the user select both folders:

- Korean release/source folder
- Taiwan Steam Win10 target folder

When applying, `stage` and `man` are repaired in memory before being written to
the Taiwan target. This means a clean Korean retail folder can be selected; it
does not need to already contain the ending/dialogue or FY-castle fixes.

## Display Options

Supported through `WindConfig` registry:

- `--display-mode fullscreen`
- `--display-mode windowed`
- `--width <pixels>`
- `--height <pixels>`

Deferred:

- `--display-mode borderless`

`WindConfig.exe` exposes fullscreen/windowed and resolution registry values, but
no static evidence was found for a borderless-window style toggle. Borderless
should be implemented later through a runtime window-style hook or a D3D/window
proxy if needed.

## Current Applied State

After `python tooling\wftsp_steam_kr_launcher.py apply`:

- Overlay files in `Wind Fantasy SP_TW` match the repaired Korean source files.
- `wind.dll` is in `cp949` state.
- TW original files are stored under:
  `Wind Fantasy SP_TW\_wftsp_kr_patch_backup`
