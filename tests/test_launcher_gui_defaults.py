import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLING = ROOT / "tooling"
if str(TOOLING) not in sys.path:
    sys.path.insert(0, str(TOOLING))

import wftsp_steam_kr_launcher as core
import wftsp_steam_kr_patch_gui as gui


def write_target_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in core.TARGET_EXECUTABLES:
        (root / name).write_bytes(b"placeholder")


class LauncherGuiDefaultsTest(unittest.TestCase):
    def test_detects_wind_fantasy_sp_from_steam_libraryfolders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            steam_root = temp / "Steam"
            library = temp / "SteamLibrary"
            game_root = library / "steamapps" / "common" / "Wind Fantasy SP"
            write_target_layout(game_root)

            steamapps = steam_root / "steamapps"
            steamapps.mkdir(parents=True)
            escaped_path = str(library).replace("\\", "\\\\")
            (steamapps / "libraryfolders.vdf").write_text(
                f'"libraryfolders" {{ "0" {{ "path" "{escaped_path}" }} }}',
                encoding="utf-8",
            )

            detected = core.detect_steam_wftsp_root(steam_roots=[steam_root])

            self.assertEqual(game_root.resolve(), detected)

    def test_gui_leaves_kr_blank_and_prefills_detected_steam_path(self) -> None:
        detected_root = Path(r"C:\Steam\steamapps\common\Wind Fantasy SP")
        with mock.patch.object(gui.core, "detect_steam_wftsp_root", return_value=detected_root):
            self.assertEqual("", gui.default_kr_root())
            self.assertEqual(str(detected_root), gui.default_tw_root())

    def test_direct_win10_launch_uses_wf_sp_win10_exe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            write_target_layout(tw_root)
            args = argparse.Namespace(
                kr_root=temp / "KR",
                tw_root=tw_root,
                dry_run=True,
                display_mode=None,
                width=None,
                height=None,
                no_apply=True,
            )

            report = core.launch_win10(args)

            self.assertEqual("wf_sp_win10.exe", Path(report["would_run"]).name)
            self.assertEqual("launch_win10", report["action"])


if __name__ == "__main__":
    unittest.main()
