"""Offline boundaries for the explicitly selected core-only CPU pilot."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

from scripts import musetalk_profile_preview as preview


class MuseTalkProfilePreviewTests(unittest.TestCase):
    def test_plan_is_source_bound_and_does_not_download_import_models_or_execute(self):
        with patch.object(preview, "execute") as run, patch.object(preview.runtime, "urlopen") as network, \
             patch.object(preview.subprocess, "Popen") as process, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(preview.main([]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["profile_id"], "shioba-rio")
        self.assertEqual(plan["spoken_line"], "え、もうこんな時間？")
        self.assertEqual(plan["source_image_sha256"], preview.IMAGE_SHA)
        self.assertFalse(plan["publish"])
        self.assertEqual((plan["recipe"]["frames"], plan["recipe"]["fps"]), (30, 10))
        self.assertNotIn("article_sha256", plan)
        run.assert_not_called(); network.assert_not_called(); process.assert_not_called()

    def test_only_approved_short_line_and_original_photo_can_be_used(self):
        with self.assertRaisesRegex(ValueError, "explicitly supplied"):
            preview.plan(spoken_line="別の台詞を増やします。")
        with patch.object(preview, "IMAGE_SHA", "0" * 64), self.assertRaisesRegex(ValueError, "photograph changed"):
            preview.plan()

    def test_mac_is_rejected_before_download_or_output_creation(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(preview.runtime.platform, "system", return_value="Darwin"), \
             patch.object(preview.runtime, "download") as download, patch.object(preview.subprocess, "Popen") as process:
            target = Path(folder) / "never-create"
            with self.assertRaises(RuntimeError): preview.execute(target)
            self.assertFalse(target.exists())
        download.assert_not_called(); process.assert_not_called()

    def test_failure_keeps_a_small_receipt_with_exact_stage_and_source(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(preview.runtime, "runner_gate", return_value=Path(folder).resolve()), \
             patch.object(preview.runtime, "RemoteStopGate"), patch.object(preview.shutil, "which", return_value="/test/encoder"), \
             patch.object(preview.runtime, "download", side_effect=RuntimeError("fixture download stopped")):
            output = Path(folder) / "preview"
            with self.assertRaisesRegex(RuntimeError, "fixture download stopped"):
                preview.execute(output)
            receipt = json.loads((output / "receipt.json").read_text())
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["current_step"], "download_exact_core_files")
            self.assertEqual(receipt["source_image_sha256"], preview.IMAGE_SHA)
            self.assertFalse(receipt["publish"])

    def test_failed_child_retains_stderr_in_the_bounded_log(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "preview.log"
            with self.assertRaisesRegex(RuntimeError, "exit 7"):
                preview.logged_command([sys.executable, "-c", "import sys; print('small fixture error',file=sys.stderr);sys.exit(7)"],
                                       Mock(), log, env=os.environ.copy())
            self.assertIn("small fixture error", log.read_text())

    def test_saved_audio_is_identical_and_no_tts_is_installed_before_model_download(self):
        commands, downloads = [], []

        def download(url, destination, budget, **options):
            downloads.append(url)
            return {"url": url, "bytes": options["size"], "sha256": options["sha256"]}

        with tempfile.TemporaryDirectory() as folder, \
             patch.object(preview.runtime, "runner_gate", return_value=Path(folder).resolve()), \
             patch.object(preview.runtime, "RemoteStopGate"), \
             patch.object(preview.shutil, "which", return_value="/fixture/encoder"), \
             patch.object(preview.runtime, "download", side_effect=download), \
             patch.object(preview.runtime, "source_archive") as archive, \
             patch.object(preview, "logged_command", side_effect=lambda argv, *a, **k: commands.append(argv)), \
             patch.object(preview.runtime, "command", side_effect=RuntimeError("fixture stop before weights")), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "fixture stop before weights"):
                preview.execute(Path(folder) / "preview")
            waveform = (Path(folder) / "preview/narration-three-seconds.wav").read_bytes()
            self.assertEqual(hashlib.sha256(waveform).hexdigest(), preview.BASELINE_FILES["narration-three-seconds.wav"][1])
        install = next(i for i, argv in enumerate(commands) if "soundfile==0.12.1" in argv)
        imports = next(i for i, argv in enumerate(commands) if any("from transformers import WhisperModel" in arg for arg in argv))
        self.assertTrue(all(package in commands[install] for package in preview.CORE_PACKAGES))
        self.assertFalse(any(word in " ".join(map(str, commands)).lower() for word in ("kokoro", "misaki", "g2p", "synthesize")))
        archive.assert_not_called()
        self.assertLess(install, imports)
        self.assertEqual(downloads, [record["url"] for record in preview.SRC_FILES])

    def test_manual_bbox_change_preserves_the_old_absolute_rasterized_mask(self):
        recipe = preview.plan()["recipe"]
        x1, y1, x2, y2 = recipe["face_box"]
        width, height = x2 - x1, y2 - y1
        mask = recipe["mask"]
        self.assertEqual([x1 + round(mask["center"][0] * width), y1 + round(mask["center"][1] * height)], [714, 318])
        self.assertEqual([round(mask["radii"][0] * width), round(mask["radii"][1] * height)], [70, 51])
        self.assertAlmostEqual(x1 + mask["right_edge"] * width, 792)
        self.assertAlmostEqual(mask["right_feather"] * width, 15.6)
        self.assertEqual(mask["blur_sigma_px"], 8)
        self.assertIn("not DWPose", preview.plan()["comparison"]["bbox_method"])

    def test_saved_audio_or_original_receipt_changes_are_rejected_offline(self):
        source = preview.resolve_profile_input("shioba-rio", preview.SPOKEN_LINE, root=preview.ROOT)
        for name in ("narration-three-seconds.wav", "receipt.json", "presenter-preview.mp4"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                import shutil
                root = Path(folder).resolve()
                shutil.copytree(preview.ROOT / preview.AUDIO_DIR, root / preview.AUDIO_DIR)
                path = root / preview.AUDIO_DIR / name
                path.write_bytes(path.read_bytes() + b"changed")
                with self.assertRaisesRegex(ValueError, "bytes changed"):
                    preview.fixed_audio(root=root, source=source)

    def test_core_child_has_no_detector_parser_or_legacy_model_import(self):
        import ast
        source = (preview.ROOT / "scripts/musetalk_cpu_inference.py").read_text()
        tree = ast.parse(source)
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name.startswith(("insightface", "mmpose", "facexlib", "face_detection")) for name in imports))
        self.assertIn('"musetalk/models/vae.py"', source)
        self.assertIn('"musetalk/models/unet.py"', source)
        self.assertIn('"musetalk/utils/audio_processor.py"', source)
        self.assertNotIn(".half()", source)
        self.assertNotIn("cuda()", source)


if __name__ == "__main__": unittest.main()
