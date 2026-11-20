"""Offline native-format fixtures and honest Set 19 mapping limitations."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import team_code
from team_code import TeamCodeError, decode_team, encode_team, load_mapping


# Published literal and decoded names, NOT generated with this implementation:
# https://github.com/ttkienn/TFT-Team-Planner-Code (README example/Example Output).
PUBLISHED_CODE = "0235801a03435d35b332025336331000TFTSet16"
PUBLISHED_NAMES = ["Kindred", "Skarner", "Lucian & Senna", "Shyvana", "Fiddlesticks",
                   "Ambessa", "Swain", "Taric", "Draven"]
PUBLISHED_MAPPING = {
    "set_id": "TFTSet16", "format_version": "02", "slots": 10, "id_hex_digits": 3,
    "champions": {"Kindred": 0x358, "Skarner": 0x01A, "Lucian & Senna": 0x034,
                  "Shyvana": 0x35D, "Fiddlesticks": 0x35B, "Ambessa": 0x332,
                  "Swain": 0x025, "Taric": 0x336, "Draven": 0x331},
}


class TeamCodeTests(unittest.TestCase):
    def test_published_literal_encodes_exactly(self):
        self.assertEqual(encode_team(PUBLISHED_NAMES, PUBLISHED_MAPPING), PUBLISHED_CODE)

    def test_published_literal_decodes_exactly(self):
        self.assertEqual(decode_team(PUBLISHED_CODE, PUBLISHED_MAPPING), PUBLISHED_NAMES)

    def test_empty_roster_pads_all_ten_slots(self):
        expected = "02000000000000000000000000000000TFTSet16"
        self.assertEqual(encode_team([], PUBLISHED_MAPPING), expected)
        self.assertEqual(decode_team(expected, PUBLISHED_MAPPING), [])

    def test_ids_use_three_digits_and_uppercase_hex_decodes(self):
        mapping = deepcopy(PUBLISHED_MAPPING)
        mapping["champions"] = {"Lower boundary": 1, "Upper boundary": 4095}
        expected = ("02" "001" "fff" "000" "000" "000" "000" "000" "000" "000" "000" "TFTSet16")
        self.assertEqual(encode_team(["Lower boundary", "Upper boundary"], mapping), expected)
        self.assertEqual(decode_team(expected.replace("fff", "FFF"), mapping),
                         ["Lower boundary", "Upper boundary"])

    def test_padding_may_occur_between_native_entries(self):
        code = ("02" "358" "000" "01a" "000" "000" "000" "000" "000" "000" "000" "TFTSet16")
        self.assertEqual(decode_team(code, PUBLISHED_MAPPING), ["Kindred", "Skarner"])
        self.assertEqual(encode_team(decode_team(code, PUBLISHED_MAPPING), PUBLISHED_MAPPING),
                         "02" + "35801a" + "000" * 8 + "TFTSet16")

    def test_ten_champions_are_preserved_and_eleven_are_rejected(self):
        mapping = deepcopy(PUBLISHED_MAPPING)
        mapping["champions"] = {f"Unit {index}": index for index in range(1, 12)}
        names = [f"Unit {index}" for index in range(1, 11)]
        expected = "0200100200300400500600700800900aTFTSet16"
        self.assertEqual(encode_team(names, mapping), expected)
        self.assertEqual(decode_team(expected, mapping), names)
        with self.assertRaisesRegex(TeamCodeError, "at most 10"):
            encode_team(names + ["Unit 11"], mapping)

    def test_duplicate_names_and_duplicate_native_entries_are_errors(self):
        with self.assertRaisesRegex(TeamCodeError, "duplicate champions"):
            encode_team(["Kindred", "Kindred"], PUBLISHED_MAPPING)
        with self.assertRaisesRegex(TeamCodeError, "Duplicate champion ID"):
            decode_team("02" + "358358" + "000" * 8 + "TFTSet16", PUBLISHED_MAPPING)

    def test_unknown_name_and_unknown_id_are_never_filtered(self):
        with self.assertRaisesRegex(TeamCodeError, "No verified native.*Not a champion"):
            encode_team(["Kindred", "Not a champion"], PUBLISHED_MAPPING)
        with self.assertRaisesRegex(TeamCodeError, "native ID fff"):
            decode_team("02" + "fff" + "000" * 9 + "TFTSet16", PUBLISHED_MAPPING)

    def test_version_set_hex_and_slot_count_are_checked(self):
        for code in ["01" + PUBLISHED_CODE[2:], PUBLISHED_CODE.replace("Set16", "Set19"),
                     PUBLISHED_CODE[:2] + "ggg" + PUBLISHED_CODE[5:],
                     PUBLISHED_CODE[:2] + PUBLISHED_CODE[5:],
                     PUBLISHED_CODE[:2] + "000" + PUBLISHED_CODE[2:]]:
            with self.subTest(code=code), self.assertRaises(TeamCodeError):
                decode_team(code, PUBLISHED_MAPPING)

    def test_mapping_rejects_invalid_ids_collisions_and_format(self):
        for bad_id in [0, -1, 4096, True, "358", None]:
            mapping = deepcopy(PUBLISHED_MAPPING)
            mapping["champions"]["Kindred"] = bad_id
            with self.subTest(bad_id=bad_id), self.assertRaises(TeamCodeError):
                encode_team(["Kindred"], mapping)
        mapping = deepcopy(PUBLISHED_MAPPING)
        mapping["champions"]["Skarner"] = 0x358
        with self.assertRaisesRegex(TeamCodeError, "Ambiguous native ID"):
            encode_team(["Kindred"], mapping)
        for field, value in [("format_version", "01"), ("slots", 11), ("id_hex_digits", 2),
                             ("set_id", "not-a-native-set")]:
            mapping = deepcopy(PUBLISHED_MAPPING)
            mapping[field] = value
            with self.subTest(field=field), self.assertRaises(TeamCodeError):
                encode_team(["Kindred"], mapping)

    def test_stars_items_and_board_records_are_not_silently_accepted(self):
        for roster in ["Kindred", ("Kindred",), [{"name": "Kindred", "stars": 3}],
                       [{"name": "Kindred", "items": ["Deathblade"]}]]:
            with self.subTest(roster=roster), self.assertRaisesRegex(TeamCodeError, "list of champion names"):
                encode_team(roster, PUBLISHED_MAPPING)

    def test_bundled_mapping_accounts_for_all_94_roster_variants(self):
        mapping = load_mapping()
        snapshot = Path(__file__).resolve().parent / "data" / "set19.json"
        names = {row["name"] for row in json.loads(snapshot.read_text(encoding="utf-8"))["champions"]}
        self.assertEqual(len(names), 94)
        self.assertEqual(set(mapping["champions"]) | set(mapping["unsupported"]), names)
        self.assertEqual(len(mapping["champions"]), 89)
        self.assertEqual(len(set(mapping["champions"].values())), 89)
        self.assertEqual(set(mapping["unsupported"]), {"Graves", "Mordekaiser", "Nasus", "Kennen", "Kobuko"})
        self.assertEqual(set(mapping["source_character_ids"]), names)
        self.assertIn("primary_ids", mapping["sources"])

    def test_variants_sona_and_baron_use_real_source_ids(self):
        mapping = load_mapping()
        self.assertEqual(mapping["champions"]["Akali (K/DA)"], 1004)
        self.assertEqual(mapping["champions"]["Akali (True Damage)"], 1002)
        self.assertEqual(mapping["champions"]["Sona"], 1068)
        self.assertEqual(mapping["champions"]["Baron Nashor"], 1013)
        for name, literal in [("Akali (K/DA)", "3ec"), ("Akali (True Damage)", "3ea"),
                              ("Sona", "42c"), ("Baron Nashor", "3f5")]:
            with self.subTest(name=name):
                self.assertEqual(encode_team([name]), "02" + literal + "000" * 9 + "TFTSet19")

    def test_source_collisions_cannot_be_exported_or_ambiguously_decoded(self):
        for name in ["Graves", "Mordekaiser", "Nasus"]:
            with self.subTest(name=name), self.assertRaisesRegex(TeamCodeError, "collides"):
                encode_team(["Sona", name])
        with self.assertRaisesRegex(TeamCodeError, "Ambiguous native ID 449"):
            decode_team("02" + "449" + "000" * 9 + "TFTSet19")

    def test_missing_id_fails_even_when_other_units_have_ids(self):
        mapping = load_mapping()
        del mapping["champions"]["Ahri"]
        with self.assertRaisesRegex(TeamCodeError, "No verified native.*Ahri"):
            encode_team(["Sona", "Ahri"], mapping)

    def test_source_disagreements_remain_visible(self):
        mapping = load_mapping()
        self.assertEqual(mapping["secondary_source_disagreements"]["Kennen"]["secondary_id"], None)
        self.assertEqual(mapping["secondary_source_disagreements"]["Kobuko"]["secondary_id"], 1039)
        for name in ["Kennen", "Kobuko"]:
            self.assertNotIn(name, mapping["champions"])
            with self.subTest(name=name), self.assertRaisesRegex(TeamCodeError, "Conflicting published"):
                encode_team(["Sona", name])
        for native_id in ["40f", "410"]:
            with self.subTest(native_id=native_id), self.assertRaisesRegex(TeamCodeError, "Ambiguous native ID"):
                decode_team("02" + native_id + "000" * 9 + "TFTSet19")

    def test_pyinstaller_loads_mapping_from_meipass(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            data.mkdir()
            (data / "team_planner_ids.json").write_text(json.dumps(PUBLISHED_MAPPING), encoding="utf-8")
            with patch.object(team_code.sys, "_MEIPASS", directory, create=True):
                self.assertEqual(encode_team(PUBLISHED_NAMES), PUBLISHED_CODE)

    def test_missing_or_malformed_mapping_is_an_explicit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            with self.assertRaisesRegex(TeamCodeError, "Cannot load native"):
                load_mapping(path)
            path.write_text("not JSON", encoding="utf-8")
            with self.assertRaisesRegex(TeamCodeError, "Cannot load native"):
                load_mapping(path)


if __name__ == "__main__":
    unittest.main()
