"""Offline checks for the selected core files; never fetch or import models."""
from pathlib import PurePosixPath
import re
import unittest

from scripts import musetalk_preview_sources as sources


class MuseTalkPreviewSourceTests(unittest.TestCase):
    def test_exact_core_only_allowlist_and_pinned_official_origins(self):
        self.assertEqual({record["path"] for record in sources.SRC_FILES}, {
            "MuseTalk/musetalk/models/vae.py", "MuseTalk/musetalk/models/unet.py",
            "MuseTalk/musetalk/utils/audio_processor.py", "MuseTalk/LICENSE"})
        self.assertEqual({record["path"] for record in sources.WEIGHTS}, {
            "models/musetalkV15/unet.pth", "models/musetalkV15/musetalk.json",
            "models/sd-vae/config.json", "models/sd-vae/diffusion_pytorch_model.bin",
            "models/whisper/config.json", "models/whisper/pytorch_model.bin",
            "models/whisper/preprocessor_config.json"})
        records = (*sources.SRC_FILES, *sources.WEIGHTS)
        self.assertEqual(len({record["path"] for record in records}), 11)
        for record in records:
            with self.subTest(path=record["path"]):
                self.assertFalse(PurePosixPath(record["path"]).is_absolute())
                self.assertNotIn("..", PurePosixPath(record["path"]).parts)
                self.assertRegex(record["sha256"], r"^[0-9a-f]{64}$")
                self.assertIs(type(record["bytes"]), int)
                self.assertGreater(record["bytes"], 0)
                self.assertIn(record["license"], {"MIT", "Apache-2.0"})
        for record in sources.SRC_FILES:
            self.assertEqual(record["url"], sources.SOURCE_BASE + record["path"].removeprefix("MuseTalk/"))
        for record in sources.WEIGHTS:
            self.assertRegex(record["revision"], r"^[0-9a-f]{40}$")
            self.assertRegex(record["url"], r"^https://huggingface\.co/(TMElyralab/MuseTalk|stabilityai/sd-vae-ft-mse|openai/whisper-tiny)/resolve/" + record["revision"] + "/")
            self.assertIn(record["revision"], record["license_url"])

    def test_exact_budget_and_large_checkpoint_lfs_pins(self):
        manifest = sources.input_manifest()
        self.assertEqual(manifest["file_count"], 11)
        self.assertEqual(manifest["source_bytes"], 24759)
        self.assertEqual(manifest["weight_bytes"], 3886065436)
        self.assertEqual(manifest["total_bytes"], 3886090195)
        expected = {
            "models/musetalkV15/unet.pth": (3400074924, "7ebf6c98c181e20838e4c0054e96e944ac60d5d692cc01db42839fe11b787007"),
            "models/sd-vae/diffusion_pytorch_model.bin": (334707217, "1b4889b6b1d4ce7ae320a02dedaeff1780ad77d415ea0d744b476155c6377ddc"),
            "models/whisper/pytorch_model.bin": (151095027, "9607f98a2b22d9e229ae43c52ecea79dcede9e0c5cfae67e8da6eda86d8aac1d"),
        }
        for record in sources.WEIGHTS:
            if record["path"] in expected:
                self.assertEqual((record["bytes"], record["sha256"]), expected[record["path"]])
        manifest["source_files"][0]["sha256"] = "changed"
        self.assertNotEqual(sources.SRC_FILES[0]["sha256"], "changed")

    def test_license_evidence_preserves_selected_whisper_card_and_excludes_testdata(self):
        licenses = sources.MODEL_LICENSES
        self.assertEqual(licenses["musetalk"]["license"], "MIT")
        self.assertEqual(licenses["sd-vae-ft-mse"]["license"], "MIT")
        self.assertEqual(licenses["whisper-tiny"]["license"], "Apache-2.0")
        self.assertIn("upstream", licenses["whisper-tiny"]["note"])
        self.assertIn("testdata are excluded", licenses["musetalk"]["note"])
        for record in licenses.values():
            self.assertIn(record["revision"], record["model_card_url"])
            self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", record["model_card_sha256"]))


if __name__ == "__main__":
    unittest.main()
