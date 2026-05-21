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


class ResolutionOptionsTest(unittest.TestCase):
    def test_directdraw_proxy_keeps_scaled_windows_non_topmost(self) -> None:
        source = (ROOT / "tooling" / "runtime" / "ddraw_proxy.cpp").read_text(encoding="utf-8")

        self.assertIn("HWND_NOTOPMOST", source)
        self.assertIn("ensure_not_topmost", source)
        self.assertNotIn("HWND_TOP,", source)

    def test_directdraw_proxy_maps_client_mouse_messages_to_logical_area(self) -> None:
        source = (ROOT / "tooling" / "runtime" / "ddraw_proxy.cpp").read_text(encoding="utf-8")

        self.assertIn("client_mouse_lparam_to_logical", source)
        self.assertIn("game_area_client", source)
        self.assertIn("game_area_screen", source)
        self.assertIn("WM_MOUSEMOVE", source)
        self.assertIn("MulDiv(x - area.left, 640, width)", source)
        self.assertIn("MulDiv(y - area.top, 480, height)", source)
        self.assertIn("forward_lparam", source)

    def test_directdraw_proxy_clears_borderless_margins(self) -> None:
        source = (ROOT / "tooling" / "runtime" / "ddraw_proxy.cpp").read_text(encoding="utf-8")

        self.assertIn("full_client_rect_screen", source)
        self.assertIn("clear_scaled_margins", source)
        self.assertIn("DDBLT_COLORFILL", source)

    def test_directdraw_proxy_scales_primary_colorfill_blt(self) -> None:
        source = (ROOT / "tooling" / "runtime" / "ddraw_proxy.cpp").read_text(encoding="utf-8")

        self.assertIn("if (proxy->primary && scaled_mode(proxy->owner->config))", source)
        self.assertNotIn("&& real_src != NULL) {\n        RECT scaled = scale_dest_rect", source)

    def test_wind_dll_patch_preserves_textout_substring_length(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tw_root = Path(temp_dir) / "Wind Fantasy SP"
            write_target_layout(tw_root)
            wind_dll = bytearray((tw_root / "wind.dll").read_bytes())
            for offset, _original, patched, _label in core.WIND_DLL_CP949_PATCHES:
                wind_dll[offset : offset + len(patched)] = patched
            (tw_root / "wind.dll").write_bytes(bytes(wind_dll))

            report = core.patch_wind_dll(tw_root, dry_run=False)

            self.assertEqual("cp949", report["state_before"])
            self.assertEqual("original", report["textout_length_state_before"])
            self.assertEqual("patched", report["textout_length_state_after"])
            for offset, _original, patched, _label in core.WIND_DLL_TEXTOUT_LENGTH_PATCHES:
                self.assertEqual(patched, (tw_root / "wind.dll").read_bytes()[offset : offset + len(patched)])

    def test_directdraw_proxy_keeps_titlebar_outside_windowed_game_clip(self) -> None:
        source = (ROOT / "tooling" / "runtime" / "ddraw_proxy.cpp").read_text(encoding="utf-8")

        self.assertIn("windowed_mode() && full_logical_rect(rect)", source)
        self.assertIn("target = NULL", source)

    def test_display_resolution_presets_are_4_by_3_and_fit_monitor(self) -> None:
        available = core.available_4_3_resolutions(1920, 1080)

        self.assertIn((1440, 1080), available)
        self.assertNotIn((1600, 1200), available)
        self.assertNotIn((1280, 720), available)
        self.assertTrue(all(width * 3 == height * 4 for width, height in available))

    def test_windowed_display_config_rejects_non_4_by_3_resolution(self) -> None:
        with self.assertRaises(SystemExit):
            core.normalize_display_config("windowed", 1280, 720)

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
                width=1024,
                height=768,
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
            self.assertEqual(1024, report["display_runtime"]["config"]["width"])
            self.assertEqual(768, report["display_runtime"]["config"]["height"])
            self.assertEqual(payload.read_bytes(), (tw_root / "ddraw.dll").read_bytes())
            config_text = (tw_root / "wftsp_ddraw.ini").read_text(encoding="ascii")
            self.assertIn("mode=windowed", config_text)
            self.assertIn("input_fix=1", config_text)
            self.assertIn("audio_focus_fix=1", config_text)
            self.assertIn("inactive_window_spoof=1", config_text)

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
                width=1024,
                height=768,
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
