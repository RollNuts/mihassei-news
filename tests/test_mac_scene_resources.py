"""Mac load deferral and owned-worker cleanup without allocating GPU memory."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import mac_scene_resources as resources
from scripts import mac_scene_worker as worker
from scripts import mac_scene_setup as setup


class ResourceTests(unittest.TestCase):
    def snapshot(self, *, pressure="1", swap="1024.0M", free=65,
                 thermal="Note: No thermal warning level has been recorded"):
        values = iter([pressure, f"total = 4096.00M used = {swap} free = 3072.00M",
                       f"System-wide memory free percentage: {free}%", thermal])
        with patch.object(resources.platform, "system", return_value="Darwin"), \
             patch.object(resources, "probe", side_effect=lambda *args: next(values)):
            return resources.sample()

    def test_healthy_resources_and_no_recorded_thermal_warning_allow_work(self):
        result = self.snapshot()
        self.assertTrue(result["allowed"])
        self.assertEqual(result["swap_gib"], 1)
        self.assertEqual(result["free_percent"], 65)

    def test_swap_limit_and_low_memory_defer_even_without_pressure_warning(self):
        self.assertFalse(self.snapshot(swap="4096.00M")["allowed"])
        self.assertFalse(self.snapshot(free=19)["allowed"])
        self.assertTrue(self.snapshot(swap="3.99G", free=20)["allowed"])

    def test_pressure_and_thermal_throttling_defer(self):
        for level in ("2", "4"):
            self.assertIn("memory pressure", self.snapshot(pressure=level)["reason"])
        for output in ("CPU_Speed_Limit = 70", "CPU_Scheduler_Limit = 50",
                       "GPU_Speed_Limit = 79", "Thermal_Level = 1"):
            self.assertFalse(self.snapshot(thermal=output)["allowed"])

    def test_invalid_or_unavailable_probes_fail_closed(self):
        for result in (self.snapshot(pressure="9"), self.snapshot(swap="unreadable"),
                       self.snapshot(free=101), self.snapshot(thermal="unrecognized")):
            self.assertFalse(result["allowed"])
            self.assertIn("probe_error", result)
        with patch.object(resources.platform, "system", return_value="Darwin"), \
             patch.object(resources, "probe", side_effect=subprocess.TimeoutExpired("probe", 2)):
            self.assertFalse(resources.sample()["allowed"])

    def test_probes_have_hard_deadline_and_fixed_executable(self):
        with patch.object(resources.subprocess, "run", return_value=Mock(stdout="1")) as run:
            resources.probe("/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level")
        self.assertEqual(run.call_args.args[0][0], "/usr/sbin/sysctl")
        self.assertEqual(run.call_args.kwargs["timeout"], 2)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_polling_is_rate_limited_and_later_pressure_is_not_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            guard = resources.Guard(app)
            healthy = {"allowed": True, "reason": "healthy"}
            unsafe = {"allowed": False, "reason": "pressure"}
            clock = [0.0]
            with patch.object(resources.time, "monotonic", side_effect=lambda: clock[0]), \
                 patch.object(resources, "sample", side_effect=[healthy, unsafe]) as sample:
                guard.check()
                for second in (2, 4, 6, 8):
                    clock[0] = second
                    guard.check()
                self.assertEqual(sample.call_count, 1)
                clock[0] = 10
                with self.assertRaises(resources.ResourceDeferred): guard.check()
            self.assertEqual(json.loads((app / "resources.json").read_text()), unsafe)
            self.assertEqual(list(app.glob(".resources-*")), [])

    def test_guard_preserves_file_behind_foreign_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)
            important = app / "important.txt"
            important.write_text("keep")
            (app / "resources.json").symlink_to(important)
            with patch.object(resources, "sample", return_value={"allowed": True, "reason": "ok"}):
                with self.assertRaisesRegex(RuntimeError, "Unsafe"):
                    resources.Guard(app).check()
            self.assertEqual(important.read_text(), "keep")

    def test_resource_pause_keeps_success_and_real_error_history(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(worker, "APP_DIR", Path(temp)):
            worker.update_status("ok", "published")
            before = json.loads((Path(temp) / "last-run.json").read_text())
            worker.update_status("skipped", "network failed")
            worker.update_status("resource-paused", "swap high")
            after = json.loads((Path(temp) / "last-run.json").read_text())
            self.assertEqual(after["last_success_at"], before["last_success_at"])
            self.assertEqual(after["last_error"]["detail"], "network failed")

    def worker_context(self, stack, app, cache, guard):
        for file in (app / "venv/bin/python", app / "ComfyUI/main.py",
                     app / "ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors", app / "install-ok.txt"):
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("")
        cache.mkdir()
        for obj, name, value in ((worker, "APP_DIR", app), (worker, "CACHE_DIR", cache)):
            stack.enter_context(patch.object(obj, name, value))
        stack.enter_context(patch.object(worker, "storage_ok", return_value=True))
        stack.enter_context(patch.object(worker.resources, "Guard", return_value=guard))
        stack.enter_context(patch.object(worker.control, "check_allowed"))
        stack.enter_context(patch.object(worker.control, "register_runtime"))
        stack.enter_context(patch.object(worker.control, "clear_runtime"))
        stack.enter_context(patch.object(worker, "port_in_use", return_value=False))

    def test_preflight_deferral_starts_neither_model_verification_nor_server(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            app, cache = Path(temp) / "app", Path(temp) / "cache"
            guard = Mock()
            guard.check.side_effect = resources.ResourceDeferred("swap high")
            self.worker_context(stack, app, cache, guard)
            verify = stack.enter_context(patch.object(setup, "verify_model"))
            spawn = stack.enter_context(patch.object(worker.subprocess, "Popen"))
            with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(worker.main([]), 0)
            spawn.assert_not_called()
            verify.assert_not_called()
            self.assertEqual(json.loads((app / "last-run.json").read_text())["status"], "resource-paused")

    def test_runtime_pressure_cleans_up_only_owned_children_and_preserves_outbox(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.ExitStack() as stack:
            app, cache = Path(temp) / "app", Path(temp) / "cache"
            guard = Mock()
            guard.check.side_effect = [None, None, None, resources.ResourceDeferred("swap high")]
            self.worker_context(stack, app, cache, guard)
            (app / "pending").mkdir()
            (app / "pending/one.jpg").write_bytes(b"saved image")
            stack.enter_context(patch.object(setup, "verify_model"))
            stack.enter_context(patch.object(worker, "wait_for_server", side_effect=lambda p, guard: guard.check()))
            stack.enter_context(patch.object(worker.threading, "Thread"))
            server = Mock(pid=100, stderr=io.BytesIO(), poll=Mock(return_value=None))
            sync = Mock(pid=101, poll=Mock(return_value=None))
            spawn = stack.enter_context(patch.object(worker.subprocess, "Popen", side_effect=[server, sync]))
            stop = stack.enter_context(patch.object(worker, "stop_process"))
            stack.enter_context(patch.dict(os.environ, {"PYTORCH_MPS_HIGH_WATERMARK_RATIO": "0"}))
            with contextlib.redirect_stderr(io.StringIO()): self.assertEqual(worker.main([]), 0)
            self.assertEqual(stop.call_args_list[0].args[0], sync)
            self.assertTrue(stop.call_args_list[0].kwargs["group"])
            self.assertEqual(stop.call_args_list[1].args[0], server)
            self.assertEqual(stop.call_count, 2)
            self.assertEqual((app / "pending/one.jpg").read_bytes(), b"saved image")
            environment = spawn.call_args_list[0].kwargs["env"]
            self.assertEqual(environment["PYTORCH_MPS_HIGH_WATERMARK_RATIO"], "0.8")
            self.assertEqual(environment["PYTORCH_MPS_LOW_WATERMARK_RATIO"], "0.7")
            self.assertEqual(environment["OMP_NUM_THREADS"], "2")


if __name__ == "__main__": unittest.main()
