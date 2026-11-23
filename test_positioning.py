"""Role/range geometry regressions; these are not combat-outcome predictions."""
import copy
import json
from pathlib import Path
import unittest

from positioning import annotate_positioning
from team_guidance import annotate_result, item_profile


def champion(name, role=None, attack_range=None, slots=1):
    row = {"name": name, "cost": 1, "traits": [], "slots": slots}
    if role is not None:
        row["role"] = role
    if attack_range is not None:
        row["attack_range"] = attack_range
        row["attack_range_source"] = "https://example.org/ranges/" + name
    return row


def board(names, main_tank=None):
    return {"champions": list(names), "item_holders": {"tank": main_tank},
            "planned_stars": {}, "traits": [], "strategy_notes": ["Existing guidance"]}


def markers(result):
    return {marker["champion"]: marker for marker in result["positioning"]}


def behind_front_tank(marker, tanks):
    return marker["row"] == 1 and any(
        tank["row"] == 0 and tank["column"] in (marker["column"], marker["column"] + 1)
        for tank in tanks)


class _PlacementAssertions:
    def assert_complete(self, result):
        names = set(result["champions"])
        placements = result["positioning"]
        self.assertEqual(len(placements), len(names))
        self.assertEqual({marker["champion"] for marker in placements}, names)
        self.assertEqual(len(result["lineup_order"]), len(names))
        self.assertEqual(set(result["lineup_order"]), names)
        cells = {(marker["row"], marker["column"]) for marker in placements}
        self.assertEqual(len(cells), len(names))
        for marker in placements:
            self.assertIn(marker["row"], range(4))
            self.assertIn(marker["column"], range(7))
            self.assertTrue(marker["reason"])
            self.assertIn("Estimated", marker["reason"])
        ordered = markers(result)
        rows = [ordered[name]["row"] for name in result["lineup_order"]]
        self.assertEqual(rows, sorted(rows))


