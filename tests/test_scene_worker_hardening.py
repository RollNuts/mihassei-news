"""Regression coverage for interrupted Mac jobs and concurrent maintenance."""
import contextlib
import fcntl
import io
import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts import mac_scene_setup as setup, mac_scene_worker as worker


class WorkerShutdownTests(unittest.TestCase):
    def test_exit_during_group_stop_still_reaps_child(self):
        process = MagicMock(pid=1234)
        process.poll.return_value = None
        with patch.object(worker.os, "killpg", side_effect=ProcessLookupError):
            worker.stop_process(process, group=True)
        process.wait.assert_called_once_with(timeout=20)

    def test_stuck_process_is_killed_and_reaped(self):
        process = MagicMock(pid=1234)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("test", 20), 0]
        with patch.object(worker.os, "killpg") as kill:
            worker.stop_process(process, group=True)
        self.assertEqual([call.args for call in kill.call_args_list],
                         [(1234, signal.SIGTERM), (1234, signal.SIGKILL)])
        self.assertEqual(process.wait.call_args.kwargs, {"timeout": 10})

    def test_sigterm_cleans_children_and_owned_png_then_records_safe_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, cache = root / "app", root / "cache"
            for relative in ("ComfyUI/main.py", "venv/bin/python", "install-ok.txt",
                             "ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors"):
                file = app / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.touch()
            output = cache / "output"
            output.mkdir(parents=True)
            keep = output / "my-photo.png"
            keep.write_bytes(b"important")
            own = output / "mihassei-staged-scene_00001_.png"
            server, sync = MagicMock(pid=1111), MagicMock(pid=2222)
            server.stderr = io.BytesIO(b"startup diagnostic")
            server.poll.return_value = sync.poll.return_value = None

            def interrupted_sleep(seconds):
                # Simulate the PNG produced during inference, then the exact
                # signal delivered by launchctl bootout/manual termination.
                own.write_bytes(b"temporary")
                os.kill(os.getpid(), signal.SIGTERM)

            previous = signal.getsignal(signal.SIGTERM)
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "APP_DIR", app), \
                 patch.object(worker, "CACHE_DIR", cache), \
                 patch.object(worker, "generated_mib", return_value=0), \
                 patch.object(worker, "storage_ok", return_value=True), \
                 patch.object(worker, "port_in_use", return_value=False), \
                 patch.object(worker, "wait_for_server"), \
                 patch.object(worker.control, "register_runtime"), \
                 patch.object(setup, "verify_model"), \
                 patch.object(worker.subprocess, "Popen", side_effect=[server, sync]), \
                 patch.object(worker.os, "killpg") as kill, \
                 patch.object(worker.time, "sleep", side_effect=interrupted_sleep), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(worker.main(["--limit", "1"]), 1)
            self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
            kill.assert_called_once_with(2222, signal.SIGTERM)
            server.terminate.assert_called_once()
            server.wait.assert_called_once_with(timeout=20)
            self.assertFalse(own.exists())
            self.assertEqual(keep.read_bytes(), b"important")
            self.assertEqual(json.loads((app / "last-run.json").read_text())["status"], "skipped")


class MaintenanceLockTests(unittest.TestCase):
    def test_model_chunk_cannot_cross_free_space_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "checkpoint.safetensors"
            response = MagicMock()
            response.__enter__.return_value = response
            response.geturl.return_value = setup.MODEL_URL
            response.headers = {"Content-Length": "8"}
            response.read.side_effect = [b"12345678", b""]
            # Four bytes above the floor cannot accommodate an eight-byte write.
            with patch.object(setup.urllib.request, "urlopen", return_value=response), \
                 patch.object(setup, "gib_free", return_value=12 + 4 / 1024**3):
                with self.assertRaisesRegex(RuntimeError, "safety floor"):
                    setup.download_checkpoint(destination)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".part").exists())

    def test_setup_and_cleanup_wait_for_existing_worker_without_deleting_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, cache = root / "app", root / "cache"
            app.mkdir()
            cache.mkdir()
            pending = cache / "setup-tmp"
            pending.mkdir()
            (pending / "download.part").write_bytes(b"in progress")
            with (app / "worker.lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(setup, "APP_DIR", app), \
                     patch.object(setup, "CACHE_DIR", cache), \
                     patch.object(setup, "require_supported_mac"), \
                     patch.object(setup, "guard_install_paths"), \
                     patch.object(setup, "gib_free", return_value=100), \
                     patch.object(setup, "candidate_python", return_value="python3.11"), \
                     patch.object(setup, "_install_runtime") as install:
                    with self.assertRaisesRegex(RuntimeError, "running"):
                        setup.install(python_command="python3.11", download_model=True,
                                      accepted_license=True)
                    install.assert_not_called()
                    with self.assertRaisesRegex(RuntimeError, "running"):
                        setup.cleanup_cache()
            self.assertEqual((pending / "download.part").read_bytes(), b"in progress")

    def test_cleanup_refuses_symlinked_app_without_touching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            other = root / "other-app"
            other.mkdir()
            app, cache = root / "app", root / "cache"
            app.symlink_to(other, target_is_directory=True)
            cache.mkdir()
            with patch.object(setup, "APP_DIR", app), \
                 patch.object(setup, "CACHE_DIR", cache), \
                 patch.object(setup, "require_supported_mac"):
                with self.assertRaisesRegex(RuntimeError, "symlinked"):
                    setup.cleanup_cache()
            self.assertTrue(cache.exists())
            self.assertEqual(list(other.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
