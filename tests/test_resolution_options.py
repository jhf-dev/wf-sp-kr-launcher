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


def write_target_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in core.TARGET_EXECUTABLES:
        (root / name).write_bytes(b"placeholder")
    wind_dll = bytearray(b"\0" * 0x600)
    for offset, original, _patched, _label in core.WIND_DLL_CP949_PATCHES:
        wind_dll[offset : offset + len(original)] = original
    (root / "wind.dll").write_bytes(bytes(wind_dll))


class ResolutionOptionsTest(unittest.TestCase):
    def test_direct_launch_applies_requested_display_registry_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            write_target_layout(tw_root)
            args = argparse.Namespace(
                kr_root=temp / "KR",
                tw_root=tw_root,
                dry_run=True,
                display_mode="windowed",
                width=1280,
                height=720,
                no_apply=True,
            )

            report = core.launch_win10(args)

            self.assertEqual(
                {
                    "IsFullscreen": 0,
                    "CreationWidth": 1280,
                    "CreationHeight": 720,
                },
                report["registry"]["values"],
            )

    def test_direct_launch_does_not_write_display_options_when_exe_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            write_target_layout(tw_root)
            (tw_root / "wf_sp_win10.exe").unlink()
            args = argparse.Namespace(
                kr_root=temp / "KR",
                tw_root=tw_root,
                dry_run=True,
                display_mode="windowed",
                width=1280,
                height=720,
                no_apply=True,
            )

            with mock.patch.object(core, "set_registry_options") as set_options:
                with self.assertRaises(SystemExit):
                    core.launch_win10(args)

            set_options.assert_not_called()


if __name__ == "__main__":
    unittest.main()
