"""No-network regression gates for the optional Mac photo installer/worker."""
from __future__ import annotations

import plistlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import mac_scene_setup as setup
from scripts import mac_scene_worker as worker


class MacSceneSafetyTests(unittest.TestCase):
    def test_server_diagnostics_are_drained_and_bounded_without_disk_logs(self):
        stream = io.BytesIO(b"x" * 20000 + b"model load failed")
        tail = bytearray()
        worker.capture_server_errors(stream, tail)
        self.assertEqual(len(tail), 8192)
        self.assertTrue(tail.endswith(b"model load failed"))
        self.assertTrue(stream.closed)

    def test_default_invocation_is_read_only(self):
        with patch.object(setup, "check") as checker, \
             patch.object(setup, "install", side_effect=AssertionError("installed")):
            self.assertEqual(setup.main([]), 0)
            checker.assert_called_once_with()

    def test_non_mac_or_low_memory_is_refused(self):
        with patch.object(setup.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(RuntimeError, "Apple Silicon"):
                setup.require_supported_mac()
        with patch.object(setup.platform, "system", return_value="Darwin"), \
             patch.object(setup.platform, "machine", return_value="arm64"), \
             patch.object(setup, "ram_gib", return_value=8):
            with self.assertRaisesRegex(RuntimeError, "16 GiB"):
                setup.require_supported_mac()

    def test_install_cannot_skip_license_or_download_choice(self):
        with patch.object(setup, "require_supported_mac"), \
             patch.object(setup, "guard_install_paths"), \
             patch.object(setup.CHECKPOINT.__class__, "is_file", return_value=False), \
             patch.object(setup, "download_checkpoint", side_effect=AssertionError("downloaded")):
            with self.assertRaisesRegex(RuntimeError, "OpenRAIL"):
                setup.install(python_command="python3", download_model=True, accepted_license=False)
            with self.assertRaisesRegex(RuntimeError, "choose --model-path"):
                setup.install(python_command="python3", accepted_license=True)

    def test_model_url_is_official_and_checksum_gate_fails_closed(self):
        self.assertTrue(setup.MODEL_URL.startswith("https://huggingface.co/stabilityai/"))
        self.assertEqual(len(setup.MODEL_SHA256), 64)
        self.assertLessEqual(setup.MODEL_MAX_BYTES, 7_500_000_000)
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "sdxl.safetensors"
            model.write_bytes(b"not a real model")
            with self.assertRaises(RuntimeError):
                setup.verify_model(model)

    def test_launchd_is_opt_in_and_uses_bounded_worker(self):
        agent = setup.agent_payload()
        self.assertEqual(agent["Label"], setup.LABEL)
        self.assertEqual(agent["RunAtLoad"], True)
        self.assertEqual([t["Hour"] for t in agent["StartCalendarInterval"]], list(range(24)))
        self.assertIn("/opt/homebrew/bin", agent["EnvironmentVariables"]["PATH"])
        self.assertIn("CommandLineTools", agent["EnvironmentVariables"]["PATH"])
        self.assertEqual(agent["ProgramArguments"][-4:], ["--limit", "1", "--min-free-gb", "12"])
        self.assertEqual(agent["EnvironmentVariables"]["OMP_NUM_THREADS"], "2")
        self.assertEqual(agent["EnvironmentVariables"]["VECLIB_MAXIMUM_THREADS"], "2")
        self.assertEqual(agent["StandardOutPath"], "/dev/null")
        self.assertEqual(agent["StandardErrorPath"], "/dev/null")
        self.assertEqual(plistlib.loads(plistlib.dumps(agent))["Label"], setup.LABEL)

    def test_worker_never_removes_unrelated_photos_or_scripts(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            own = directory / "mihassei-staged-scene_00001_.png"
            foreign = directory / "my-vacation.png"
            jpg = directory / "article.jpg"
            own.write_bytes(b"intermediate")
            foreign.write_bytes(b"important")
            jpg.write_bytes(b"photo")
            self.assertEqual(worker.clean_owned_intermediates(directory), 1)
            self.assertFalse(own.exists())
            self.assertTrue(foreign.exists())
            self.assertTrue(jpg.exists())

    def test_worker_rejects_unbounded_jobs_without_launching(self):
        with self.assertRaisesRegex(ValueError, "Limit"):
            worker.run_once(limit=5)
        with self.assertRaisesRegex(ValueError, "floor"):
            worker.run_once(min_free_gb=3)
        with self.assertRaisesRegex(ValueError, "timeout"):
            worker.run_once(timeout=7200)
        self.assertEqual(worker.MAX_GENERATED_MIB, 1536)

    def test_storage_measure_preserves_other_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            photos = root / "assets/generated"
            photos.mkdir(parents=True)
            (photos / "one.jpg").write_bytes(b"x" * 1024)
            (photos / "not-a-photo.png").write_bytes(b"y" * 65536)
            self.assertAlmostEqual(worker.generated_mib(root), 1/1024)
            other = root / "other-important"
            other.mkdir()
            (other / "keep.txt").write_text("keep")
            self.assertTrue((other / "keep.txt").exists())

    def test_cleanup_cache_only_touches_owned_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = root / "app"
            cache = root / "cache"
            cache.mkdir()
            (cache / "temp.png").write_bytes(b"temporary")
            external = root / "keep.txt"
            external.write_text("important")
            with patch.object(setup, "APP_DIR", app), \
                 patch.object(setup, "CACHE_DIR", cache), \
                 patch.object(setup, "require_supported_mac"):
                setup.cleanup_cache()
            self.assertFalse(cache.exists())
            self.assertEqual(external.read_text(), "important")

    def test_compressed_image_budget_is_bounded(self):
        source = (Path(__file__).resolve().parents[1] / "scripts/local_scene_photos.py").read_text()
        self.assertIn("700 * 1024", source)
        self.assertIn("--min-free-gb", source)
        self.assertIn("--output-dir", source)
        self.assertIn("candidate.unlink()", source)


if __name__ == "__main__":
    unittest.main()