class PositioningTests(_PlacementAssertions, unittest.TestCase):
    def test_only_main_tank_is_offset_toward_right_backline(self):
        roster = [champion("Leona", "Magic Tank", 1), champion("Ashe", "Attack Marksman", 4)]
        result = board(["Leona", "Ashe"], "Leona")
        annotate_positioning(result, roster)
        self.assertEqual((markers(result)["Leona"]["row"], markers(result)["Leona"]["column"]), (0, 4))
        self.assertEqual(markers(result)["Ashe"]["column"], 6)
        self.assert_complete(result)

    def test_weak_tank_filler_does_not_force_main_tank_to_outside_edge(self):
        leona = champion("Leona", "Magic Tank", 1)
        leona["cost"] = 4
        roster = [leona, champion("Lillia", "Magic Tank", 1),
                  champion("Ashe", "Attack Marksman", 4)]
        result = board([unit["name"] for unit in roster], "Leona")
        result["planned_stars"] = {"Leona": 2, "Lillia": 1}
        annotate_positioning(result, roster)
        placed = markers(result)
        self.assertEqual((placed["Leona"]["row"], placed["Leona"]["column"]), (0, 4))
        self.assertEqual(placed["Lillia"]["row"], 0)
        self.assert_complete(result)

    def test_true_solo_amumu_has_empty_adjacent_friendly_hexes(self):
        roster = [champion("Amumu", "Magic Tank", 1)]
        roster.extend(champion(f"Fighter {i}", "Attack Fighter", 1) for i in range(6))
        roster.extend(champion(f"Caster {i}", "Magic Caster", 4) for i in range(4))
        result = board([unit["name"] for unit in roster], "Amumu")
        annotate_positioning(result, roster)
        placed = markers(result)
        self.assertEqual((placed["Amumu"]["row"], placed["Amumu"]["column"]), (0, 3))
        occupied = {(marker["row"], marker["column"]) for marker in result["positioning"]}
        self.assertFalse(occupied & {(0, 2), (0, 4), (1, 2), (1, 3)})
        self.assert_complete(result)

    def test_amumu_with_another_comparable_tank_is_not_isolated(self):
        roster = [champion("Amumu", "Magic Tank", 1), champion("Olaf", "Attack Tank", 1),
                  champion("Yone", "Attack Fighter", 1)]
        result = board([unit["name"] for unit in roster], "Amumu")
        annotate_positioning(result, roster)
        placed = markers(result)
        self.assertEqual((placed["Amumu"]["row"], placed["Amumu"]["column"]), (0, 4))
        self.assertTrue(behind_front_tank(placed["Yone"], [placed["Amumu"], placed["Olaf"]]))
        self.assert_complete(result)

    def test_main_tank_and_support_tanks_form_a_connected_centered_front(self):
        roster = [champion(name, "Magic Tank", 1) for name in ("A", "B", "Z")]
        result = board(["A", "Z", "B"], "Z")
        annotate_positioning(result, roster)
        placed = markers(result)
        self.assertEqual((placed["Z"]["row"], placed["Z"]["column"]), (0, 4))
        self.assertEqual((placed["A"]["row"], placed["A"]["column"]), (0, 3))
        self.assertEqual(placed["B"]["row"], 0)
        self.assertEqual(result["lineup_order"][0], "Z")
        self.assertIn("Main item-holder tank", placed["Z"]["reason"])
        self.assert_complete(result)

    def test_frontline_uses_middle_hexes_before_reaching_the_edges(self):
        for count in range(1, 8):
            with self.subTest(tanks=count):
                roster = [champion(f"Tank {i}", "Magic Tank", 1) for i in range(count)]
                result = board([unit["name"] for unit in roster], "Tank 0")
                annotate_positioning(result, roster)
                columns = sorted(marker["column"] for marker in result["positioning"])
                self.assertEqual(columns, list(range(columns[0], columns[0] + count)))
                if count <= 5:
                    self.assertFalse({0, 6} & set(columns))
                self.assert_complete(result)

    def test_fighters_assassins_and_two_range_carries_are_protected(self):
        roster = [champion("Tank R", "Attack Tank", 1),
                  champion("Tank L", "Magic Tank", 1),
                  champion("A Fighter", "Magic Fighter", 1),
                  champion("B Assassin", "Attack Assassin", 1),
                  champion("C Two-range AD", "Attack Marksman", 2),
                  champion("D Ranged AD", "Attack Marksman", 4)]
        result = board([row["name"] for row in roster], "Tank R")
        annotate_positioning(result, roster)
        placed = markers(result)
        tanks = [placed["Tank R"], placed["Tank L"]]
        for name in ("A Fighter", "B Assassin", "C Two-range AD"):
            self.assertTrue(behind_front_tank(placed[name], tanks), name)
            self.assertIn("directly behind", placed[name]["reason"])
        self.assertIn(placed["A Fighter"]["column"], (2, 3, 4))
        self.assertEqual(placed["D Ranged AD"]["row"], 3)
        self.assert_complete(result)

    def test_magic_and_attack_ranged_carries_cluster_on_right_back_row(self):
        roster = [champion("AP", "Magic Caster", 4),
                  champion("AD", "Attack Marksman", 4),
                  champion("Specialist", "Attack Specialist", 3)]
        result = board([row["name"] for row in roster])
        annotate_positioning(result, roster)
        self.assertEqual({marker["row"] for marker in result["positioning"]}, {3})
        self.assertEqual({marker["column"] for marker in result["positioning"]}, {4, 5, 6})
        self.assert_complete(result)

    def test_teemo_center_is_reserved_before_backline_and_overflow(self):
        roster = [champion(f"Ranged {index:02}", "Magic Caster", 4) for index in range(9)]
        roster.append(champion("Teemo", "Magic Caster", 4))
        result = board([row["name"] for row in roster])
        annotate_positioning(result, roster)
        teemo = markers(result)["Teemo"]
        self.assertEqual((teemo["row"], teemo["column"]), (3, 3))
        self.assertIn("large-area", teemo["reason"])
        self.assertEqual(sum(marker["row"] == 3 for marker in result["positioning"]), 7)
        self.assertTrue(all(marker["row"] >= 2 for marker in result["positioning"]))
        self.assert_complete(result)

    def test_yone_flex_keeps_melee_protection(self):
        roster = [champion("Tank", "Magic Tank", 1), champion("Yone", "Magic Fighter", 1)]
        result = board(["Yone", "Tank"], "Tank")
        annotate_result(result, roster)
        annotate_positioning(result, roster)
        placed = markers(result)
        self.assertEqual(result["item_holders"]["flex"], "Yone")
        self.assertTrue(behind_front_tank(placed["Yone"], [placed["Tank"]]))
        self.assertIn("AD/AP flex", placed["Yone"]["reason"])

    def test_missing_ranges_use_explainable_role_fallbacks(self):
        roster = [champion("Tank", "Magic Tank"), champion("Melee", "Attack Fighter"),
                  champion("Caster", "Magic Caster"), champion("Unknown")]
        result = board([row["name"] for row in roster] + ["Missing champion"], "Tank")
        annotate_positioning(result, roster)
        placed = markers(result)
        self.assertEqual(placed["Tank"]["row"], 0)
        self.assertEqual(placed["Melee"]["row"], 1)
        for name in ("Caster", "Unknown", "Missing champion"):
            self.assertEqual(placed[name]["row"], 3)
        for marker in result["positioning"]:
            self.assertIn("Attack range unavailable", marker["reason"])
            self.assertNotIn("Attack range: 1", marker["reason"])
        self.assertIn("Combat role unavailable", placed["Unknown"]["reason"])
        self.assert_complete(result)

    def test_invalid_ranges_do_not_become_sourced_numbers(self):
        for value in (True, False, 0, -1, float("inf"), float("nan"), "4"):
            with self.subTest(value=value):
                row = champion("Carry", "Attack Marksman")
                row["attack_range"] = value
                result = board(["Carry"])
                annotate_positioning(result, [row])
                self.assertIn("Attack range unavailable", result["positioning"][0]["reason"])
                self.assertEqual(result["positioning"][0]["row"], 3)

    def test_numeric_range_without_provenance_is_not_claimed_sourced(self):
        row = champion("Carry", "Attack Marksman", 4)
        del row["attack_range_source"]
        result = board(["Carry"])
        annotate_positioning(result, [row])
        self.assertIn("Attack range: 4 hexes", result["positioning"][0]["reason"])
        self.assertIn("Attack-range source unavailable", result["positioning"][0]["reason"])

    def test_repeated_baron_uses_one_physical_marker_not_two_team_slots(self):
        roster = [champion("Baron Nashor", "Magic Tank", 1, slots=2),
                  champion("Carry", "Attack Marksman", 4)]
        result = board(["Carry", "Baron Nashor", "Baron Nashor"], "Baron Nashor")
        original_order = list(result["champions"])
        annotate_positioning(result, roster)
        self.assert_complete(result)
        self.assertEqual(result["champions"], original_order)
        self.assertIn("2 team slots; shown once", markers(result)["Baron Nashor"]["reason"])

    def test_annotation_is_deterministic_and_does_not_reorder_export_roster(self):
        roster = [champion("Tank", "Magic Tank", 1), champion("Yone", "Magic Fighter", 1),
                  champion("AP", "Magic Caster", 4), champion("AD", "Attack Marksman", 4)]
        result = board(["AD", "Yone", "Tank", "AP"], "Tank")
        original_order = list(result["champions"])
        annotate_positioning(result, roster)
        expected = copy.deepcopy(result)
        annotate_positioning(result, list(reversed(roster)))
        self.assertEqual(result, expected)
        self.assertEqual(result["champions"], original_order)
        reversed_board = board(list(reversed(original_order)), "Tank")
        annotate_positioning(reversed_board, {row["name"]: row for row in roster})
        self.assertEqual(result["positioning"], reversed_board["positioning"])
        self.assertEqual(result["lineup_order"], reversed_board["lineup_order"])

    def test_eleven_unit_mixed_and_overflow_boards_stay_behind_tanks(self):
        for role, attack_range in (("Attack Fighter", 1), ("Attack Marksman", 2),
                                   ("Magic Caster", 4)):
            with self.subTest(role=role, attack_range=attack_range):
                roster = [champion("Tank", "Magic Tank", 1)] + [
                    champion(f"Unit {index:02}", role, attack_range) for index in range(10)]
                result = board([row["name"] for row in roster], "Tank")
                annotate_positioning(result, roster)
                self.assert_complete(result)
                self.assertTrue(all(marker["row"] > 0 for marker in result["positioning"]
                                    if marker["champion"] != "Tank"))
                self.assertTrue(any("overflow" in marker["reason"]
                                    for marker in result["positioning"]))

    def test_full_physical_capacity_and_tank_overflow_are_collision_free(self):
        for role, attack_range in (("Magic Tank", 1), ("Attack Fighter", 1),
                                   ("Attack Marksman", 4)):
            with self.subTest(role=role):
                roster = [champion(f"Unit {index:02}", role, attack_range) for index in range(28)]
                result = board([row["name"] for row in roster])
                annotate_positioning(result, roster)
                self.assert_complete(result)
                if role == "Magic Tank":
                    self.assertEqual(sum(marker["row"] == 0 for marker in result["positioning"]), 7)
                    self.assertTrue(any("tank overflow" in marker["reason"]
                                        for marker in result["positioning"]))
                else:
                    self.assertTrue(any("unavoidable frontline overflow" in marker["reason"]
                                        for marker in result["positioning"]))

    def test_capacity_error_does_not_silently_drop_units_or_partially_annotate(self):
        oversized = board([f"Unit {index}" for index in range(29)])
        result = board(["One"])
        result["buildup"] = [{"board": board(["One"]), "fallback": oversized}]
        original = copy.deepcopy(result)
        with self.assertRaisesRegex(ValueError, "28 distinct champions"):
            annotate_positioning(result, [])
        self.assertEqual(result, original)

    def test_all_detached_board_variants_get_independent_annotations(self):
        roster = [champion("Tank", "Magic Tank", 1), champion("Carry", "Attack Marksman", 4)]
        prototype = board(["Tank", "Carry"], "Tank")
        result = copy.deepcopy(prototype)
        variants = [copy.deepcopy(prototype) for _ in range(4)]
        result["buildup"] = [
            {"board": None, "fallback": None, "goal_board": None,
             "upgrades": [{"board": None}]},
            {"board": variants[0], "fallback": variants[1], "goal_board": variants[2],
             "upgrades": [{"board": variants[3]}]}]
        originals = [copy.deepcopy(row) for row in [result] + variants]
        roster_before = copy.deepcopy(roster)
        annotate_positioning(result, roster)
        annotated = [result] + variants
        for row, original in zip(annotated, originals):
            self.assert_complete(row)
            for key in ("champions", "item_holders", "planned_stars", "traits", "strategy_notes"):
                self.assertEqual(row[key], original[key])
        self.assertEqual(len({id(row["positioning"]) for row in annotated}), 5)
        self.assertEqual(len({id(row["lineup_order"]) for row in annotated}), 5)
        self.assertEqual(len({id(marker) for row in annotated for marker in row["positioning"]}), 10)
        variants[0]["positioning"][0]["reason"] = "Changed only this board"
        self.assertNotEqual(result["positioning"][0]["reason"], "Changed only this board")
        self.assertEqual(roster, roster_before)
        self.assertEqual(json.loads(json.dumps(result)), result)
        self.assertIsNone(result["buildup"][0]["board"])

    def test_empty_boards_are_annotated(self):
        result = board([])
        annotate_positioning(result, [])
        self.assertEqual(result["positioning"], [])
        self.assertEqual(result["lineup_order"], [])


