"""Story media reuse the accepted photograph identity, never an article key."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.photo_story_motion import validate_story_motion, copy_story_motion, generate, MAX_TOTAL_BYTES
from scripts.photo_stories import load_photo_stories

ROOT = Path(__file__).resolve().parents[1]


class StoryMotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = json.loads((ROOT / "content/photo_stories.json").read_text())
        cls.post = next(post for post in data["stories"] if "motion" in post)

    def validate(self, record, root=ROOT):
        return validate_story_motion(record, story_id=self.post["id"],
                                     image_sha256=self.post["source"]["image_sha256"], root=root)

    def test_actual_clip_and_only_published_media_are_copied(self):
        motion = self.validate(self.post["motion"])
        with tempfile.TemporaryDirectory() as folder:
            copy_story_motion(load_photo_stories(ROOT), folder, root=ROOT)
            files = [path for path in Path(folder).rglob("*") if path.is_file()]
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].name, self.post["id"] + ".mp4")
            self.assertEqual(files[0].read_bytes(), (ROOT / self.post["motion"]["path"]).read_bytes())
        self.assertEqual(motion["durationSeconds"], 12)

    def test_source_hash_codec_duration_identity_and_changed_bytes_fail_closed(self):
        record = self.post["motion"]
        for key, value in (("image_sha256", "0" * 64), ("codec", "hevc"),
                           ("duration_ms", 4000), ("bytes", 999999),
                           ("path", "assets/clips/fake-article.mp4")):
            with self.subTest(key=key):
                altered = copy.deepcopy(record); altered[key] = value
                with self.assertRaises(ValueError): self.validate(altered)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / record["path"]
            target.parent.mkdir(parents=True)
            data = bytearray((ROOT / record["path"]).read_bytes()); data[-1] ^= 1
            target.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "bytes changed"): self.validate(record, folder)
            target.unlink(); target.symlink_to(ROOT / record["path"])
            with self.assertRaisesRegex(ValueError, "Unsafe"): self.validate(record, folder)

    def test_low_disk_interruption_and_budget_preserve_story_catalog(self):
        post = copy.deepcopy(self.post)
        del post["motion"]
        source = post["source"]
        catalog = {"issues": [{"slug": source["issue_slug"], "images": {
            source["cut_id"]: {"path": "source.jpg"}}}]}
        for failure in ("disk", "interrupt", "budget"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                document = root / "content/photo_stories.json"
                document.parent.mkdir()
                if failure == "budget":
                    document.write_text('{"schema_version":1,"stories":[]}')
                    document = root / "content/photo_stories" / (post["model_id"] + ".json")
                    document.parent.mkdir()
                document.write_text(json.dumps({"schema_version": 1, "stories": [post]}))
                original = document.read_bytes()

                def encode(image, output, **options):
                    output.write_bytes(b"partial movie")
                    if failure == "interrupt":
                        raise KeyboardInterrupt()

                error = KeyboardInterrupt if failure == "interrupt" else RuntimeError
                with patch("scripts.photo_stories.load_photo_stories", return_value=[{"id": post["id"], "modelId": post["model_id"]}]), \
                     patch("scripts.photo_issues.load_photo_issues", return_value=catalog), \
                     patch("scripts.photo_story_motion.shutil.which", return_value="/unused/ffmpeg"), \
                     patch("scripts.photo_story_motion.shutil.disk_usage", return_value=SimpleNamespace(
                         free=0 if failure == "disk" else 100 * 1024**3)), \
                     patch("scripts.photo_story_motion.mac_resource_guard", return_value=None), \
                     patch("scripts.photo_story_motion.require_mac_not_stopped"), \
                     patch("scripts.photo_story_motion.movie_bytes", return_value=MAX_TOTAL_BYTES), \
                     patch("scripts.photo_story_motion.encode_clip", side_effect=encode), \
                     self.assertRaises(error):
                    generate(post["id"], root=root)
                self.assertEqual(document.read_bytes(), original)
                self.assertEqual(list(root.rglob("*.mp4")), [])


if __name__ == "__main__": unittest.main()
