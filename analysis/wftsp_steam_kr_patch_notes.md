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

The same `TextOutA` wrapper also normalizes the caller-provided text length back
to `strlen`. That breaks the original game's two-line helper text renderer,
which passes a substring length when it wraps long title/save labels. The
launcher patches that wrapper to preserve the `nCount` argument:

- `0x0534`: `mov [ebp+0x18], eax` -> `nop; nop; nop`

## Implemented Tool

`tooling/wftsp_steam_kr_launcher.py`

Commands:

- `status`: report overlay, `wind.dll` CP state, DirectDraw runtime state, observed WindConfig registry values, and Steam string indicators.
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

`WindConfig.exe` stores `IsFullscreen`, `CreationWidth`, and `CreationHeight`
under `HKCU\WindSP`, but runtime testing showed the Steam Win10 game still
enters a fixed 640x480 exclusive DirectDraw path. Static analysis of
`wf_sp_win10.exe` confirms the relevant setup:

- imports `DirectDrawCreate` from `DDRAW.dll`
- calls `SetCooperativeLevel(hwnd, 0x11)`
- calls `SetDisplayMode(640, 480, 16)`
- presents with primary-surface `BltFast`

The launcher therefore uses a local `ddraw.dll` proxy instead of relying on the
WindConfig registry for display changes.

Supported through `payload/ddraw.dll` plus `wftsp_ddraw.ini`:

- `--display-mode fullscreen`
- `--display-mode windowed`
- `--display-mode borderless`
- `--width <pixels>`
- `--height <pixels>`

`windowed` and `borderless` force `DDSCL_NORMAL`, skip `SetDisplayMode`, adjust
the game window style, and scale the original 640x480 blits into a 4:3 game
area. Windowed mode only accepts fixed 4:3 presets no larger than the current
monitor. Borderless mode keeps a monitor-sized borderless window but centers the
game in a 4:3 area, clearing the remaining side/top/bottom regions with black
color-fill so they behave as pillarbox space instead of stale primary-surface
contents.
`BltFast` scales from its source rectangle size, while `Blt` scales the
destination rectangle itself to preserve UI partial-update semantics. The proxy
also forces non-primary 640x480 offscreen surfaces to RGB565 16bpp in scaled
modes, because the game locks those surfaces and writes `ushort` pixels directly
after checking the surface `RBitMask`. `fullscreen` keeps the proxy installed
but passes the original DirectDraw exclusive path through.
Primary-surface `Blt` scaling is applied even when the source surface is null,
because the game uses `DDBLT_COLORFILL` clears to erase small UI/text regions
before redrawing menu hints and confirmation dialogs.

The game imports `SetCursorPos` and `ClipCursor` directly from `USER32.dll` and
uses 640x480 fullscreen-style coordinates. The proxy patches those imports in
scaled modes so logical cursor coordinates are mapped into the actual window
game area. Windowed `ClipCursor` requests for the full 640x480 logical area are
released instead of being mapped to the client area so the title bar, close
button, and window dragging remain reachable. Static analysis of the game window
procedure also showed that
`WM_MOUSEMOVE` stores `lParam` low/high words directly into the global cursor
position variables. In scaled modes the proxy therefore maps client mouse
messages back down to 640x480 before forwarding them to the original WndProc.

The game also drives BGM with `mciSendStringA`, including `play MUSIC from 0
notify` and `play mp3 notify from 0`. To reduce focus-return restarts without
breaking normal music changes or end-of-track looping, the proxy subclasses the
game window, records focus-return time, and rewrites only those play-from-zero
commands issued within a short focus-resume window.

Static analysis of the game window procedure showed that `WM_ACTIVATEAPP`
performs exclusive-fullscreen behavior: on deactivate it calls
`ShowWindow(hwnd, SW_SHOWMINNOACTIVE)` and stops BGM, and on activate it restores
the window and restarts BGM from the beginning. The proxy suppresses that
message path only in scaled `windowed`/`borderless` modes via
`inactive_window_spoof=1`; `fullscreen` mode leaves the original handling intact.
The scaled modes also explicitly avoid topmost placement: borderless and windowed
configuration uses `HWND_NOTOPMOST`, clears any stray `WS_EX_TOPMOST` style, and
keeps owner z-order stable so the game does not behave like an always-on-top
window while the focus workaround is active.

## Current Applied State

After `python tooling\wftsp_steam_kr_launcher.py apply`:

- Overlay files in `Wind Fantasy SP_TW` match the repaired Korean source files.
- `wind.dll` is in `cp949` state.
- TW original files are stored under:
  `Wind Fantasy SP_TW\_wftsp_kr_patch_backup`
