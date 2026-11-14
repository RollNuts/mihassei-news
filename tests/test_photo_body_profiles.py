"""Small offline checks for authored designs; no image measurement or generation."""
import copy
import json
import unittest
from pathlib import Path

from scripts import photo_body_profiles as p


ROOT = Path(__file__).resolve().parents[1]
DESIGNS = {
    "nagino-saya": (164, 91, 61, 91, 37, 76, 7.3),
    "shioba-rio": (166, 95, 63, 94, 38, 77, 7.4),
    "tsukishiro-rena": (158, 82, 58, 85, 35, 73, 7.2),
    "hanase-mio": (160, 85, 60, 88, 37, 74, 7.2),
    "shiramine-yuki": (167, 89, 63, 92, 38, 78, 7.4),
    "amane-hikari": (156, 84, 58, 86, 35, 71, 7.1),
    "kiritani-sumire": (170, 87, 62, 91, 39, 81, 7.6),
}


class PhotoBodyProfileTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / "content/photo_issues.json").read_text())
        self.models = {model["id"]: model for model in self.catalog["models"]}
        self.profile = copy.deepcopy(self.models["nagino-saya"]["body_profile"])

    def test_seven_authored_designs_and_reference_roles(self):
        self.assertEqual(set(self.models), set(DESIGNS))
        fingerprints = set()
        for model_id, expected in DESIGNS.items():
            model = self.models[model_id]
            profile = p.validate_body_profile(model["body_profile"])
            self.assertEqual(tuple(profile[key] for key in p.PROFILE_RANGES), expected)
            fingerprints.add(p.body_profile_fingerprint(profile))
            p.validate_body_reference(model["body_reference"])
        self.assertEqual(len(fingerprints), 7)
        saya = self.models["nagino-saya"]
        self.assertEqual(saya["body_reference"], {"state": "accepted_reference", **saya["master"]})
        self.assertNotEqual(saya["body_reference"]["sha256"], saya["preferred_face_reference"]["sha256"])
        for model_id in set(DESIGNS) - {"nagino-saya"}:
            self.assertEqual(self.models[model_id]["body_reference"], {"state": "unprepared"})
        self.assertEqual(self.models["shioba-rio"]["reference_state"], "preview_only")

    def test_fingerprint_is_pure_order_independent_and_changes_with_design(self):
        original = copy.deepcopy(self.profile)
        baseline = p.body_profile_fingerprint(self.profile)
        reordered = dict(reversed(tuple(self.profile.items())))
        reordered["height_cm"] = float(reordered["height_cm"])
        self.assertEqual(p.body_profile_fingerprint(reordered), baseline)
        self.assertEqual(self.profile, original)
        changed = copy.deepcopy(self.profile)
        changed["waist_cm"] += 1
        self.assertNotEqual(p.body_profile_fingerprint(changed), baseline)

    def test_rejects_invalid_basis_numbers_and_schema(self):
        for field, value in (("basis", "measured_from_photo"), ("version", True),
                             ("height_cm", True), ("bust_cm", float("nan")),
                             ("hip_cm", float("inf")), ("waist_cm", -1),
                             ("height_cm", 16400), ("heads_tall", "7.3")):
            with self.subTest(field=field, value=value):
                invalid = {**self.profile, field: value}
                with self.assertRaises(ValueError):
                    p.body_profile_fingerprint(invalid)
        for invalid in ({key: value for key, value in self.profile.items() if key != "hip_cm"},
                        {**self.profile, "measured": True}):
            with self.assertRaises(ValueError):
                p.validate_body_profile(invalid)

    def test_rejects_face_preview_and_false_readiness_as_body_reference(self):
        for invalid in (None, self.models["shioba-rio"]["master"],
                        {"state": "accepted_reference"},
                        {"state": "unprepared", "path": "assets/generated/a.jpg"},
                        {**self.models["nagino-saya"]["body_reference"],
                         "path": "assets/generated/../face.jpg"}):
            with self.subTest(reference=invalid):
                with self.assertRaises(ValueError):
                    p.validate_body_reference(invalid)

    def test_catalog_gate_requires_the_body_design_and_reference_state(self):
        from scripts.photo_issues import validate_catalog
        validate_catalog(self.catalog)
        for key in ("body_profile", "body_reference"):
            invalid = copy.deepcopy(self.catalog)
            invalid["models"][0].pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_catalog(invalid)


if __name__ == "__main__":
    unittest.main()
