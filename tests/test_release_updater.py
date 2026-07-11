import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLING = ROOT / "tooling"
if str(TOOLING) not in sys.path:
    sys.path.insert(0, str(TOOLING))

import wftsp_release_updater as updater
import wftsp_steam_kr_patch_gui as gui


def write_version(root: Path, version: str = "v1") -> None:
    (root / updater.VERSION_FILE).write_text(
        json.dumps({
            "version": version,
            "repository": "owner/repo",
            "asset_pattern": "package",
            "launcher": updater.DEFAULT_LAUNCHER,
            "updater": "WFTSP_KR_Steam_Patch_Updater.exe",
        }),
        encoding="utf-8",
    )


class ReleaseUpdaterTest(unittest.TestCase):
    def test_date_release_tags_do_not_allow_downgrade(self) -> None:
        self.assertTrue(updater.is_remote_newer("beta-2026-05-22-v1", "beta-2026-07-11-v1"))
        self.assertFalse(updater.is_remote_newer("beta-2026-07-11-v1", "beta-2026-05-22-v1"))

    def test_selects_matching_zip_asset(self) -> None:
        release = {"assets": [
            {"name": "other.zip"},
            {"name": "WFTSP_package.zip", "browser_download_url": "https://example.test/a"},
        ]}
        selected = updater.select_release_asset(release, "WFTSP_package")
        self.assertEqual("WFTSP_package.zip", selected["name"])

    def test_safe_extract_rejects_parent_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escape.txt", "bad")
            with self.assertRaises(RuntimeError):
                updater.safe_extract_zip(archive, root / "candidate")

    def test_validate_candidate_requires_matching_tag_and_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_version(root, "v2")
            (root / updater.DEFAULT_LAUNCHER).write_bytes(b"exe")
            (root / "payload").mkdir()
            (root / "payload" / "ddraw.dll").write_bytes(b"dll")
            self.assertEqual("v2", updater.validate_candidate(root, "v2").version)

    def test_apply_candidate_replaces_only_managed_files_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            bundle = temp / "bundle"
            candidate = temp / "candidate"
            bundle.mkdir()
            candidate.mkdir()
            write_version(bundle, "v1")
            write_version(candidate, "v2")
            (bundle / updater.DEFAULT_LAUNCHER).write_bytes(b"old")
            (candidate / updater.DEFAULT_LAUNCHER).write_bytes(b"new")
            (bundle / "user-note.txt").write_text("keep", encoding="utf-8")
            backup = updater.apply_candidate(candidate, bundle)
            self.assertEqual(b"new", (bundle / updater.DEFAULT_LAUNCHER).read_bytes())
            self.assertEqual(b"old", (backup / updater.DEFAULT_LAUNCHER).read_bytes())
            self.assertEqual("keep", (bundle / "user-note.txt").read_text(encoding="utf-8"))

    def test_source_launcher_builds_python_updater_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "tooling").mkdir()
            (root / "tooling" / "wftsp_release_updater.py").write_text("", encoding="utf-8")
            command = gui.updater_command(root, 123)
            self.assertEqual(sys.executable, command[0])
            self.assertIn("--restart", command)
            self.assertIn("123", command)


if __name__ == "__main__":
    unittest.main()
