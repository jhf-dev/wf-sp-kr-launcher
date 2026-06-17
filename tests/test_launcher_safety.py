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
    for offset, original, _patched, _label in core.WIND_DLL_TEXTOUT_LENGTH_PATCHES:
        wind_dll[offset : offset + len(original)] = original
    (root / "wind.dll").write_bytes(bytes(wind_dll))


def write_kr_overlay_sources(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in core.KR_OVERLAY_FILES:
        (root / name).write_bytes(b"kr overlay " + name.encode("ascii"))


def make_args(tw_root: Path, kr_root: Path, **overrides) -> argparse.Namespace:
    values = {
        "kr_root": kr_root,
        "tw_root": tw_root,
        "dry_run": False,
        "display_mode": None,
        "width": None,
        "height": None,
        "no_apply": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class LauncherSafetyTest(unittest.TestCase):
    def test_apply_rejects_identical_kr_and_tw_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tw_root = Path(temp_dir) / "Wind Fantasy SP"
            write_target_layout(tw_root)
            args = make_args(tw_root, kr_root=tw_root)

            with self.assertRaises(SystemExit):
                core.apply_patch(args)

    def test_restore_refuses_while_game_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tw_root = Path(temp_dir) / "Wind Fantasy SP"
            write_target_layout(tw_root)
            (tw_root / core.BACKUP_DIR_NAME).mkdir()
            args = make_args(tw_root, kr_root=Path(temp_dir) / "KR")

            with mock.patch.object(
                core, "running_wftsp_processes", return_value=["wf_sp_win10.exe"]
            ):
                with self.assertRaises(SystemExit):
                    core.restore_patch(args)

    def test_apply_validates_wind_dll_before_writing_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            kr_root = temp / "KR"
            write_target_layout(tw_root)
            write_kr_overlay_sources(kr_root)
            (tw_root / "game.ini").write_bytes(b"tw original game.ini")
            wind_dll = bytearray((tw_root / "wind.dll").read_bytes())
            offset = core.WIND_DLL_CP949_PATCHES[0][0]
            wind_dll[offset : offset + 4] = b"\xde\xad\xbe\xef"
            (tw_root / "wind.dll").write_bytes(bytes(wind_dll))
            args = make_args(tw_root, kr_root)

            with mock.patch.object(core, "running_wftsp_processes", return_value=[]):
                with self.assertRaises(SystemExit):
                    core.apply_patch(args)

            self.assertEqual(b"tw original game.ini", (tw_root / "game.ini").read_bytes())
            self.assertFalse((tw_root / core.BACKUP_DIR_NAME).exists())

    def test_overlay_status_compares_prepared_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            kr_root = temp / "KR"
            tw_root = temp / "Wind Fantasy SP"
            kr_root.mkdir()
            tw_root.mkdir()
            (kr_root / "stage").write_bytes(b"raw stage bytes")
            (tw_root / "stage").write_bytes(b"prepared stage bytes")

            with mock.patch.object(
                core,
                "prepare_overlay_data",
                return_value=(b"prepared stage bytes", {"kind": "test"}),
            ):
                status = core.overlay_status(kr_root, tw_root, ["stage"])

            self.assertTrue(status[0].target_matches_source)
            self.assertEqual(len(b"prepared stage bytes"), status[0].size_source)

    def test_overlay_status_falls_back_to_raw_hash_when_prepare_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            kr_root = temp / "KR"
            tw_root = temp / "Wind Fantasy SP"
            kr_root.mkdir()
            tw_root.mkdir()
            (kr_root / "stage").write_bytes(b"same bytes")
            (tw_root / "stage").write_bytes(b"same bytes")

            with mock.patch.object(
                core, "prepare_overlay_data", side_effect=ValueError("bad pack")
            ):
                status = core.overlay_status(kr_root, tw_root, ["stage"])

            self.assertTrue(status[0].target_matches_source)

    def test_restore_after_repeated_apply_removes_created_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            payload = temp / "payload" / "ddraw.dll"
            write_target_layout(tw_root)
            payload.parent.mkdir()

            original_payload_path = core.ddraw_payload_path
            core.ddraw_payload_path = lambda: payload
            try:
                with mock.patch.object(core.subprocess, "Popen"):
                    payload.write_bytes(b"ddraw proxy v1")
                    core.launch_win10(
                        make_args(
                            tw_root,
                            kr_root=temp / "KR",
                            display_mode="windowed",
                            width=800,
                            height=600,
                        )
                    )
                    payload.write_bytes(b"ddraw proxy v2")
                    core.launch_win10(
                        make_args(tw_root, kr_root=temp / "KR", display_mode="borderless")
                    )
                with mock.patch.object(core, "running_wftsp_processes", return_value=[]):
                    restore_report = core.restore_patch(
                        make_args(tw_root, kr_root=temp / "KR")
                    )
            finally:
                core.ddraw_payload_path = original_payload_path

            self.assertEqual(
                ["ddraw.dll", "wftsp_ddraw.ini"],
                restore_report["removed_created_runtime"],
            )
            self.assertEqual([], restore_report["restored"])
            self.assertFalse((tw_root / "ddraw.dll").exists())
            self.assertFalse((tw_root / "wftsp_ddraw.ini").exists())
            self.assertFalse((tw_root / core.BACKUP_DIR_NAME / "ddraw.dll").exists())
            self.assertFalse(
                (tw_root / core.BACKUP_DIR_NAME / "wftsp_ddraw.ini").exists()
            )

    def test_restore_prefers_original_runtime_backup_after_missing_target_reapply(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            payload = temp / "payload" / "ddraw.dll"
            write_target_layout(tw_root)
            payload.parent.mkdir()
            (tw_root / "ddraw.dll").write_bytes(b"original ddraw dll")
            (tw_root / "wftsp_ddraw.ini").write_bytes(b"original config")

            original_payload_path = core.ddraw_payload_path
            core.ddraw_payload_path = lambda: payload
            try:
                with mock.patch.object(core.subprocess, "Popen"):
                    payload.write_bytes(b"ddraw proxy v1")
                    core.launch_win10(
                        make_args(
                            tw_root,
                            kr_root=temp / "KR",
                            display_mode="windowed",
                            width=800,
                            height=600,
                        )
                    )
                    (tw_root / "ddraw.dll").unlink()
                    (tw_root / "wftsp_ddraw.ini").unlink()

                    payload.write_bytes(b"ddraw proxy v2")
                    core.launch_win10(
                        make_args(tw_root, kr_root=temp / "KR", display_mode="borderless")
                    )
                with mock.patch.object(core, "running_wftsp_processes", return_value=[]):
                    restore_report = core.restore_patch(
                        make_args(tw_root, kr_root=temp / "KR")
                    )
            finally:
                core.ddraw_payload_path = original_payload_path

            self.assertEqual(["ddraw.dll", "wftsp_ddraw.ini"], restore_report["restored"])
            self.assertEqual([], restore_report["removed_created_runtime"])
            self.assertEqual(b"original ddraw dll", (tw_root / "ddraw.dll").read_bytes())
            self.assertEqual(
                b"original config", (tw_root / "wftsp_ddraw.ini").read_bytes()
            )

    def test_restore_keeps_runtime_file_with_unknown_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            tw_root = temp / "Wind Fantasy SP"
            payload = temp / "payload" / "ddraw.dll"
            write_target_layout(tw_root)
            payload.parent.mkdir()
            payload.write_bytes(b"ddraw proxy v1")

            original_payload_path = core.ddraw_payload_path
            core.ddraw_payload_path = lambda: payload
            try:
                with mock.patch.object(core.subprocess, "Popen"):
                    core.launch_win10(
                        make_args(
                            tw_root,
                            kr_root=temp / "KR",
                            display_mode="windowed",
                            width=800,
                            height=600,
                        )
                    )
                (tw_root / "ddraw.dll").write_bytes(b"user replaced dll")
                with mock.patch.object(core, "running_wftsp_processes", return_value=[]):
                    restore_report = core.restore_patch(
                        make_args(tw_root, kr_root=temp / "KR")
                    )
            finally:
                core.ddraw_payload_path = original_payload_path

            self.assertTrue((tw_root / "ddraw.dll").exists())
            skipped = restore_report["skipped_created_runtime"]
            self.assertEqual(1, len(skipped))
            self.assertEqual("ddraw.dll", skipped[0]["path"])


if __name__ == "__main__":
    unittest.main()
