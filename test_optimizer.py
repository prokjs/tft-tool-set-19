"""Small deterministic engine regressions; run with python -m unittest test_optimizer."""

import copy
import json
import tempfile
import unittest
from itertools import combinations
from pathlib import Path

from optimizer import BAND_TRAITS, _evaluate_team, load_data, optimize, planned_stars


class LateCarryPresenceTests(unittest.TestCase):
    def test_capped_fielded_presence_and_level_gate(self):
        champions = {
            "Cheap": {"name": "Cheap", "cost": 1, "traits": [], "role": "Attack Marksman"},
            "Carry": {"name": "Carry", "cost": 3, "traits": [], "role": "Magic Caster"},
            "Core": {"name": "Core", "cost": 2, "traits": [], "role": "Magic Tank"},
        }
        def score(names, level=10, mode="balanced", stars=None):
            return _evaluate_team(names, {}, champions, [], mode, "", stars or {}, level)["score"]
        self.assertEqual(score(["Carry"]) - score(["Cheap"]), 300.0)
        self.assertEqual(score(["Carry", "Cheap"]), 320.0)
        self.assertEqual(score(["Carry"], 9), score(["Cheap"], 9))
        self.assertEqual(score(["Carry"], mode="synergies"), score(["Cheap"], mode="synergies"))
        self.assertEqual(score(["Cheap"], stars={"Core": 3}), 20.0)
        self.assertEqual(score(["Cheap", "Core"], stars={"Core": 3}), 700.0)
        self.assertEqual(score(["Cheap"], stars={"Cheap": 3}), 220.0)


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


def jazz_fixture():
    return {
        "traits": [
            {"name": name, "breakpoints": points, "unique": unique}
            for name, points, unique in [
                ("Collab", [3, 5], False), ("Jazz", [2, 3], False),
                ("Link", [2], False), ("Other", [2], False),
                ("Solo", [1, 2], True),
            ]
        ],
        "champions": [
            {"name": name, "cost": 1, "traits": traits, "unlockable": False}
            for name, traits in [
                ("BaseJazz", ["Jazz"]), ("Pool", ["Link"]), ("Support", ["Other"]),
                ("AMismatch", ["Jazz", "Other"]), ("ZMatch", ["Jazz", "Link"]),
            ]
        ],
    }


def frontline_fixture():
    return {
        "traits": [
            {"name": name, "breakpoints": [2, 4], "unique": False}
            for name in ("Bodyguard", "Juggernaut")
        ],
        "champions": [
            {"name": f"{prefix}{index}", "cost": 1, "traits": [trait], "unlockable": False}
            for prefix, trait in (("Body", "Bodyguard"), ("Jug", "Juggernaut"))
            for index in range(1, 5)
        ],
    }



def late_game_fixture():
    return {
        "traits": [],
        "champions": [
            {"name": f"F{index}", "cost": 1, "traits": [], "unlockable": False}
            for index in range(9)
        ] + [
            {"name": name, "cost": cost, "traits": [], "role": role, "unlockable": False}
            for name, cost, role in [
                ("FourTank", 4, "Magic Tank"), ("SecondTank", 4, "Attack Tank"),
                ("ThreeTank", 3, "Attack Tank"), ("Premium", 5, "Magic Caster"),
                ("Unknown", 4, ""), ("Senna", 5, "Magic Caster"),
            ]
        ],
    }


