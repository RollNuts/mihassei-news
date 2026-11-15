"""The editorial cast lookup stays small and does not enter image production."""
import copy
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from scripts import photo_issues as photos


class PhotoCastTests(unittest.TestCase):
    def setUp(self):
        self.catalog = photos.load_photo_issues()

    def read_cast(self, *arguments):
        output = io.StringIO()
        before = copy.deepcopy(self.catalog)
        with patch.object(photos, "load_photo_issues", return_value=self.catalog), \
             patch.object(photos, "_verify_plan_references") as verify, \
             patch.object(photos, "freeze_model_plan") as freeze, \
             patch.object(photos, "generation_inputs") as generation, \
             patch.object(photos, "validate_published_issue") as release, \
             patch.object(photos, "build_photo_issues") as build, \
             patch("urllib.request.urlopen") as network, redirect_stdout(output):
            self.assertEqual(photos.main(["--cast", *arguments, "--json"]), 0)
        for operation in (verify, freeze, generation, release, build, network):
            operation.assert_not_called()
        self.assertEqual(self.catalog, before)
        return json.loads(output.getvalue())

    def test_roster_lists_seven_fixed_adult_identities_and_occupations_only(self):
        report = self.read_cast()
        self.assertEqual(set(report), {"cast"})
        self.assertEqual(len(report["cast"]), 7)
        self.assertEqual([(row["name"], row["age"], row["occupation"]) for row in report["cast"]], [
            ("凪野さや", 27, "体験ロケ担当レポーター兼ファッションモデル"),
            ("汐羽りお", 29, "報道アナウンサー兼キャスター"),
            ("月城れな", 23, "俳優兼モデル"),
            ("花瀬みお", 20, "体験型バラエティアイドル"),
            ("白峰ゆき", 24, "旅・衣服担当モデル兼リポーター"),
            ("天音ひかり", 21, "歌とダンスのアイドル"),
            ("桐谷すみれ", 26, "俳優兼ファッション企画ナビゲーター"),
        ])
        for row, model in zip(report["cast"], self.catalog["models"]):
            self.assertEqual(set(row), {"id", "name", "age", "occupation"})
            self.assertEqual(row["id"], model["id"])
            self.assertTrue(model["fictional"])
            self.assertTrue(model["character"]["casting_specialty"])
        self.assertLess(len(json.dumps(report, ensure_ascii=False)), 1000)

    def test_one_unprepared_model_returns_only_editorial_persona(self):
        model = next(model for model in self.catalog["models"] if model["id"] == "tsukishiro-rena")
        # Cast lookup must work before photo preparation, independently of the
        # current production registry's later accepted references.
        model.update(reference_state="unprepared", master=None, body_reference={"state": "unprepared"})
        self.assertEqual(model["reference_state"], "unprepared")
        report = self.read_cast(model["id"])
        self.assertEqual(set(report), {"id", "name", "age", "fictional", "occupation",
                                       "casting_specialty", "first_person", "personality", "voice", "likes", "weakness"})
        self.assertEqual((report["id"], report["age"], report["fictional"]), (model["id"], 23, True))
        for field in ("occupation", "casting_specialty", "first_person", "personality", "voice", "likes", "weakness"):
            self.assertEqual(report[field], model["character"][field])

    def test_unknown_model_and_conflicting_cli_modes_fail_without_output(self):
        for arguments, message in [
            (["--cast", "missing-person", "--json"], "Unknown cast model"),
            (["--cast", "--status"], "not allowed with argument"),
            (["--cast", "--cut", "04"], "--cut is only valid with --cut-plan"),
        ]:
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(arguments=arguments), \
                 patch.object(photos, "load_photo_issues", return_value=self.catalog), \
                 redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
                photos.main(arguments)
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn(message, stderr.getvalue())

    def test_cast_rejects_missing_occupation_without_exposing_visual_metadata(self):
        self.catalog["models"][0]["character"].pop("occupation")
        with self.assertRaisesRegex(ValueError, "cast occupation"):
            photos.cast_report(self.catalog)


if __name__ == "__main__":
    unittest.main()
