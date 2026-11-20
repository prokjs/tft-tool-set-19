"""Small deterministic engine regressions; run with python -m unittest test_optimizer."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from optimizer import BAND_TRAITS, load_data, optimize


def fixture():
    def trait(name, points, unique=False, **extra):
        return {"name": name, "breakpoints": points, "unique": unique, **extra}

    def champion(name, traits, cost=1, **extra):
        return {"name": name, "cost": cost, "traits": traits, "unlockable": False, **extra}

    return {
        "version": "synthetic", "sources": [], "collab_groups": [],
        "traits": [
            trait("Collab", [3, 5]), trait("Bodyguard", [2, 4]),
            trait("Juggernaut", [2]), trait("Frontman", [2]),
            trait("K/DA", [3, 5, 7], count_mode="stars"),
            trait("Jazz", [2, 3]), trait("Arcane", [2]),
            trait("Solo", [1], unique=True),
        ],
        "champions": [
            champion("A", ["Bodyguard", "Arcane"]),
            champion("B", ["Bodyguard", "K/DA"], 2),
            champion("C", ["Jazz"]),
            champion("D", ["K/DA", "Arcane"], 3),
            champion("E", ["Juggernaut"], 2),
            champion("F", ["Frontman"], 2),
            champion("G", ["Jazz"]),
            champion("H", ["Solo"], 4),
            champion("Sona", ["Collab", "Jazz"], 5),
            champion("Baron Nashor", ["Juggernaut"], 5, slots=2),
            champion("OffGroup", ["Collab", "K/DA"]),
        ],
    }


def row(result, name):
    return next(item for item in result["traits"] if item["name"] == name)


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def search(self, **overrides):
        options = {
            "data": self.data, "collab": ["A", "B", "C"], "size": 3,
            "locked": [], "excluded": [], "min_collab": 0,
            "mode": "synergies", "limit": 12,
        }
        options.update(overrides)
        return optimize(**options)

    def test_deterministic_unique_teams_and_no_input_mutation(self):
        original = copy.deepcopy(self.data)
        first = self.search()
        self.assertEqual(first, self.search())
        self.assertEqual(self.data, original)
        self.assertTrue(first)
        identities = [tuple(result["champions"]) for result in first]
        self.assertEqual(len(identities), len(set(identities)))
        costs = {champion["name"]: champion["cost"] for champion in self.data["champions"]}
        for result in first:
            names = result["champions"]
            self.assertEqual(len(names), len(set(names)))
            slots = sum(2 if name == "Baron Nashor" else 1 for name in names)
            self.assertEqual(slots, 3)
            self.assertEqual(result["cost"], sum(costs[name] for name in names))
            self.assertEqual(result["active"], sum(
                not trait["unique"] and trait["tier"] > 0 for trait in result["traits"]
            ))
            points = {trait["name"]: trait["breakpoints"] for trait in self.data["traits"]}
            self.assertEqual(result["tiers"], sum(
                sum(point <= trait["count"] for point in points[trait["name"]])
                for trait in result["traits"] if not trait["unique"]
            ))

    def test_locks_exclusions_and_minimum_three(self):
        results = self.search(locked=["A"], excluded=["Sona", "Baron Nashor"], min_collab=3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["champions"], ["A", "B", "C"])
        self.assertEqual(row(results[0], "Collab")["count"], 3)

    def test_sona_always_collab_and_minimum_five(self):
        results = self.search(collab=["A", "B", "C", "D"], size=5, min_collab=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["champions"], ["A", "B", "C", "D", "Sona"])
        self.assertEqual(row(results[0], "Collab")["tier"], 5)
        self.assertIsNone(row(results[0], "Collab")["next"])

    def test_collab_is_dynamic_not_static(self):
        result = self.search(collab=["B"], locked=["B", "OffGroup", "Sona"])[0]
        self.assertEqual(row(result, "Collab")["count"], 2)
        self.assertEqual(row(result, "Collab")["tier"], 0)
        self.assertEqual(row(result, "Collab")["next"], 3)

    def test_sona_band_and_duplicate_native_trait_count_once(self):
        sona = next(champion for champion in self.data["champions"] if champion["name"] == "Sona")
        sona["traits"] += ["Jazz", "K/DA"]
        result = self.search(
            collab=[], locked=["A", "B", "Sona"], sona_band="K/DA",
            stars={"Sona": 3, "B": 2},
        )[0]
        self.assertEqual(row(result, "K/DA")["count"], 5)
        self.assertEqual(row(result, "K/DA")["tier"], 5)
        self.assertEqual(row(result, "Jazz")["count"], 1)
        self.assertEqual(row(result, "Collab")["count"], 1)
        self.assertEqual(row(result, "Bodyguard")["count"], 2)

    def test_sona_band_does_not_affect_other_units_or_absent_sona(self):
        with_sona = self.search(collab=[], locked=["A", "C", "Sona"], sona_band="K/DA")[0]
        self.assertEqual(row(with_sona, "K/DA")["count"], 1)
        without = self.search(collab=[], locked=["A", "C", "G"], sona_band="K/DA")[0]
        self.assertNotIn("K/DA", {trait["name"] for trait in without["traits"]})

    def test_star_defaults_and_unit_counting_are_separate(self):
        one_star = self.search(collab=[], locked=["A", "B", "D"])[0]
        self.assertEqual(row(one_star, "K/DA")["count"], 2)
        self.assertEqual(row(one_star, "K/DA")["tier"], 0)
        four_star = self.search(collab=["A", "B", "D"], locked=["A", "B", "D"],
                                stars={"A": 4, "B": 4, "D": 4})[0]
        self.assertEqual(row(four_star, "K/DA")["count"], 8)
        self.assertEqual(row(four_star, "K/DA")["tier"], 7)
        self.assertEqual(row(four_star, "Collab")["count"], 3)
        self.assertEqual(row(four_star, "Bodyguard")["count"], 2)

    def test_unique_traits_not_in_active_or_tier_totals(self):
        result = self.search(collab=[], locked=["H", "C", "G"])[0]
        self.assertTrue(row(result, "Solo")["unique"])
        self.assertEqual(row(result, "Solo")["tier"], 1)
        self.assertEqual(result["active"], 1)
        self.assertEqual(result["tiers"], 1)

    def test_weighted_slots_with_collab_minimum(self):
        result = self.search(
            collab=["A", "B", "Baron Nashor"], size=4,
            locked=["A", "B", "Baron Nashor"], min_collab=3,
        )[0]
        self.assertEqual(len(result["champions"]), 3)
        self.assertEqual(row(result, "Collab")["count"], 3)
        self.assertEqual(result["frontline"], 3)

    def test_weighted_slots_can_be_completed_from_two_search_depths(self):
        self.data["champions"] = [champion for champion in self.data["champions"]
                                  if champion["name"] in {"A", "B", "C", "Baron Nashor"}]
        results = self.search(collab=[], size=3, limit=20)
        teams = {tuple(result["champions"]) for result in results}
        self.assertIn(("A", "B", "C"), teams)
        self.assertIn(("A", "Baron Nashor"), teams)
        self.assertIn(("B", "Baron Nashor"), teams)
        self.assertIn(("Baron Nashor", "C"), teams)
        self.assertEqual(len(teams), 4)

    def test_minimum_collab_not_starved_by_early_decoys(self):
        self.data["champions"] = [
            {"name": f"A{index:02}", "cost": 1, "traits": ["Solo"], "unlockable": False}
            for index in range(18)
        ] + [
            {"name": f"Z{index}", "cost": 5, "traits": [], "unlockable": False}
            for index in range(5)
        ]
        group = [f"Z{index}" for index in range(5)]
        result = self.search(collab=group, size=5, min_collab=5)[0]
        self.assertEqual(result["champions"], group)
        self.assertEqual(row(result, "Collab")["count"], 5)

    def test_balanced_rewards_two_frontline_and_one_other(self):
        self.data["traits"] = [
            {"name": "Bodyguard", "breakpoints": [99], "unique": False},
            {"name": "Arcane", "breakpoints": [99], "unique": False},
        ]
        self.data["champions"] = [
            {"name": name, "cost": 1, "traits": ["Bodyguard"], "unlockable": False}
            for name in ("A", "B", "C")
        ] + [{"name": "Z", "cost": 1, "traits": ["Arcane"], "unlockable": False}]
        synergy = self.search(collab=[], limit=1)[0]
        balanced = self.search(collab=[], mode="balanced", limit=1)[0]
        self.assertEqual(synergy["champions"], ["A", "B", "C"])
        self.assertEqual(balanced["frontline"], 2)
        self.assertIn("Z", balanced["champions"])
        self.assertEqual(balanced["score"], 80.0)

    def test_high_value_prefers_premium_unit_for_identical_synergies(self):
        self.data["traits"] = []
        self.data["champions"] = [
            {"name": name, "cost": cost, "traits": [], "unlockable": False}
            for name, cost in [("Anchor1", 1), ("Anchor2", 1), ("Cheap", 1), ("Premium", 5)]
        ]
        options = dict(collab=[], locked=["Anchor1", "Anchor2"], limit=1)
        balanced = self.search(mode="balanced", **options)[0]
        high_value = self.search(mode="high_value", **options)[0]
        self.assertIn("Cheap", balanced["champions"])
        self.assertIn("Premium", high_value["champions"])
        self.assertGreater(high_value["cost"], balanced["cost"])
        self.assertEqual(high_value["active"], balanced["active"])

    def test_high_value_can_trade_one_trait_but_not_ignore_synergies(self):
        self.data["traits"] = [{"name": "Link", "breakpoints": [2], "unique": False}]
        self.data["champions"] = [
            {"name": name, "cost": cost, "traits": traits, "unlockable": False}
            for name, cost, traits in [
                ("Anchor1", 1, ["Link"]), ("Anchor2", 1, []),
                ("Cheap", 1, ["Link"]), ("Premium", 5, []),
            ]
        ]
        options = dict(collab=[], locked=["Anchor1", "Anchor2"], limit=1)
        balanced = self.search(mode="balanced", **options)[0]
        premium = self.search(mode="high_value", **options)[0]
        self.assertIn("Cheap", balanced["champions"])
        self.assertIn("Premium", premium["champions"])
        self.assertLess(premium["active"], balanced["active"])
        self.data["champions"].append(
            {"name": "Synergistic", "cost": 4, "traits": ["Link"], "unlockable": False}
        )
        synergistic = self.search(mode="high_value", **options)[0]
        self.assertIn("Synergistic", synergistic["champions"])
        self.assertLess(synergistic["cost"], premium["cost"])
        self.assertGreater(synergistic["active"], premium["active"])

    def test_high_value_preserves_locked_excluded_and_collab_constraints(self):
        results = self.search(mode="high_value", size=4, min_collab=3,
                              locked=["A"], excluded=["Sona", "Baron Nashor"])
        self.assertTrue(results)
        for result in results:
            self.assertTrue({"A", "B", "C"} <= set(result["champions"]))
            self.assertFalse({"Sona", "Baron Nashor"} & set(result["champions"]))
            self.assertEqual(len(result["champions"]), 4)
            self.assertGreaterEqual(row(result, "Collab")["count"], 3)

    def test_tied_results_diversify_and_limit(self):
        self.data["traits"] = []
        self.data["champions"] = [
            {"name": name, "cost": 1, "traits": [], "unlockable": False}
            for name in "ABCDEF"
        ]
        results = self.search(collab=[], limit=4)
        self.assertEqual(len(results), 4)
        self.assertIn(["A", "B", "C"], [result["champions"] for result in results])
        self.assertIn(["D", "E", "F"], [result["champions"] for result in results])

    def test_exclusive_variants_never_share_a_team(self):
        self.data["champions"] += [
            {"name": name, "cost": 4, "traits": traits, "unlockable": True,
             "exclusive_group": "akali"}
            for name, traits in (
                ("Akali (K/DA)", ["K/DA"]),
                ("Akali (True Damage)", ["Arcane"]),
            )
        ]
        variants = {"Akali (K/DA)", "Akali (True Damage)"}
        for result in self.search(collab=[], limit=50):
            self.assertLessEqual(len(variants & set(result["champions"])), 1)
        for result in self.search(collab=[], locked=["Akali (K/DA)"], limit=50):
            self.assertIn("Akali (K/DA)", result["champions"])
            self.assertNotIn("Akali (True Damage)", result["champions"])
        with self.assertRaises(ValueError):
            self.search(collab=[], locked=sorted(variants))
        with self.assertRaises(ValueError):
            self.search(collab=["A", *sorted(variants)], excluded=["Sona"], min_collab=3)

    def test_exclusivity_and_weighted_slots_infeasibility(self):
        self.data["champions"] = [
            {"name": "A", "cost": 1, "slots": 2, "traits": [],
             "unlockable": False, "exclusive_group": "pair"},
            {"name": "B", "cost": 1, "slots": 2, "traits": [],
             "unlockable": False, "exclusive_group": "pair"},
            {"name": "C", "cost": 1, "traits": [], "unlockable": False},
        ]
        with self.assertRaises(ValueError):
            self.search(collab=[], size=4)
        self.assertEqual(len(self.search(collab=[], size=3)), 2)

    def test_one_and_two_slot_boards(self):
        for size in (1, 2):
            for result in self.search(size=size):
                self.assertEqual(sum(
                    2 if name == "Baron Nashor" else 1 for name in result["champions"]
                ), size)

    def test_invalid_constraints_raise_value_error(self):
        cases = [
            {"size": 0}, {"size": 12}, {"size": True}, {"size": 3.0},
            {"min_collab": 1}, {"min_collab": True}, {"mode": "strongest"},
            {"limit": 0}, {"limit": False}, {"collab": "A"},
            {"collab": ["A", "A"]}, {"locked": ["A", "A"]},
            {"excluded": ["A", "A"]}, {"locked": ["Unknown"]},
            {"excluded": ["Unknown"]}, {"collab": ["Unknown"]},
            {"locked": ["A"], "excluded": ["A"]},
            {"sona_band": "Bodyguard"}, {"sona_band": "Missing"}, {"sona_band": None},
            {"stars": []}, {"stars": {"Unknown": 1}}, {"stars": {"A": 0}},
            {"stars": {"A": 5}}, {"stars": {"A": True}}, {"stars": {"A": 1.5}},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.search(**overrides)

    def test_infeasible_constraints_raise_value_error(self):
        cases = [
            {"locked": ["A", "B", "C", "D"]},
            {"locked": ["A", "B", "Baron Nashor"]},
            {"collab": [], "min_collab": 3},
            {"collab": ["A", "B"], "min_collab": 3, "excluded": ["Sona"]},
            {"size": 3, "min_collab": 5},
            {"excluded": [champion["name"] for champion in self.data["champions"]]},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.search(**overrides)
        self.data["champions"] = [
            {"name": "Tank1", "cost": 1, "slots": 2, "traits": [], "unlockable": False},
            {"name": "Tank2", "cost": 1, "slots": 2, "traits": [], "unlockable": False},
        ]
        with self.assertRaises(ValueError):
            self.search(collab=[])

    def test_invalid_data_raise_value_error(self):
        cases = [None, {}, {"traits": [], "champions": {}},
                 {"traits": [{"name": "Bad", "breakpoints": [2, 2], "unique": False}],
                  "champions": []}]
        for bad in cases:
            with self.subTest(data=bad), self.assertRaises(ValueError):
                self.search(data=bad)
        for field, value in (("slots", 0), ("cost", -1), ("traits", ["Missing"])):
            bad = copy.deepcopy(self.data)
            bad["champions"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.search(data=bad)

    def test_load_data_and_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            with self.assertRaises(ValueError):
                load_data(path)
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_data(path)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_data(path)
            path.write_text(json.dumps(self.data), encoding="utf-8-sig")
            self.assertEqual(load_data(path), self.data)

    def test_exported_band_names(self):
        self.assertEqual(len(BAND_TRAITS), len(set(BAND_TRAITS)))
        self.assertIn("K/DA", BAND_TRAITS)
        self.assertIn("Jazz", BAND_TRAITS)
        self.assertNotIn("Bodyguard", BAND_TRAITS)


if __name__ == "__main__":
    unittest.main()
