"""Offline authenticity gates for reusing one existing talking-presenter run."""
import contextlib
from fractions import Fraction
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave
from zipfile import ZipFile

from scripts import compose_caster_preview as compose

PINNED = "a" * 40 + ":drafts/caster-review/night-art-20261113/manifest.json:" + "b" * 64


def retained_probe():
    return {"streams": [{"codec_type":"video","codec_name":"h264","width":1280,"height":720,
                         "avg_frame_rate":"25/1","nb_frames":"200","index":0,"time_base":"1/12800",
                         "start_time":"0.000000","duration":"8.000000"},
                        {"codec_type":"audio","codec_name":"aac","channels":1,"sample_rate":"24000",
                         "index":1,"time_base":"1/24000","start_time":"0.000000","duration":"8.000000"}],
            "format":{"duration":"8.000000"}}


def retained_packets():
    return {"packets":[{"stream_index":0,"pts":frame*512} for frame in range(200)]+
                      [{"stream_index":1,"pts":-1024,"dts":-1024,"side_data_list":[
                          {"side_data_type":"Skip Samples","skip_samples":1024,"discard_padding":0}]}]}


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

    def test_retained_plan_and_mac_guard_never_download_or_encode(self):
        with patch.object(compose,"execute") as execute,patch.object(compose,"command") as command,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compose.main(["--source-manifest",PINNED]),0)
        execute.assert_not_called();command.assert_not_called()
        with patch.object(compose,"runner_gate",side_effect=RuntimeError("Mac forbidden")),patch.object(compose,"repository_file") as download:
            with self.assertRaises(RuntimeError):compose.execute(None,Path("unused"),PINNED)
        download.assert_not_called()

    def test_stopped_or_unknown_cpu_blocks_retained_download_and_encoding(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(compose,"runner_gate",return_value=Path(directory)), \
             patch.object(compose.RemoteStopGate,"check",side_effect=RuntimeError("CPU status unavailable")), \
             patch.object(compose,"current_assets") as assets,patch.object(compose,"repository_file") as download,patch.object(compose,"command") as command:
            with self.assertRaises(RuntimeError):compose.execute(None,Path(directory)/"output",PINNED)
            assets.assert_not_called();download.assert_not_called();command.assert_not_called()

    def test_retained_manifest_rejects_unpinned_url_traversal_and_injection_before_download(self):
        for value in (PINNED.replace("a"*40,"main"),PINNED.replace("drafts/caster-review/","../"),
                      PINNED.replace("manifest.json","../manifest.json"),"https://example.org/movie.mp4",
                      PINNED+";echo bad",PINNED.replace("night-art-20261113","night%2fescape")):
            with self.subTest(value=value),patch.object(compose,"repository_file") as download:
                with self.assertRaises(ValueError):compose.retained_composition(value,Path("unused"),Mock())
                download.assert_not_called()

    def test_repository_source_requires_size_regular_path_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"source";payload=b"original source"
            def download(argv,target,budget,**kwargs):target.write_bytes(payload)
            metadata={"type":"file","path":"drafts/source","size":len(payload)}
            with patch.object(compose,"command",return_value=json.dumps(metadata)),patch.object(compose,"binary_command",side_effect=download):
                compose.repository_file("a"*40,"drafts/source",hashlib.sha256(payload).hexdigest(),path,Mock(),100)
                with self.assertRaises(ValueError):compose.repository_file("a"*40,"drafts/source","0"*64,path,Mock(),100)
            for change in ({"type":"symlink"},{"path":"other"},{"size":101},{"size":0},{"size":True}):
                with self.subTest(change=change),patch.object(compose,"command",return_value=json.dumps({**metadata,**change})),patch.object(compose,"binary_command") as download:
                    with self.assertRaises(ValueError):compose.repository_file("a"*40,"drafts/source","0"*64,path,Mock(),100)
                    download.assert_not_called()

    def test_retained_manifest_binds_original_receipt_and_both_successful_manual_runs(self):
        manifest={"version":1,"composition_run_id":compose.COMPOSITION_RUN,"movie_sha256":compose.COMPOSITION_SHA,
                  "receipt_sha256":compose.COMPOSITION_RECEIPT_SHA}
        original={"version":1,"publication":"preview_only","status":"composed_unreviewed","source_run_id":42928393181,
                  "article_slug":compose.ARTICLE_SLUG,"article_sha256":compose.ARTICLE_SHA,"photo_sha256":compose.PHOTO_SHA,
                  "portrait_sha256":compose.PORTRAIT_SHA,"narration":compose.TEXT,"duration_seconds":8,
                  "sha256":compose.COMPOSITION_SHA,"models_downloaded":False,"models_executed":False,"source_head_sha":"c"*40}
        def source(argv,budget,**kwargs):
            return json.dumps({"id":int(argv[-1].rsplit("/",1)[-1]),"event":"workflow_dispatch","status":"completed",
                               "conclusion":"success","path":compose.WORKFLOW,"head_sha":"c"*40,
                               "repository":{"full_name":compose.REPOSITORY,"private":False},
                               "head_repository":{"full_name":compose.REPOSITORY}})
        def check(manifest_change=None,receipt_change=None):
            def download(ref,path,sha,target,budget,limit):
                data=({**manifest,**(manifest_change or {})} if path.endswith("manifest.json") else
                      {**original,**(receipt_change or {})} if path.endswith("receipt.json") else None)
                target.write_text(json.dumps(data) if data else "movie")
            with tempfile.TemporaryDirectory() as directory,patch.object(compose,"repository_file",side_effect=download) as files,patch.object(compose,"command",side_effect=source):
                movie,receipt,provenance=compose.retained_composition(PINNED,Path(directory),Mock())
                self.assertEqual(receipt,original);self.assertEqual(movie.name,compose.MOVIE)
                self.assertEqual(provenance["source_original_receipt_sha256"],compose.COMPOSITION_RECEIPT_SHA)
                self.assertEqual(files.call_args_list[1].args[2],compose.COMPOSITION_SHA)
        check()
        for change in ({"version":True},{"movie_sha256":"0"*64},{"composition_run_id":1},{"extra_url":"https://example.org"}):
            with self.subTest(change=change),self.assertRaises(ValueError):check(manifest_change=change)
        for change in ({"article_sha256":"0"*64},{"narration":"new voice"},{"models_executed":True},
                       {"source_head_sha":"d"*40},{"status":"accepted"}):
            with self.subTest(change=change),self.assertRaises(ValueError):check(receipt_change=change)

    def test_retained_probe_rejects_nonfinite_changed_duration_and_extra_streams(self):
        compose.validate_retained_probe(retained_probe())
        for duration in ("nan","inf","-inf","7.9","8.1"):
            with self.subTest(duration=duration),self.assertRaises(ValueError):
                compose.validate_retained_probe({**retained_probe(),"format":{"duration":duration}})
        for change in ({"avg_frame_rate":"24/1"},{"nb_frames":"199"},{"codec_name":"hevc"},{"width":640}):
            probe=retained_probe();probe["streams"][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):compose.validate_retained_probe(probe)
        probe=retained_probe();probe["streams"].append({"codec_type":"subtitle"})
        with self.assertRaises(ValueError):compose.validate_retained_probe(probe)
        for index in (0,1):
            for change in ({"start_time":"nan"},{"start_time":"0.05"},{"duration":"inf"},{"duration":"7.9"}):
                probe=retained_probe();probe["streams"][index].update(change)
                with self.subTest(index=index,change=change),self.assertRaises(ValueError):compose.validate_retained_probe(probe)

    def test_packet_timing_preserves_cut_frames_aac_priming_and_sync(self):
        with patch.object(compose,"command",return_value=json.dumps(retained_packets())):
            timing=compose.packet_timing(Path("source.mp4"),retained_probe(),Mock())
        proof=compose.compare_packet_timing(timing,timing)
        self.assertEqual(proof["cut_frame_times_seconds"],[2.96,3.0])
        self.assertEqual(proof["audio_skip_samples"],1024)
        for change in ({"audio_pts":timing["audio_pts"]+Fraction(2,24000)},
                       {"audio_dts":timing["audio_dts"]-Fraction(2,24000)},
                       {"skip_samples":0},{"discard_padding":1},{"video_pts":timing["video_pts"][:-1]}):
            with self.subTest(change=change),self.assertRaises(ValueError):compose.compare_packet_timing(timing,{**timing,**change})
        for field in ("pts","dts"):
            packets=retained_packets();packets["packets"][-1][field]="nan"
            with self.subTest(field=field),patch.object(compose,"command",return_value=json.dumps(packets)),self.assertRaises(ValueError):
                compose.packet_timing(Path("source.mp4"),retained_probe(),Mock())
        packets=retained_packets();packets["packets"][75]["pts"]+=512
        with patch.object(compose,"command",return_value=json.dumps(packets)),self.assertRaises(ValueError):
            compose.packet_timing(Path("source.mp4"),retained_probe(),Mock())
        packets=retained_packets();packets["packets"][-1]["side_data_list"]=[]
        with patch.object(compose,"command",return_value=json.dumps(packets)),self.assertRaises(ValueError):
            compose.packet_timing(Path("source.mp4"),retained_probe(),Mock())

    def test_reedit_only_replaces_labels_and_copies_original_audio_without_retiming(self):
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);argv=compose.reedit_command(work/"source.mp4",work/"edited.mp4",work)
            self.assertEqual(argv.count("-i"),1);self.assertEqual(argv[argv.index("-c:a")+1],"copy")
            self.assertNotIn("-af",argv);self.assertNotIn("-t",argv);self.assertNotIn("-r",argv)
            graph=argv[argv.index("-vf")+1]
            for size in (56,44,40):self.assertIn("fontsize="+str(size),graph)
            for forbidden in ("scale=","crop=","setpts=","overlay=","fps="):self.assertNotIn(forbidden,graph)
            self.assertEqual((work/"detail.txt").read_text(),"正体は、前日の来場者の光の人影")

    def test_aac_proof_hashes_encoded_packets_without_decoding_or_encoding(self):
        with patch.object(compose,"command",return_value="SHA256="+"a"*64+"\n") as command:
            self.assertEqual(compose.aac_packet_hash(Path("source.mp4"),Mock()),"a"*64)
        argv=command.call_args.args[0]
        self.assertEqual(argv[argv.index("-c:a")+1],"copy")
        for value in ("", "SHA256=nan", "SHA256="+"a"*64+"\nother"):
            with self.subTest(value=value),patch.object(compose,"command",return_value=value),self.assertRaises(ValueError):
                compose.aac_packet_hash(Path("source.mp4"),Mock())

    @unittest.skipUnless(os.environ.get("GITHUB_ACTIONS")=="true" and platform.system()=="Linux"
                         and os.environ.get("GITHUB_REPOSITORY")==compose.REPOSITORY
                         and os.environ.get("NEWS_CASTER_PUBLIC_REPOSITORY")=="true"
                         and os.environ.get("RUNNER_ENVIRONMENT")=="github-hosted",
                         "Real encoding runs only on the disposable GitHub CPU runner")
    def test_real_readability_filter_keeps_eight_seconds_and_identical_aac_packets(self):
        self.assertTrue(compose.FONT.is_file());self.assertIsNotNone(shutil.which("ffmpeg"))
        with tempfile.TemporaryDirectory() as directory:
            work=Path(directory);source=work/"source.mp4";edited=work/"edited.mp4"
            subprocess.run(["ffmpeg","-nostdin","-v","error","-f","lavfi","-i","color=c=gray:s=1280x720:r=25:d=8",
                            "-f","lavfi","-i","sine=frequency=440:sample_rate=24000:duration=8","-c:v","libx264",
                            "-threads","1","-pix_fmt","yuv420p","-c:a","aac","-ac","1",str(source)],check=True,timeout=60)
            subprocess.run(compose.reedit_command(source,edited,work),check=True,timeout=60)
            probe=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(edited)]))
            compose.validate_retained_probe(probe)
            source_probe=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(source)]))
            compose.validate_retained_probe(source_probe)
            proof=compose.compare_packet_timing(compose.packet_timing(source,source_probe,Mock()),
                                                compose.packet_timing(edited,probe,Mock()))
            self.assertTrue(proof["audio_timing_matches_within_one_sample"])
            def audio_hash(path):
                return subprocess.check_output(["ffmpeg","-v","error","-i",str(path),"-map","0:a:0","-c:a","copy","-f","hash","-hash","sha256","-"])
            self.assertEqual(audio_hash(source),audio_hash(edited))

    def test_fresh_main_rejects_stale_generation_or_current_article_hold(self):
        def check(generation_article_sha, hold_article_sha=None):
            generation={"sha256":compose.PHOTO_SHA,"article_sha256":generation_article_sha}
            manifest={"images":{compose.ARTICLE_SLUG:generation},"rejected":{}}
            if hold_article_sha:
                manifest["rejected"][compose.ARTICLE_SLUG]={"article_sha256":hold_article_sha}
            review={"article_sha256":compose.ARTICLE_SHA,"image_sha256":compose.PHOTO_SHA,"decision":"accepted"}
            results=["",json.dumps([{"slug":compose.ARTICLE_SLUG}]),json.dumps(manifest),
                     json.dumps({"reviews":{compose.ARTICLE_SLUG:review}}),"a"*40]
            with patch.object(compose,"command",side_effect=results), \
                 patch.object(compose,"article_fingerprint",return_value=compose.ARTICLE_SHA):
                return compose.check_fresh_main(Mock())
        self.assertEqual(check(compose.ARTICLE_SHA),"a"*40)
        self.assertEqual(check(compose.ARTICLE_SHA,"0"*64),"a"*40)
        with self.assertRaises(ValueError):check("0"*64)
        with self.assertRaises(ValueError):check(compose.ARTICLE_SHA,compose.ARTICLE_SHA)


if __name__=="__main__":unittest.main()
