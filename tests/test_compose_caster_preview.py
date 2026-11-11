"""Offline authenticity gates for reusing one existing talking-presenter run."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
from zipfile import ZipFile

from scripts import compose_caster_preview as compose


class CasterCompositionTests(unittest.TestCase):
    def test_plan_does_not_start_commands_or_network(self):
        with patch.object(compose,"execute") as execute,patch.object(compose,"command") as command,contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(compose.main(["--source-run","123"]),0)
        execute.assert_not_called();command.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())["models_downloaded"])

    def test_only_successful_manual_same_public_workflow_source_is_accepted(self):
        source={"id":123,"event":"workflow_dispatch","status":"completed","conclusion":"success",
                "path":compose.WORKFLOW,"repository":{"full_name":compose.REPOSITORY,"private":False},
                "head_repository":{"full_name":compose.REPOSITORY},"head_sha":"a"*40}
        compose.validate_source_run(source,123)
        for change in ({"conclusion":"failure"},{"status":"in_progress"},{"event":"pull_request"},
                       {"path":"other.yml"},{"repository":{"full_name":compose.REPOSITORY,"private":True}},
                       {"head_repository":{"full_name":"someone/fork"}}):
            with self.subTest(change=change),self.assertRaises(ValueError):compose.validate_source_run({**source,**change},123)

    def test_missing_digest_expired_oversized_and_other_run_artifacts_are_rejected(self):
        item={"id":456,"name":"news-caster-preview-123","expired":False,"workflow_run":{"id":123},
              "size_in_bytes":2000,"digest":"sha256:"+"a"*64}
        self.assertEqual(compose.select_artifact({"total_count":1,"artifacts":[item]},123),item)
        for change in ({"digest":None},{"expired":True},{"size_in_bytes":compose.MAX_ARTIFACT+1},{"workflow_run":{"id":789}}):
            with self.subTest(change=change),self.assertRaises(ValueError):compose.select_artifact({"artifacts":[{**item,**change}]},123)

    def test_authenticated_zip_digest_and_exact_members_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive=root/"source.zip";target=root/"unpacked";target.mkdir()
            with ZipFile(archive,"w") as file:
                for name in compose.FILES:file.writestr(name,b"source")
            artifact={"size_in_bytes":archive.stat().st_size,"digest":"sha256:"+compose.digest(archive)}
            compose.unpack_artifact(archive,target,artifact)
            self.assertEqual({p.name for p in target.iterdir()},compose.FILES)
            with self.assertRaises(ValueError):compose.unpack_artifact(archive,target,{**artifact,"digest":"sha256:"+"0"*64})
            malicious=root/"malicious.zip"
            with ZipFile(malicious,"w") as file:file.writestr("../escape",b"bad")
            with self.assertRaises(ValueError):compose.unpack_artifact(malicious,target,{"size_in_bytes":malicious.stat().st_size,"digest":"sha256:"+compose.digest(malicious)})
            self.assertFalse((root/"escape").exists())

    def test_audio_duration_rate_and_non_silence_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"narration.wav"
            with wave.open(str(path),"wb") as file:
                file.setnchannels(1);file.setsampwidth(2);file.setframerate(24000);file.writeframes(b"\x01\x00"*(24000*6))
            self.assertEqual(compose.pcm(path,24000)[0],6)
            with self.assertRaises(ValueError):compose.pcm(path,16000)
            with wave.open(str(path),"wb") as file:
                file.setnchannels(1);file.setsampwidth(2);file.setframerate(24000);file.writeframes(b"\x00\x00"*(24000*6))
            with self.assertRaises(ValueError):compose.pcm(path,24000)

    def test_receipt_cannot_rebind_an_old_article_or_other_portrait(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/"presenter-preview.mp4").write_bytes(b"video")
            portrait={"portrait_sha256":compose.PORTRAIT_SHA,"source_png_sha256":"a"*64,"generation_id":"source","fictional_person":True,"intended_age":29}
            record={"version":1,"publication":"preview_only","status":"generated_unreviewed","seconds":3,
                    "article_slug":compose.ARTICLE_SLUG,"article_sha256":compose.ARTICLE_SHA,"full_narration_text":compose.TEXT,
                    "preview_sha256":compose.digest(root/"presenter-preview.mp4"),"portrait":portrait}
            (root/"receipt.json").write_text(json.dumps(record))
            with patch.object(compose,"pcm",side_effect=[(5.85,b"audio"),(3,b"audio")]):
                self.assertEqual(compose.validate_bundle(root,portrait)[1],8)
            for change in ({"article_sha256":"0"*64},{"preview_sha256":"0"*64},{"status":"failed"},{"full_narration_text":"other"}):
                (root/"receipt.json").write_text(json.dumps({**record,**change}))
                with self.subTest(change=change),self.assertRaises(ValueError):compose.validate_bundle(root,portrait)
            (root/"receipt.json").write_text(json.dumps(record))
            with self.assertRaises(ValueError):compose.validate_bundle(root,{**portrait,"portrait_sha256":"0"*64})

    def test_existing_mac_cannot_start_composition_or_downloads(self):
        with patch.object(compose,"runner_gate",side_effect=RuntimeError("not a GitHub runner")),patch.object(compose,"binary_command") as download:
            with self.assertRaises(RuntimeError):compose.execute(123,Path("unused"))
        download.assert_not_called()


if __name__=="__main__":unittest.main()
