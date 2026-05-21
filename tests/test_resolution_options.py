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
    def test_direct_launch_installs_windowed_directdraw_runtime_without_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            payload = temp / "payload" / "ddraw.dll"
            write_target_layout(tw_root)
            payload.parent.mkdir()
            payload.write_bytes(b"ddraw proxy")
            args = argparse.Namespace(
                kr_root=temp / "KR",
                tw_root=tw_root,
                dry_run=False,
                display_mode="windowed",
                width=1280,
                height=720,
                no_apply=True,
            )

            original_payload_path = core.ddraw_payload_path
            core.ddraw_payload_path = lambda: payload
            try:
                with mock.patch.object(core.subprocess, "Popen"):
                    report = core.launch_win10(args)
            finally:
                core.ddraw_payload_path = original_payload_path

            self.assertNotIn("registry", report)
            self.assertEqual("windowed", report["display_runtime"]["config"]["mode"])
            self.assertEqual(1280, report["display_runtime"]["config"]["width"])
            self.assertEqual(720, report["display_runtime"]["config"]["height"])
            self.assertEqual(payload.read_bytes(), (tw_root / "ddraw.dll").read_bytes())
            self.assertIn("mode=windowed", (tw_root / "wftsp_ddraw.ini").read_text(encoding="ascii"))

    def test_direct_launch_dry_run_reports_borderless_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            payload = temp / "payload" / "ddraw.dll"
            write_target_layout(tw_root)
            payload.parent.mkdir()
            payload.write_bytes(b"ddraw proxy")
            args = argparse.Namespace(
                kr_root=temp / "KR",
                tw_root=tw_root,
                dry_run=True,
                display_mode="borderless",
                width=None,
                height=None,
                no_apply=True,
            )

            original_payload_path = core.ddraw_payload_path
            core.ddraw_payload_path = lambda: payload
            try:
                report = core.launch_win10(args)
            finally:
                core.ddraw_payload_path = original_payload_path

            self.assertEqual("borderless", report["display_runtime"]["config"]["mode"])
            self.assertTrue(report["display_runtime"]["dry_run"])
            self.assertFalse((tw_root / "ddraw.dll").exists())
            self.assertFalse((tw_root / "wftsp_ddraw.ini").exists())

    def test_direct_launch_does_not_write_display_runtime_when_exe_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            write_target_layout(tw_root)
            (tw_root / "wf_sp_win10.exe").unlink()
            args = argparse.Namespace(
                kr_root=temp / "KR",
                tw_root=tw_root,
                dry_run=False,
                display_mode="windowed",
                width=1280,
                height=720,
                no_apply=True,
            )

            with self.assertRaises(SystemExit):
                core.launch_win10(args)

            self.assertFalse((tw_root / "ddraw.dll").exists())
            self.assertFalse((tw_root / "wftsp_ddraw.ini").exists())

    def test_restore_removes_launcher_created_display_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            payload = temp / "payload" / "ddraw.dll"
            write_target_layout(tw_root)
            payload.parent.mkdir()
            payload.write_bytes(b"ddraw proxy")
            args = argparse.Namespace(
                kr_root=temp / "KR",
                tw_root=tw_root,
                dry_run=False,
                display_mode="windowed",
                width=800,
                height=600,
                no_apply=True,
            )

            original_payload_path = core.ddraw_payload_path
            core.ddraw_payload_path = lambda: payload
            try:
                with mock.patch.object(core.subprocess, "Popen"):
                    core.launch_win10(args)
                restore_report = core.restore_patch(args)
            finally:
                core.ddraw_payload_path = original_payload_path

            self.assertEqual(["ddraw.dll", "wftsp_ddraw.ini"], restore_report["removed_created_runtime"])
            self.assertFalse((tw_root / "ddraw.dll").exists())
            self.assertFalse((tw_root / "wftsp_ddraw.ini").exists())


if __name__ == "__main__":
    unittest.main()
