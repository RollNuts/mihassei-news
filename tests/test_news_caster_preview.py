"""Offline safety boundaries for a manual CPU-only presenter experiment."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import news_caster_preview as preview


class PresenterPreviewTests(unittest.TestCase):
    def test_default_is_offline_plan_with_no_download_or_subprocess(self):
        with patch.object(preview, "execute") as execute, patch.object(preview, "urlopen") as network, \
             patch.object(preview.subprocess, "Popen") as process, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(preview.main([]), 0)
        execute.assert_not_called(); network.assert_not_called(); process.assert_not_called()
        result = json.loads(output.getvalue())
        self.assertEqual(result["publication"], "preview_only")
        self.assertEqual(result["seconds"], 3)
        self.assertLess(result["timeout_seconds"], 15*60)

    def test_only_manual_public_github_hosted_linux_cpu_can_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": preview.REPOSITORY,
                   "NEWS_CASTER_PUBLIC_REPOSITORY": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                   "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_TEMP": directory}
            self.assertEqual(preview.runner_gate(env, system="Linux", python_version=(3, 10)), Path(directory).resolve())
            for key, value in (("GITHUB_ACTIONS", "false"), ("GITHUB_REPOSITORY", "other/repo"),
                               ("NEWS_CASTER_PUBLIC_REPOSITORY", "false"), ("GITHUB_EVENT_NAME", "schedule"),
                               ("GITHUB_EVENT_NAME", "pull_request"), ("RUNNER_ENVIRONMENT", "self-hosted")):
                with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                    preview.runner_gate({**env, key:value}, system="Linux", python_version=(3,10))
            with self.assertRaises(RuntimeError):preview.runner_gate(env, system="Darwin", python_version=(3,10))
            with self.assertRaises(RuntimeError):preview.runner_gate(env, system="Linux", python_version=(3,11))

    def test_real_run_on_mac_stops_before_file_changes_or_network(self):
        with patch.object(preview.platform, "system", return_value="Darwin"), \
             patch.object(preview, "download") as network, patch.object(preview.subprocess, "Popen") as process:
            with self.assertRaises(RuntimeError):
                preview.execute(Path("absent.jpg"), Path("absent.json"), Path("never-create"))
        network.assert_not_called(); process.assert_not_called()

    @patch.object(preview,"jpeg_dimensions",return_value=(1024,1024))
    def test_portrait_requires_exact_adult_fictional_preview_receipt(self, dimensions):
        with tempfile.TemporaryDirectory() as directory:
            image, receipt = Path(directory)/"portrait.jpg", Path(directory)/"portrait.json"
            image.write_bytes(b"\xff\xd8" + b"x"*2048 + b"\xff\xd9")
            baseline={"version":1,"portrait_sha256":preview.digest(image),"fictional_person":True,
                      "intended_age":29,"publication":"preview_only", "source_png_sha256":"a"*64,
                      "generation_id":"00000000-0000-0000-0000-000000000000", "generation_id_source":"test receipt",
                      "generation_receipt":"exec-example.png", "generation_prompt":"Fictional adult newsreader"}
            receipt.write_text(json.dumps(baseline));self.assertEqual(preview.validate_portrait(image,receipt),baseline)
            for change in ({"fictional_person":False},{"intended_age":17},{"intended_age":True},
                           {"publication":"published"},{"portrait_sha256":"0"*64}):
                receipt.write_text(json.dumps({**baseline,**change}))
                with self.subTest(change=change),self.assertRaises(ValueError):preview.validate_portrait(image,receipt)

    def test_failed_artifact_cap_only_removes_owned_partial_media(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)
            movie=output/"presenter-preview.mp4";movie.write_bytes(b"x"*preview.MAX_ARTIFACT)
            other=output/"unrelated-file";other.write_text("retain")
            self.assertEqual(preview.cap_partial_artifacts(output),[movie.name])
            self.assertTrue(other.is_file())

    def test_low_disk_and_deadline_stop_before_model_work(self):
        with tempfile.TemporaryDirectory() as directory:
            budget=preview.Budget(Path(directory))
            with patch.object(preview.shutil,"disk_usage",return_value=Mock(free=preview.MIN_FREE-1)):
                with self.assertRaisesRegex(RuntimeError,"12 GiB"):budget.check()
            budget.deadline=0
            with self.assertRaisesRegex(RuntimeError,"deadline"):budget.check()

    def test_existing_all_stop_and_unknown_state_fail_closed(self):
        for state, code in (("disabled_manually",0),("",1),("unknown",0)):
            with self.subTest(state=state), patch.object(preview.subprocess,"run",return_value=Mock(returncode=code,stdout=state)):
                with self.assertRaises(RuntimeError):preview.RemoteStopGate().check()
        with patch.object(preview.subprocess,"run",return_value=Mock(returncode=0,stdout="active\n")) as request:
            gate=preview.RemoteStopGate();gate.check();gate.check()
            request.assert_called_once()

    def test_cancellation_targets_only_its_own_child_process_group(self):
        process=Mock(pid=123,poll=Mock(return_value=None))
        with patch.object(preview.os,"getpgid",return_value=123),patch.object(preview.os,"killpg") as kill:
            preview.stop_child(process)
        kill.assert_called_once_with(123,preview.signal.SIGTERM)
        with patch.object(preview.os,"getpgid",return_value=999),patch.object(preview.os,"killpg") as kill:
            preview.stop_child(process)
        kill.assert_not_called()

    def test_pinned_hash_rejects_corrupt_download_without_retries(self):
        response=Mock(url="https://huggingface.co/author/model/resolve/revision/weights")
        response.read.side_effect=[b"bad",b""]
        source=Mock();source.__enter__=Mock(return_value=response);source.__exit__=Mock(return_value=False)
        with tempfile.TemporaryDirectory() as directory,patch.object(preview,"urlopen",return_value=source) as network:
            with self.assertRaises(ValueError):
                preview.download(response.url,Path(directory)/"weights",Mock(),size=3,sha256=hashlib.sha256(b"good").hexdigest())
            network.assert_called_once()
        for url in ("http://github.com/unsafe","https://github.com.evil.invalid/weights","https://api.openai.com/v1/images"):
            self.assertFalse(preview.allowed_url(url))

    def test_source_archive_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def archive(url,destination,budget,**kwargs):
                with tarfile.open(destination,"w:gz") as file:
                    member=tarfile.TarInfo("repo/../../escape");member.size=3;file.addfile(member,io.BytesIO(b"bad"))
                return {}
            with patch.object(preview,"download",side_effect=archive),self.assertRaises(ValueError):
                preview.source_archive("official/repo","a"*40,root,Mock())
            self.assertFalse((root.parent/"escape").exists())


class CasterSourcePublicationTests(unittest.TestCase):
    def test_preview_portrait_and_receipt_are_excluded_from_public_build(self):
        from tests.site_build_fixture import ensure_site_build

        ensure_site_build()
        self.assertTrue((preview.ROOT / "assets/casters/newsreader-preview.jpg").is_file())
        self.assertTrue((preview.ROOT / "assets/casters/newsreader-preview.json").is_file())
        self.assertFalse((preview.ROOT / "dist/assets/casters").exists())


if __name__ == "__main__":unittest.main()