def strategy_fixture(trait_name):
    return {
        "traits": [{"name": trait_name, "breakpoints": [2, 4, 5], "unique": False}],
        "champions": [
            {"name": f"F{index}", "cost": 1, "traits": [], "unlockable": False}
            for index in range(10)
        ] + [
            {"name": f"Low{index}", "cost": 1, "traits": [trait_name], "unlockable": False}
            for index in range(1, 6)
        ] + [
            {"name": name, "cost": cost, "traits": traits, "unlockable": False}
            for name, cost, traits in [
                ("Qiyana", 2, [trait_name]), ("Ekko", 3, [trait_name]),
                ("Carry", 4, [trait_name]), ("Senna", 5, [trait_name]),
                ("Impostor", 5, []),
            ]
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

    def test_connected_jazz_beats_equivalent_unmatched_choices(self):
        self.data = jazz_fixture()
        fixed = ["BaseJazz", "Pool", "Support"]
        for mode in ("balanced", "high_value"):
            with self.subTest(mode=mode):
                connected = self.search(
                    collab=["Pool"], size=4, locked=fixed, mode=mode, limit=1,
                )[0]
                unmatched = self.search(
                    collab=["Pool"], size=4, locked=fixed + ["AMismatch"], mode=mode,
                )[0]
                self.assertIn("ZMatch", connected["champions"])
                for field in ("active", "tiers", "cost", "frontline"):
                    self.assertEqual(connected[field], unmatched[field])
                self.assertEqual(connected["score"] - unmatched["score"], 40.0)
        breadth = self.search(
            collab=["Pool"], size=4, locked=fixed, mode="synergies", limit=1,
        )[0]
        self.assertIn("AMismatch", breadth["champions"])

    def test_unfielded_pool_and_catalog_jazz_matches_do_not_add_bonus(self):
        self.data = jazz_fixture()
        self.data["champions"].append({
            "name": "Catalog", "cost": 1, "traits": ["Collab", "Other"],
            "unlockable": False,
        })
        boards = [
            ["BaseJazz", "Pool", "Support", "AMismatch"],
            ["BaseJazz", "ZMatch", "Support", "AMismatch"],
            ["BaseJazz", "AMismatch", "Catalog"],
        ]
        for mode in ("balanced", "high_value"):
            for names in boards:
                with self.subTest(mode=mode, names=names):
                    with_pool = self.search(
                        collab=["Pool"], size=len(names), locked=names, mode=mode,
                    )[0]
                    without_pool = self.search(
                        collab=[], size=len(names), locked=names, mode=mode,
                    )[0]
                    self.assertEqual(with_pool["score"], without_pool["score"])
                    self.assertEqual(with_pool["active"], without_pool["active"])
                    self.assertGreater(row(with_pool, "Jazz")["tier"], 0)

    def test_jazz_bonus_requires_activation_even_with_a_connection(self):
        self.data = jazz_fixture()
        for collab, names in [
            (["Pool"], ["Pool", "ZMatch"]),
            (["ZMatch"], ["ZMatch", "Support"]),
        ]:
            for mode in ("balanced", "high_value"):
                with self.subTest(mode=mode, collab=collab):
                    result = self.search(
                        collab=collab, size=2, locked=names, mode=mode,
                    )[0]
                    self.assertEqual(row(result, "Jazz")["tier"], 0)
                    expected = 110.0 * result["active"] + 20.0
                    if mode == "high_value":
                        expected += 30.0 * result["cost"]
                    self.assertEqual(result["score"], expected)

    def test_fielded_collab_jazz_qualifies_directly(self):
        self.data = jazz_fixture()
        for mode in ("balanced", "high_value"):
            with self.subTest(mode=mode):
                names = ["BaseJazz", "AMismatch", "Support"]
                direct = self.search(
                    collab=["AMismatch"], size=3, locked=names, mode=mode,
                )[0]
                ordinary = self.search(collab=[], size=3, locked=names, mode=mode)[0]
                self.assertEqual(direct["score"] - ordinary["score"], 40.0)
                self.assertEqual(row(direct, "Collab")["count"], 1)
                names = ["BaseJazz", "ZMatch", "Support"]
                unfielded = self.search(
                    collab=["AMismatch"], size=3, locked=names, mode=mode,
                )[0]
                ordinary = self.search(collab=[], size=3, locked=names, mode=mode)[0]
                self.assertEqual(unfielded["score"], ordinary["score"])

    def test_featured_sona_jazz_qualifies_only_when_fielded(self):
        self.data = jazz_fixture()
        self.data["champions"].append({
            "name": "Sona", "cost": 1, "traits": ["Collab"], "unlockable": False,
        })
        for mode in ("balanced", "high_value"):
            with self.subTest(mode=mode):
                featured = self.search(
                    collab=[], size=3, locked=["BaseJazz", "Sona", "Support"],
                    mode=mode, sona_band="Jazz",
                )[0]
                self.assertEqual(row(featured, "Jazz")["count"], 2)
                self.assertEqual(row(featured, "Collab")["count"], 1)
                self.assertEqual(featured["active"], 1)
                self.assertEqual(featured["tiers"], 1)
                self.assertEqual(featured["score"], 150.0 + (90.0 if mode == "high_value" else 0.0))
                names = ["BaseJazz", "ZMatch", "Support"]
                unfielded = self.search(
                    collab=[], size=3, locked=names, mode=mode, sona_band="Jazz",
                )[0]
                ordinary = self.search(collab=[], size=3, locked=names, mode=mode)[0]
                self.assertEqual(unfielded, ordinary)

    def test_unique_native_matches_neither_bridge_nor_gain_tier_weight(self):
        self.data = jazz_fixture()
        self.data["champions"] += [
            {"name": name, "cost": 1, "traits": traits, "unlockable": False}
            for name, traits in [
                ("UniqueJazz", ["Jazz", "Solo"]), ("UniquePool", ["Solo"]),
            ]
        ]
        for mode in ("balanced", "high_value"):
            with self.subTest(mode=mode):
                result = self.search(
                    collab=["UniquePool"], size=3,
                    locked=["BaseJazz", "UniqueJazz", "UniquePool"], mode=mode,
                )[0]
                self.assertEqual(row(result, "Solo")["tier"], 2)
                self.assertEqual(result["active"], 1)
                self.assertEqual(result["tiers"], 1)
                self.assertEqual(result["score"], 131.0 + (90.0 if mode == "high_value" else 0.0))

    def test_silver_frontline_outvalues_two_bronze_traits(self):
        self.data = frontline_fixture()
        for mode in ("balanced", "high_value"):
            mixed = self.search(
                collab=[], size=4, locked=["Body1", "Body2", "Jug1", "Jug2"], mode=mode,
            )[0]
            for prefix in ("Body", "Jug"):
                with self.subTest(mode=mode, trait=prefix):
                    silver = self.search(
                        collab=[], size=4, locked=[f"{prefix}{index}" for index in range(1, 5)],
                        mode=mode,
                    )[0]
                    self.assertEqual(silver["cost"], mixed["cost"])
                    self.assertEqual(silver["frontline"], mixed["frontline"])
                    self.assertEqual(silver["active"], 1)
                    self.assertEqual(silver["tiers"], 2)
                    self.assertEqual(silver["score"] - mixed["score"], 80.0)
            best = self.search(collab=[], size=4, mode=mode, limit=1)[0]
            self.assertEqual(best["active"], 1)
            self.assertEqual(best["tiers"], 2)
            self.assertGreater(best["score"], mixed["score"])

    def test_synergies_mode_keeps_breadth_over_silver_frontline(self):
        self.data = frontline_fixture()
        best = self.search(collab=[], size=4, mode="synergies", limit=1)[0]
        self.assertEqual(best["active"], 2)
        self.assertEqual(best["tiers"], 2)
        self.assertEqual(row(best, "Bodyguard")["count"], 2)
        self.assertEqual(row(best, "Juggernaut")["count"], 2)
        self.assertEqual(best["score"], 220.0)
        silver = self.search(
            collab=[], size=4, locked=["Jug1", "Jug2", "Jug3", "Jug4"], mode="synergies",
        )[0]
        self.assertEqual(silver["score"], 120.0)

    def test_jazz_beam_matches_final_scoring_with_locks_and_exclusions(self):
        for reverse_order in (False, True):
            self.data = jazz_fixture()
            aliases = {"Pool": "ZPool", "ZMatch": "AJazz"} if reverse_order else {}
            for champion in self.data["champions"]:
                champion["name"] = aliases.get(champion["name"], champion["name"])
            pool, jazz = aliases.get("Pool", "Pool"), aliases.get("ZMatch", "ZMatch")
            trait_map = {trait["name"]: trait for trait in self.data["traits"]}
            champion_map = {champion["name"]: champion for champion in self.data["champions"]}
            boards = list(combinations(sorted(set(champion_map) - {"AMismatch"}), 3))
            for locked in ([], [pool], [jazz], [pool, jazz]):
                for mode in ("balanced", "high_value", "synergies"):
                    with self.subTest(reverse_order=reverse_order, locked=locked, mode=mode):
                        evaluated = [
                            _evaluate_team(list(board), trait_map, champion_map, [pool], mode, "", {})
                            for board in boards if set(locked).issubset(board)
                        ]
                        expected = min(evaluated, key=lambda result: (
                            -result["score"], -result["active"], -result["tiers"],
                            -result["cost"] if mode == "high_value" else result["cost"],
                            result["champions"],
                        ))
                        actual = self.search(
                            collab=[pool], size=3, locked=locked, excluded=["AMismatch"],
                            mode=mode, limit=1,
                        )[0]
                        self.assertEqual(actual, expected)

    def test_planned_stars_use_only_sourced_four_cost_tanks_and_preserve_overrides(self):
        self.data = late_game_fixture()
        champions = {champion["name"]: champion for champion in self.data["champions"]}
        original = copy.deepcopy(champions)
        self.assertTrue(all(rank == 1 for rank in planned_stars(champions, 9).values()))
        ranks = planned_stars(champions, 10)
        self.assertEqual(ranks["FourTank"], 2)
        self.assertEqual(ranks["SecondTank"], 2)
        self.assertEqual(ranks["ThreeTank"], 1)
        self.assertEqual(ranks["Unknown"], 1)
        self.assertEqual(ranks["Premium"], 1)
        requested = {"FourTank": 1, "ThreeTank": 3, "Premium": 2}
        overrides = planned_stars(champions, 11, requested)
        for name, rank in requested.items():
            self.assertEqual(overrides[name], rank)
        self.assertEqual(requested, {"FourTank": 1, "ThreeTank": 3, "Premium": 2})
        self.assertEqual(champions, original)

    def test_late_game_search_prefers_an_upgraded_sourced_tank_anchor(self):
        self.data = late_game_fixture()
        fixed = [f"F{index}" for index in range(9)]
        for mode in ("balanced", "high_value"):
            with self.subTest(mode=mode):
                result = self.search(collab=[], size=10, locked=fixed, mode=mode, limit=1)[0]
                self.assertIn(result["frontline_anchor"], ("FourTank", "SecondTank"))
                self.assertEqual(result["planned_stars"][result["frontline_anchor"]], 2)
                self.assertEqual(result["frontline"], 1)
                self.assertTrue(any("Automatic tank upgrade target" in note for note in result["strategy_notes"]))
                self.assertTrue(any("★★" in note for note in result["strategy_notes"]))
                self.assertNotIn("ThreeTank", result["champions"])

    def test_anchor_bonus_is_best_only_and_explicit_three_star_tank_can_anchor(self):
        self.data = late_game_fixture()
        one = [f"F{index}" for index in range(9)] + ["FourTank"]
        two = [f"F{index}" for index in range(8)] + ["FourTank", "SecondTank"]
        for mode in ("balanced", "high_value"):
            first = self.search(collab=[], size=10, locked=one, mode=mode)[0]
            second = self.search(collab=[], size=10, locked=two, mode=mode)[0]
            self.assertEqual(second["score"] - first["score"], 30.0 + (90.0 if mode == "high_value" else 0.0))
            self.assertEqual(second["frontline_anchor"], "FourTank")
            names = [f"F{index}" for index in range(8)] + ["FourTank", "ThreeTank"]
            explicit = self.search(
                collab=[], size=10, locked=names, mode=mode, stars={"ThreeTank": 3},
            )[0]
            self.assertEqual(explicit["frontline_anchor"], "ThreeTank")
            self.assertEqual(explicit["planned_stars"]["ThreeTank"], 3)
            self.assertTrue(any("reroll-path penalty" in note for note in explicit["strategy_notes"]))

    def test_tank_anchor_defaults_do_not_leak_before_ten_or_override_manual_one_star(self):
        self.data = late_game_fixture()
        names = [f"F{index}" for index in range(8)] + ["FourTank"]
        early = self.search(collab=[], size=9, locked=names, mode="balanced")[0]
        self.assertEqual(early["planned_stars"]["FourTank"], 1)
        self.assertIsNone(early["frontline_anchor"])
        self.assertEqual(early["frontline"], 1)
        self.assertEqual(early["score"], 50.0)
        late = self.search(
            collab=[], size=10, locked=names + ["F8"], mode="balanced", stars={"FourTank": 1},
        )[0]
        self.assertEqual(late["planned_stars"]["FourTank"], 1)
        self.assertIsNone(late["frontline_anchor"])
        self.assertEqual(late["score"], 50.0)
        self.assertFalse(any("Automatic tank upgrade" in note for note in late["strategy_notes"]))

    def test_gu_feng_splash_is_discounted_until_four_actual_members(self):
        self.data = strategy_fixture("Gu Feng")
        for level in (9, 10):
            for count, expected in ((2, 38.5), (4, 300.0)):
                names = [f"Low{index}" for index in range(1, count + 1)]
                names += [f"F{index}" for index in range(level - count)]
                result = self.search(collab=[], size=level, locked=names, mode="balanced")[0]
                self.assertEqual(row(result, "Gu Feng")["count"], count)
                self.assertEqual(result["active"], 1)
                self.assertEqual(result["tiers"], 1 if count == 2 else 2)
                self.assertEqual(result["score"], expected + 20.0)
                self.assertEqual(any("Gu Feng" in note for note in result["strategy_notes"]), count < 4)
        names = ["Low1", "Carry"] + [f"F{index}" for index in range(8)]
        upgraded = self.search(
            collab=[], size=10, locked=names, mode="balanced", stars={"Carry": 2},
        )[0]
        self.assertEqual(upgraded["score"], 58.5)

    def test_true_damage_requires_a_fielded_eligible_upgraded_carry_or_five_members(self):
        self.data = strategy_fixture("True Damage")
        for carry, rank, unit_penalty in (("Qiyana", 2, 60.0), ("Ekko", 3, 100.0), ("Carry", 2, 0.0)):
            names = ["Low1", carry] + [f"F{index}" for index in range(8)]
            with self.subTest(carry=carry):
                result = self.search(
                    collab=[], size=10, locked=names, mode="balanced", stars={carry: rank},
                )[0]
                self.assertEqual(result["score"], 130.0 - unit_penalty)
                self.assertEqual(result["active"], 1)
                self.assertEqual(result["tiers"], 1)
                self.assertFalse(any("splash value" in note for note in result["strategy_notes"]))
        names = ["Low1", "Low2"] + [f"F{index}" for index in range(8)]
        for overrides in ({}, {"Carry": 2}, {"Qiyana": 2}, {"Ekko": 3}):
            result = self.search(
                collab=[], size=10, locked=names, mode="balanced", stars=overrides,
            )[0]
            self.assertEqual(result["score"], 58.5)
        names = ["Low1", "Low2", "Impostor"] + [f"F{index}" for index in range(7)]
        impostor = self.search(
            collab=[], size=10, locked=names, mode="balanced", stars={"Impostor": 2},
        )[0]
        self.assertEqual(impostor["score"], 58.5)
        names = [f"Low{index}" for index in range(1, 6)] + [f"F{index}" for index in range(5)]
        deep = self.search(collab=[], size=10, locked=names, mode="balanced")[0]
        self.assertEqual(deep["score"], 590.0)
        self.assertEqual(deep["tiers"], 3)

    def test_hyperpop_needs_a_fielded_upgrade_only_at_late_levels(self):
        self.data = strategy_fixture("Hyperpop")
        for level, expected in ((9, 130.0), (10, 58.5)):
            names = ["Low1", "Low2"] + [f"F{index}" for index in range(level - 2)]
            ordinary = self.search(collab=[], size=level, locked=names, mode="balanced")[0]
            self.assertEqual(ordinary["score"], expected)
            upgraded = self.search(
                collab=[], size=level, locked=names, mode="balanced", stars={"Low2": 2},
            )[0]
            self.assertEqual(upgraded["score"], 130.0)
            bench_upgrade = self.search(
                collab=[], size=level, locked=names, mode="balanced", stars={"Carry": 2},
            )[0]
            self.assertEqual(bench_upgrade["score"], expected)
        names = [f"Low{index}" for index in range(1, 5)] + [f"F{index}" for index in range(6)]
        deep = self.search(collab=[], size=10, locked=names, mode="balanced")[0]
        self.assertEqual(deep["score"], 320.0)

    def test_featured_sona_membership_can_supply_a_real_upgraded_carry(self):
        for trait_name in ("True Damage", "Hyperpop"):
            self.data = strategy_fixture(trait_name)
            self.data["traits"].append({"name": "Collab", "breakpoints": [3, 5], "unique": False})
            self.data["champions"].append({
                "name": "Sona", "cost": 5, "traits": ["Collab"], "unlockable": False,
            })
            names = ["Low1", "Sona"] + [f"F{index}" for index in range(8)]
            featured = self.search(
                collab=[], size=10, locked=names, mode="balanced",
                sona_band=trait_name, stars={"Sona": 2},
            )[0]
            self.assertEqual(featured["score"], 130.0)
            self.assertEqual(row(featured, trait_name)["count"], 2)
            self.assertFalse(any("splash value" in note for note in featured["strategy_notes"]))
            names = ["Low1", "Low2"] + [f"F{index}" for index in range(8)]
            bench = self.search(
                collab=[], size=10, locked=names, mode="balanced",
                sona_band=trait_name, stars={"Sona": 2},
            )[0]
            self.assertEqual(bench["score"], 58.5)

    def test_senna_is_penalized_not_banned_and_cannot_unlock_true_damage(self):
        self.data = strategy_fixture("True Damage")
        for level in (9, 10):
            names = ["Low1", "Senna"] + [f"F{index}" for index in range(level - 2)]
            result = self.search(
                collab=[], size=level, locked=names, mode="balanced", stars={"Senna": 2},
            )[0]
            self.assertIn("Senna", result["champions"])
            self.assertEqual(result["score"], 58.5 - 350.0)
            self.assertTrue(any("Senna" in note for note in result["strategy_notes"]))
        names = ["Low1"] + [f"F{index}" for index in range(8)]
        result = self.search(collab=[], size=10, locked=names, mode="high_value", limit=1)[0]
        self.assertNotIn("Senna", result["champions"])

    def test_fast_ten_reroll_tax_applies_to_targets_without_double_taxing_qiyana(self):
        self.data = late_game_fixture()
        self.data["champions"].append({
            "name": "Qiyana", "cost": 2, "traits": [], "unlockable": False,
        })
        for name, rank, tax in (("ThreeTank", 3, 100.0), ("Qiyana", 2, 60.0), ("Qiyana", 3, 100.0)):
            names = [f"F{index}" for index in range(9)] + [name]
            result = self.search(
                collab=[], size=10, locked=names, mode="balanced", stars={name: rank},
            )[0]
            anchor = 450.0 if name == "ThreeTank" else 0.0
            balance = 50.0 if name == "ThreeTank" else 20.0
            self.assertEqual(result["score"], anchor + balance - tax)
            self.assertEqual(result["planned_stars"][name], rank)
            early = self.search(
                collab=[], size=9, locked=names[1:], mode="balanced", stars={name: rank},
            )[0]
            self.assertEqual(early["score"], balance)

    def test_synergies_mode_ignores_all_late_game_valuation_preferences(self):
        self.data = strategy_fixture("True Damage")
        names = ["Low1", "Senna"] + [f"F{index}" for index in range(8)]
        result = self.search(
            collab=[], size=10, locked=names, mode="synergies", stars={"Senna": 2, "Low1": 3},
        )[0]
        self.assertEqual(result["score"], 110.0)
        self.data = late_game_fixture()
        names = [f"F{index}" for index in range(8)] + ["FourTank", "ThreeTank"]
        result = self.search(
            collab=[], size=10, locked=names, mode="synergies", stars={"ThreeTank": 3},
        )[0]
        self.assertEqual(result["score"], 0.0)
        self.assertIsNone(result["frontline_anchor"])

    def test_strategy_beam_matches_final_values_with_fixed_and_optional_readiness(self):
        for trait_name in ("Gu Feng", "True Damage", "Hyperpop"):
            self.data = strategy_fixture(trait_name)
            fixed = [f"F{index}" for index in range(8)]
            allowed = set(fixed) | {"Low1", "Low2", "Carry", "Qiyana", "Ekko", "Impostor", "Senna"}
            self.data["champions"] = [
                champion for champion in self.data["champions"] if champion["name"] in allowed
            ]
            trait_map = {trait["name"]: trait for trait in self.data["traits"]}
            champion_map = {champion["name"]: champion for champion in self.data["champions"]}
            optional = sorted(set(champion_map) - set(fixed) - {"Senna"})
            boards = [sorted(fixed + list(pair)) for pair in combinations(optional, 2)]
            stars = {"Carry": 2, "Qiyana": 2, "Ekko": 3, "Low2": 2, "Impostor": 2}
            for locked in (fixed, fixed + ["Carry"]):
                for mode in ("balanced", "high_value", "synergies"):
                    with self.subTest(trait=trait_name, locked_carry="Carry" in locked, mode=mode):
                        evaluated = [
                            _evaluate_team(board, trait_map, champion_map, [], mode, "", stars, 10)
                            for board in boards if set(locked).issubset(board)
                        ]
                        expected = min(evaluated, key=lambda result: (
                            -result["score"], -result["active"], -result["tiers"],
                            -result["cost"] if mode == "high_value" else result["cost"],
                            result["champions"],
                        ))
                        actual = self.search(
                            collab=[], size=10, locked=locked, excluded=["Senna"],
                            mode=mode, stars=stars, limit=1,
                        )[0]
                        self.assertEqual(actual, expected)

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


class KdaPlannedStarsTests(unittest.TestCase):
    def test_fielded_star_sum_and_dynamic_sona_preserve_manual_rerolls(self):
        champions = {
            name: {"name": name, "cost": cost, "traits": traits}
            for name, cost, traits in [("A", 1, ["K/DA"]), ("B", 2, ["K/DA"]),
                                       ("Absent", 1, ["K/DA"]), ("Sona", 7, [])]
        }
        traits = {"K/DA": {"name": "K/DA", "breakpoints": [3, 5, 7, 10],
                          "unique": False, "count_mode": "stars"},
                  "Collab": {"name": "Collab", "breakpoints": [3, 5], "unique": False}}
        def count(level, stars, band="K/DA", names=("A", "B", "Sona")):
            result = _evaluate_team(list(names), traits, champions, [], "balanced", band, stars, level)
            return next(row["count"] for row in result["traits"] if row["name"] == "K/DA")
        self.assertEqual(count(10, {}), 6)
        self.assertEqual(count(10, {"A": 1, "Absent": 4}), 5)
        self.assertEqual(count(8, {"A": 3, "B": 3}, "", ("A", "B")), 6)
        self.assertEqual(planned_stars(champions, 8, sona_band="K/DA")["A"], 1)
        self.assertEqual(planned_stars(champions, 10)["Sona"], 1)


if __name__ == "__main__":
    unittest.main()
