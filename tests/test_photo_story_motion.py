"""Story media reuse the accepted photograph identity, never an article key."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.photo_story_motion import validate_story_motion, copy_story_motion
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


if __name__ == "__main__": unittest.main()