class SnapshotPositioningTests(_PlacementAssertions, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot_path = Path(__file__).parent / "data" / "set19.json"
        cls.snapshot_text = cls.snapshot_path.read_text(encoding="utf-8")
        cls.snapshot = json.loads(cls.snapshot_text)
        cls.champions = {row["name"]: row for row in cls.snapshot["champions"]}

    def test_real_tanks_and_ranged_carries_are_separated(self):
        names = ["Elise", "Olaf", "Ashe", "Ziggs", "Varus", "Yone", "Teemo"]
        result = board(names, "Olaf")
        original = copy.deepcopy(self.snapshot)
        annotate_positioning(result, self.champions)
        placed = markers(result)
        for name in ("Elise", "Olaf"):
            self.assertEqual(item_profile(self.champions[name]), "tank")
            self.assertEqual(placed[name]["row"], 0)
        for name in ("Ashe", "Ziggs", "Varus"):
            self.assertGreater(self.champions[name]["attack_range"], 2)
            self.assertEqual(placed[name]["row"], 3)
        self.assertTrue(behind_front_tank(placed["Yone"], [placed["Elise"], placed["Olaf"]]))
        self.assertEqual(result["lineup_order"][0], "Olaf")
        self.assertEqual((placed["Teemo"]["row"], placed["Teemo"]["column"]), (3, 3))
        self.assert_complete(result)
        self.assertEqual(self.snapshot, original)
        self.assertEqual(self.snapshot_path.read_text(encoding="utf-8"), self.snapshot_text)

    def test_every_snapshot_champion_fits_deterministic_full_rosters(self):
        names = sorted(self.champions)
        for offset in range(0, len(names), 11):
            selected = names[offset:offset + 11]
            with self.subTest(champions=selected):
                result = board(selected)
                annotate_positioning(result, self.champions)
                self.assert_complete(result)
                reversed_result = board(list(reversed(selected)))
                annotate_positioning(reversed_result, self.snapshot["champions"])
                self.assertEqual(result["positioning"], reversed_result["positioning"])
                self.assertEqual(result["lineup_order"], reversed_result["lineup_order"])


if __name__ == "__main__":
    unittest.main()
