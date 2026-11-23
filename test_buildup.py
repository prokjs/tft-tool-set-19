"""Consumer-visible deterministic buildup regressions; no network or UI required."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from buildup import (SHOP_SOURCE, _Planner, _chain_exclusions, _prep_recipes,
                     _upgrade_phases, format_buildup, recommend_with_buildups, replan_buildup)
from optimizer import _evaluate_team, optimize


def fixture():
    def champion(name, traits, cost=1, **extra):
        return {"name": name, "traits": traits, "cost": cost, "unlockable": False, **extra}

    def quest(name, traits, cost, condition, level="Any", **extra):
        return champion(name, traits, cost, unlockable=True, unlock_condition=condition,
                        unlock_level=level, unlock_source="https://example.test/unlocks", **extra)

    return {
        "traits": [
            {"name": name, "breakpoints": points, "unique": False, **extra}
            for name, points, extra in [
                ("Collab", [3, 5], {}), ("Bodyguard", [2, 4], {}),
                ("Juggernaut", [2], {}), ("Frontman", [2], {}),
                ("K/DA", [3, 5, 7], {"count_mode": "stars"}),
                ("Jazz", [2, 3], {}), ("Arcane", [2, 4], {}),
                ("Gu Feng", [2, 3], {}),
            ]
        ],
        "champions": [
            champion("A", ["Bodyguard"]), champion("B", ["K/DA"]),
            champion("C", ["Jazz"]), champion("D", ["Arcane"]),
            champion("E", ["Juggernaut"]), champion("F", ["Frontman"]),
            champion("G", ["K/DA", "Arcane"]), champion("H", ["Jazz", "Bodyguard"]),
            champion("I", ["Gu Feng"]), champion("J", ["Arcane"]),
            champion("KDA Helper 1", ["K/DA"]), champion("KDA Helper 2", ["K/DA"]),
            champion("KDA Helper 3", ["K/DA"]),
            champion("Annie", ["Arcane"], 2), champion("Xayah", ["Gu Feng"], 4),
            champion("Late Guard", ["Bodyguard"], 4),
            champion("Premium", ["Bodyguard", "K/DA"], 5),
            quest("Tibbers", ["Arcane", "Bodyguard"], 3,
                  "Have a 2-star Annie holding 1 item."),
            quest("Gu Feng Quest", ["Gu Feng"], 5,
                  "Have allies deal 25,000 damage while fielding Xayah."),
            quest("Sona", ["Jazz"], 7,
                  "Have Collab champions become inspired 15 times.", "Level 10"),
            quest("Baron Nashor", ["Juggernaut"], 7,
                  "Collect all 5 ensemble members and deal 60,000 damage with Malzahar.", slots=2),
            quest("Akali (K/DA)", ["K/DA"], 5,
                  "Have 12 unique star levels of K/DA without Akali (True Damage) unlocked.",
                  exclusive_group="akali"),
            quest("Akali (True Damage)", ["Arcane"], 5,
                  "Earn Bling without Akali (K/DA) unlocked.", exclusive_group="akali"),
        ],
    }


def actual_phases(result):
    return [phase for step in result["buildup"] for phase in [step] + step["upgrades"]]


def assert_explicit_stars(test, result):
    previous = {}
    for phase in actual_phases(result):
        if phase["board"] is None:
            continue
        ranks = phase["board"]["planned_stars"]
        upgrades = {row["champion"]: row for row in phase.get("star_upgrades", [])}
        replacements = {row["champion"]: row for row in phase.get("copy_replacements", [])}
        for name, rank in ranks.items():
            before = previous.get(name, 1)
            if rank > before:
                test.assertEqual(upgrades.get(name), {"champion": name, "from": before, "to": rank})
            if rank < before:
                test.assertEqual(replacements.get(name), {"champion": name, "from": before, "to": rank})
        previous = ranks


class BuildupTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.champions = {c["name"]: c for c in self.data["champions"]}

    def search(self, **changes):
        options = dict(data=self.data, collab=["A", "B", "C", "D", "E"], size=10,
                       locked=[], excluded=[], min_collab=3, mode="balanced", limit=1)
        options.update(changes)
        return recommend_with_buildups(**options)

    def slots(self, board):
        return sum(self.champions[name].get("slots", 1) for name in board["champions"])

    def test_results_obey_limit_and_have_complete_deterministic_nonmutating_builds(self):
        before = copy.deepcopy(self.data)
        arguments = {"stars": {"B": 3}, "limit": 12}
        options_before = copy.deepcopy(arguments)
        first = self.search(**arguments)
        self.assertTrue(first)
        self.assertLessEqual(len(first), arguments["limit"])
        self.assertEqual(first, self.search(**arguments))
        self.assertEqual(self.data, before)
        self.assertEqual(arguments, options_before)
        for result in first:
            self.assertEqual([step["level"] for step in result["buildup"]], list(range(1, 11)))
            for step in result["buildup"]:
                self.assertEqual(self.slots(step["board"]), step["level"])
                self.assertEqual(self.slots(step["fallback"]), step["level"])
            self.assertIsNot(result["buildup"][-1]["board"], result)
        # Consumer JSON export must not encounter a circular reference.
        json.dumps(first)
        # Cached planning boards cannot alias other results or fallback rows.
        other = copy.deepcopy(first[1])
        first[0]["buildup"][0]["board"]["champions"].append("changed")
        first[0]["buildup"][0]["board"]["planned_stars"]["changed"] = 4
        first[0]["buildup"][0]["board"]["strategy_notes"].append("changed")
        self.assertEqual(first[1], other)

    def test_sona_never_appears_at_level_nine_and_early_shops_are_cheap(self):
        result = self.search(locked=["Sona"])[0]
        for step in result["buildup"][:-1]:
            self.assertNotIn("Sona", step["board"]["champions"])
        final = result["buildup"][-1]
        self.assertIn("Sona", final["goal_board"]["champions"])
        self.assertIn("Sona", (final["upgrades"][-1]["board"] if final["upgrades"] else final["board"])["champions"])
        for step in result["buildup"][:2]:
            self.assertTrue(all(self.champions[name]["cost"] == 1 for name in step["board"]["champions"]))
        early = result["buildup"][2]["board"]
        self.assertGreaterEqual(next(row["count"] for row in early["traits"] if row["name"] == "Collab"), 3)
        with self.assertRaisesRegex(ValueError, "Sona.*level 9.*level 10"):
            self.search(size=9, locked=["Sona"])
        for candidate in self.search(size=9, limit=3):
            self.assertNotIn("Sona", candidate["champions"])

    def test_six_pool_champions_plus_sona_are_all_counted(self):
        pool = ["A", "B", "C", "D", "E", "F"]
        result = self.search(collab=pool, locked=pool + ["Sona"], min_collab=5)[0]
        collab = next(trait for trait in result["traits"] if trait["name"] == "Collab")
        self.assertEqual(collab["count"], 7)
        self.assertEqual(collab["tier"], 5)
        for step in result["buildup"]:
            board = step["board"]
            expected = sum(name in set(pool) | {"Sona"} for name in board["champions"])
            actual = next((trait["count"] for trait in board["traits"] if trait["name"] == "Collab"), 0)
            self.assertEqual(actual, expected)
        final = result["buildup"][-1]
        self.assertEqual(final["goal_board"]["champions"], result["champions"])
        self.assertEqual((final["upgrades"][-1]["board"] if final["upgrades"] else final["board"])["champions"],
                         result["champions"])

    def test_final_gating_and_exact_optimizer_stats(self):
        # At level 3 only the ordinary one/two-cost champions are shop-eligible.
        excluded = [name for name, c in self.champions.items() if c["cost"] > 2]
        for mode in ("balanced", "high_value", "synergies"):
            with self.subTest(mode=mode):
                result = self.search(size=3, min_collab=0, limit=4, stars={"B": 3}, mode=mode)
                expected = optimize(self.data, ["A", "B", "C", "D", "E"], 3,
                                    [], excluded, 0, mode, limit=4, stars={"B": 3})
                self.assertEqual([{key: item[key] for key in expected[0]} for item in result], expected)
                for item in result:
                    final = item["buildup"][-1]
                    expected_board = {key: item[key] for key in expected[0]}
                    self.assertEqual({key: final["goal_board"][key] for key in expected_board}, expected_board)
                    final_board = final["upgrades"][-1]["board"] if final["upgrades"] else final["board"]
                    self.assertEqual({key: final_board[key] for key in expected_board}, expected_board)
                    for step in item["buildup"][:-1]:
                        self.assertTrue(all(star == 1 for star in step["stars"].values()))
        with self.assertRaisesRegex(ValueError, "Premium.*level 3.*5-cost"):
            self.search(size=3, min_collab=0, locked=["Premium"])
        with self.assertRaisesRegex(ValueError, "Xayah.*level 4.*4-cost"):
            self.search(size=4, min_collab=0, locked=["Xayah"])

    def test_final_fallback_stars_and_traits_match_displayed_assumptions(self):
        result = self.search(size=3, min_collab=0, locked=["B"], stars={"B": 3})[0]
        final = result["buildup"][-1]
        self.assertIn("B", final["fallback"]["champions"])
        for board in (final["board"], final["fallback"]):
            expected = sum(board["planned_stars"][name] for name in board["champions"]
                           if "K/DA" in self.champions[name]["traits"])
            actual = next(row["count"] for row in board["traits"] if row["name"] == "K/DA")
            self.assertEqual(actual, expected)
        text = format_buildup(result, self.champions)
        self.assertIn("B (★★★)", text)
        self.assertNotIn("B (3★)", text)

    def test_intermediate_defensive_silver_outvalues_two_bronzes_in_value_modes(self):
        traits = {
            name: {"name": name, "breakpoints": [2, 4], "unique": False}
            for name in ("Juggernaut", "Bodyguard")
        }
        champions = {
            name: {"name": name, "traits": [trait], "cost": 1}
            for trait, prefix, count in (("Juggernaut", "J", 4), ("Bodyguard", "B", 2))
            for name in (f"{prefix}{index}" for index in range(count))
        }
        for mode in ("balanced", "high_value", "synergies"):
            with self.subTest(mode=mode):
                planner = _Planner(traits, champions, [], mode, "")
                board = planner.board(4, sorted(champions), set(), set(), set(), False)
                if mode == "synergies":
                    self.assertEqual(board["active"], 2)
                    self.assertEqual(board["tiers"], 2)
                    self.assertEqual(board["score"], 220)
                else:
                    self.assertEqual(board["champions"], ["J0", "J1", "J2", "J3"])
                    self.assertEqual(board["active"], 1)
                    self.assertEqual(board["tiers"], 2)
                    self.assertEqual(board["score"], 480 if mode == "high_value" else 360)

    def test_intermediate_jazz_bonus_needs_fielded_bridge_and_activation(self):
        traits = {
            name: {"name": name, "breakpoints": points, "unique": False}
            for name, points in (("Collab", [2]), ("Jazz", [2, 3]),
                                 ("Link", [2]), ("Choice", [1]))
        }
        champions = {
            name: {"name": name, "traits": names, "cost": 1}
            for name, names in (
                ("Core Jazz", ["Jazz", "Link"]), ("Core Collab", ["Link"]),
                ("Offboard Collab", ["Link"]), ("A Ordinary", ["Choice"]),
                ("Z Jazz", ["Jazz"]),
            )
        }
        pool = ["Core Jazz", "Core Collab", "A Ordinary", "Z Jazz"]
        weights = {"Core Jazz": 1000, "Core Collab": 1000, "A Ordinary": 30, "Z Jazz": 0}
        for mode in ("balanced", "high_value", "synergies"):
            for collab in ("Core Collab", "Offboard Collab"):
                for order in (pool, list(reversed(pool))):
                    with self.subTest(mode=mode, collab=collab, order=order):
                        planner = _Planner(traits, champions, [collab], mode, "")
                        board = planner._search(3, order, weights, False)
                        qualified = collab == "Core Collab" and mode != "synergies"
                        self.assertEqual(board, sorted([
                            "Core Jazz", "Core Collab", "Z Jazz" if qualified else "A Ordinary",
                        ]))
        # A real native bridge still adds nothing before the Jazz breakpoint.
        planner = _Planner(traits, champions, ["Core Collab"], "balanced", "")
        board = planner._search(
            2, ["Core Collab", "Core Jazz", "A Ordinary"],
            {"Core Collab": 1000, "Core Jazz": 0, "A Ordinary": 30}, False,
        )
        self.assertEqual(board, ["A Ordinary", "Core Collab"])

    def test_level_ten_intermediate_uses_planned_stars_and_exact_shared_stats(self):
        self.champions["Late Guard"]["role"] = "Magic Tank"
        self.champions["Late Guard"]["traits"].append("K/DA")
        collab = ["A", "B", "C", "D", "E"]
        requested = {"B": 4}
        result = self.search(size=11, collab=collab, locked=["B", "Late Guard"],
                             stars=requested)[0]
        self.assertEqual(result["collab_pool"], collab)
        self.assertEqual(result["sona_band"], "")
        collab.append("F")
        self.assertNotIn("F", result["collab_pool"])
        assert_explicit_stars(self, result)
        for step in result["buildup"]:
            level = step["level"]
            for board in [step["board"], step["fallback"]] + [phase["board"] for phase in step["upgrades"]]:
                if board is None:
                    continue
                expected = _evaluate_team(
                    board["champions"], {t["name"]: t for t in self.data["traits"]},
                    self.champions, result["collab_pool"], "balanced", "", board["planned_stars"],
                    level=level)
                self.assertEqual({key: board[key] for key in expected if key != "strategy_notes"},
                                 {key: value for key, value in expected.items() if key != "strategy_notes"})
        self.assertIn("Late Guard (★★)", format_buildup(result, self.champions))
        self.assertEqual(result["planned_stars"]["Late Guard"], 2)
        self.assertEqual(requested, {"B": 4})
        final = result["buildup"][-1]["goal_board"]
        final["planned_stars"]["Late Guard"] = 1
        final["strategy_notes"].append("changed")
        self.assertEqual(result["planned_stars"]["Late Guard"], 2)
        self.assertNotIn("changed", result["strategy_notes"])
        json.dumps(result)

    def test_intermediate_anchor_is_best_only_and_explicit_stars_override_defaults(self):
        traits = {"Bodyguard": {"name": "Bodyguard", "breakpoints": [20], "unique": False}}
        champions = {
            name: {"name": name, "traits": ["Bodyguard"], "cost": 4,
                   **({"role": "Attack Tank"} if name in ("Anchor", "Second Tank") else {})}
            for name in ["Anchor", "Second Tank", "Other"] + [f"Fill {i}" for i in range(8)]
        }
        fixed = ["Anchor"] + [f"Fill {i}" for i in range(8)]
        weights = {name: 1000 if name in fixed else 10 if name == "Other" else 0
                   for name in champions}
        planner = _Planner(traits, champions, [], "balanced", "", {}, 11)
        board = planner._search(10, list(champions), weights, False)
        self.assertEqual(board, sorted(fixed + ["Other"]))
        self.assertEqual(planner.stage_stars[9]["Anchor"], 1)
        self.assertEqual(planner.stage_stars[10]["Anchor"], 2)
        planner = _Planner(traits, champions, [], "balanced", "", {"Anchor": 1}, 11)
        board = planner._search(10, list(champions), weights, False)
        self.assertEqual(board, sorted(fixed + ["Second Tank"]))
        self.assertEqual(planner.stage_stars[10]["Anchor"], 1)

    def test_intermediate_true_damage_readiness_requires_fielded_planned_carry(self):
        traits = {
            name: {"name": name, "breakpoints": points, "unique": False}
            for name, points in (("True Damage", [2]), ("Choice", [1]), ("Padding", [20]))
        }
        fixed = ["TD 1", "TD 2"] + [f"Fill {i}" for i in range(7)]
        champions = {
            name: {"name": name, "traits": ["True Damage" if name.startswith("TD") else "Padding"],
                   "cost": 1}
            for name in fixed
        }
        champions.update({
            name: {"name": name, "traits": [trait], "cost": 4}
            for name, trait in (("Carry", "True Damage"), ("Unfielded Carry", "True Damage"),
                                ("Ordinary", "Choice"))
        })
        pool = fixed + ["Carry", "Ordinary"]
        weights = {name: 1000 if name in fixed else 45 if name == "Carry" else 0
                   for name in pool}
        for rank, choice in ((1, "Ordinary"), (2, "Carry")):
            for order in (pool, list(reversed(pool))):
                with self.subTest(rank=rank, order=order):
                    planner = _Planner(traits, champions, [], "balanced", "",
                                       {"Carry": rank, "Unfielded Carry": 2}, 11)
                    board = planner._search(10, order, weights, False)
                    self.assertEqual(board, sorted(fixed + [choice]))

    def test_stage_star_count_cache_handles_all_eleven_four_star_units(self):
        traits = {"K/DA": {"name": "K/DA", "breakpoints": [12, 24, 44],
                           "unique": False, "count_mode": "stars"}}
        champions = {
            f"K{i}": {"name": f"K{i}", "traits": ["K/DA"], "cost": 1}
            for i in range(11)
        }
        planner = _Planner(traits, champions, [], "balanced", "",
                           {name: 4 for name in champions}, 11)
        board = planner.board(11, sorted(champions), set(), set(), set(), False)
        self.assertEqual(board["traits"][0]["count"], 44)
        self.assertEqual(board["traits"][0]["tier"], 44)
        self.assertEqual(set(board["planned_stars"].values()), {4})

    def test_intermediate_senna_preference_is_not_a_ban_or_synergies_change(self):
        traits = {"Padding": {"name": "Padding", "breakpoints": [20], "unique": False}}
        champions = {
            name: {"name": name, "traits": ["Padding"], "cost": 1}
            for name in ("Fixed", "Senna", "Other")
        }
        weights = {"Fixed": 1000, "Senna": 50, "Other": 0}
        for mode in ("balanced", "high_value", "synergies"):
            planner = _Planner(traits, champions, [], mode, "")
            expected = "Senna" if mode == "synergies" else "Other"
            self.assertEqual(planner._search(2, list(champions), weights, False),
                             sorted(["Fixed", expected]))
            self.assertEqual(planner._search(2, ["Fixed", "Senna"], weights, False),
                             ["Fixed", "Senna"])

    def test_temporary_swaps_and_target_specific_paths(self):
        first = self.search(size=7, min_collab=0,
                            locked=["A", "B", "C", "D", "G", "Late Guard", "Xayah"])[0]
        second = self.search(size=7, min_collab=0,
                             locked=["E", "F", "G", "H", "I", "J", "Tibbers"])[0]
        self.assertNotEqual(first["buildup"][0]["board"]["champions"],
                            second["buildup"][0]["board"]["champions"])
        self.assertTrue(first["buildup"][5]["temporary"])
        self.assertIn("Xayah", first["buildup"][-1]["goal_board"]["champions"])
        self.assertTrue(any(step["removed"] for step in first["buildup"]) or
                        first["buildup"][-1]["upgrades"])
        previous = set()
        for step in first["buildup"]:
            current = set(step["board"]["champions"])
            self.assertEqual(step["added"], sorted(current - previous))
            self.assertEqual(step["removed"], sorted(previous - current))
            self.assertEqual(step["retained"], sorted(previous & current))
            self.assertEqual(step["temporary"], sorted(current - set(first["champions"])))
            previous = set((step["upgrades"][-1]["board"] if step["upgrades"] else step["board"])["champions"])

    def test_baron_occupies_two_slots_and_gate_is_labeled_heuristic(self):
        result = self.search(locked=["Baron Nashor"])[0]
        for step in result["buildup"]:
            self.assertEqual(self.slots(step["board"]), step["level"])
            if step["level"] < 9:
                self.assertNotIn("Baron Nashor", step["board"]["champions"])
        self.assertEqual(len(result["champions"]), 9)
        text = format_buildup(result, self.champions)
        self.assertIn("not a published unlock minimum", text)
        self.assertIn("2 board slots", text)
        with self.assertRaisesRegex(ValueError, "Baron Nashor.*level 8.*level 9"):
            self.search(size=8, locked=["Baron Nashor"])

    def test_exclusions_exclusivity_and_weighted_constraints_are_preserved(self):
        result = self.search(locked=["Akali (K/DA)"], excluded=["A", "Xayah"])[0]
        for step in result["buildup"]:
            for board in (step["board"], step["fallback"]):
                self.assertFalse({"A", "Xayah"} & set(board["champions"]))
                self.assertNotIn("Akali (True Damage)", board["champions"])
            self.assertFalse(any("Field Akali (True Damage)" in item for item in step["milestones"]))
        for locked in (["A"], ["Akali (K/DA)", "Akali (True Damage)"]):
            with self.assertRaises(ValueError):
                self.search(locked=locked, excluded=["A"])
        with self.assertRaises(ValueError):
            self.search(size=9, locked=["Baron Nashor", "A", "B", "C", "D", "E", "F", "G", "H"])
        with self.assertRaises(ValueError):
            self.search(size=10, min_collab=5, collab=["A", "B"], excluded=["Sona"])

    def test_conditional_tasks_helpers_and_all_quests_incomplete_fallback(self):
        result = self.search(locked=["Tibbers", "Gu Feng Quest"], stars={"B": 3})[0]
        text = format_buildup(result, list(self.champions.values()))
        self.assertIn("Field Annie", text)
        self.assertIn("Field Xayah", text)
        self.assertIn("2-star Annie holding 1 item", text)
        self.assertIn("25,000 damage", text)
        self.assertIn("https://example.test/unlocks", text)
        self.assertIn("conditional unlock", text)
        self.assertIn(SHOP_SOURCE, text)
        for step in result["buildup"]:
            self.assertFalse(any(self.champions[name]["unlockable"] for name in step["fallback"]["champions"]))
            required = {name for name in step["board"]["champions"] if self.champions[name]["unlockable"]}
            self.assertEqual({unlock["name"] for unlock in step["unlocks"]}, required)
        phases = actual_phases(result)
        first = next(i for i, phase in enumerate(phases) if "Tibbers" in phase["board"]["champions"])
        self.assertTrue(any(phase["board"]["planned_stars"].get("Annie") == 2 for phase in phases[:first]))
        assert_explicit_stars(self, result)

    def test_missing_unlock_tasks_are_rejected_not_filled_with_placeholders(self):
        for field in ("unlock_condition", "unlock_level", "unlock_source"):
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                next(champion for champion in data["champions"] if champion["name"] == "Sona").pop(field)
                with self.assertRaisesRegex(ValueError, f"Sona.*{field}"):
                    self.search(data=data, locked=["Sona"])

    def test_no_feasible_early_board_and_no_unlock_alternative_are_honest(self):
        self.data["champions"] = [{
            "name": "Only", "cost": 3, "traits": ["Arcane"], "slots": 4,
            "unlockable": True, "unlock_condition": "Perform a task.", "unlock_level": "Any",
            "unlock_source": "https://example.test/unlocks",
        }]
        self.champions = {c["name"]: c for c in self.data["champions"]}
        result = self.search(size=4, collab=[], min_collab=0, locked=["Only"])[0]
        self.assertEqual(len(result["buildup"]), 4)
        for step in result["buildup"][:3]:
            self.assertIsNone(step["board"])
            self.assertIn("No feasible exact-slot board", step["reason"])
            self.assertEqual(step["added"], [])
        for step in result["buildup"]:
            self.assertIsNone(step["fallback"])
            self.assertTrue(any("No exact-slot no-unlock alternative" in item for item in step["milestones"]))
        self.assertIn("no feasible exact-slot board", format_buildup(result, self.champions))

    def test_level_eleven_requires_special_cap_extension(self):
        result = self.search(size=11)[0]
        self.assertEqual(self.slots(result["buildup"][-1]["board"]), 11)
        self.assertIn("special level-cap extension", format_buildup(result, self.champions))

    def test_conservative_shop_timing_and_published_unlock_minimum(self):
        for size, name, minimum in ((5, "Xayah", 7), (6, "Xayah", 7), (8, "Premium", 9)):
            with self.subTest(size=size, name=name):
                with self.assertRaisesRegex(ValueError, f"{name}.*planning level {minimum}"):
                    self.search(size=size, min_collab=0, locked=[name])
        tibbers = self.champions["Tibbers"]
        tibbers["unlock_level"] = "Level 7"
        with self.assertRaisesRegex(ValueError, "Tibbers.*Level 7"):
            self.search(size=6, min_collab=0, locked=["Tibbers"])


class RealisticPathTests(unittest.TestCase):
    def make_data(self, baron=False):
        traits = [
            {"name": "Padding", "breakpoints": [99], "unique": False},
            {"name": "Collab", "breakpoints": [3], "unique": False},
        ]
        cheap = [{"name": f"Starter {i}", "traits": ["Padding"], "cost": 1,
                  "unlockable": False} for i in range(11)]
        legends = [{"name": f"Legend {i}", "traits": ["Padding"], "cost": 5,
                    "unlockable": False} for i in range(8 if baron else 10)]
        if baron:
            legends.append({
                "name": "Baron Nashor", "traits": ["Padding"], "cost": 7, "slots": 2,
                "unlockable": True, "unlock_condition": "Complete the ensemble and damage task.",
                "unlock_level": "Any", "unlock_source": "https://example.test/baron",
            })
        return {"traits": traits, "champions": cheap + legends}, [c["name"] for c in legends]

    def search(self, data, **changes):
        options = dict(data=data, collab=[], size=10, locked=[], excluded=[],
                       min_collab=0, mode="balanced", limit=1)
        options.update(changes)
        return recommend_with_buildups(**options)

    def assert_path(self, data, result, excluded=(), mode="balanced"):
        champions = {c["name"]: c for c in data["champions"]}
        traits = {t["name"]: t for t in data["traits"]}
        previous = set()
        previous_fallback = set()
        for step in result["buildup"]:
            level = step["level"]
            board = step["board"]
            current = set(board["champions"]) if board else set()
            self.assertEqual(step["added"], sorted(current - previous))
            self.assertEqual(step["removed"], sorted(previous - current))
            self.assertEqual(step["retained"], sorted(current & previous))
            # Explicit preparation phases can change the previous end roster; arrival deltas remain exact.
            fallback = step["fallback"]
            fallback_names = set(fallback["champions"]) if fallback else set()
            self.assertLessEqual(len(previous_fallback - fallback_names), 2 if level >= 8 else 1)
            self.assertFalse(any(champions[n].get("unlockable") for n in fallback_names))
            phase_previous = current
            for phase in step["upgrades"]:
                names = set(phase["board"]["champions"])
                self.assertEqual(phase["added"], sorted(names - phase_previous))
                self.assertEqual(phase["removed"], sorted(phase_previous - names))
                self.assertEqual(phase["retained"], sorted(phase_previous & names))
                if phase["removed"]:
                    self.assertTrue(phase["added"])
                self.assertEqual(phase["stars"], phase["board"]["planned_stars"])
                self.assertEqual({u["name"] for u in phase["unlocks"]},
                                 {n for n in names if champions[n].get("unlockable")})
                phase_previous = names
            boards = [board, fallback, step["goal_board"]] + [p["board"] for p in step["upgrades"]]
            for item in boards:
                if item is None:
                    continue
                names = item["champions"]
                self.assertEqual(len(names), len(set(names)))
                self.assertFalse(set(names) & set(excluded))
                self.assertEqual(sum(champions[n].get("slots", 1) for n in names), level)
                groups = [champions[n]["exclusive_group"] for n in names
                          if champions[n].get("exclusive_group")]
                self.assertEqual(len(groups), len(set(groups)))
                for name in names:
                    if champions[name]["cost"] == 4:
                        self.assertGreaterEqual(level, 7)
                    if champions[name]["cost"] == 5:
                        self.assertGreaterEqual(level, 9)
                expected = _evaluate_team(names, traits, champions, result["collab_pool"],
                                          mode, result["sona_band"],
                                          item["planned_stars"], level=level)
                self.assertEqual({key: item[key] for key in expected}, expected)
            if level < len(result["buildup"]):
                self.assertIsNone(step["goal_board"])
            previous, previous_fallback = phase_previous, fallback_names
        final = result["buildup"][-1]
        expected = {key: result[key] for key in final["goal_board"]}
        self.assertEqual(final["goal_board"], expected)
        self.assertEqual(final["upgrades"][-1]["board"] if final["upgrades"] else final["board"],
                         final["goal_board"])
        self.assertIsNot(final["goal_board"], result)
        json.dumps(result)

    def test_final_upgrade_exchanges_match_roles_before_alphabetical_names(self):
        roles = {
            "A old tank": "Magic Tank", "Z old carry": "Attack Marksman",
            "A new carry": "Attack Marksman", "Z new tank": "Magic Tank",
            "Keep AP": "Magic Caster", "Keep tank": "Attack Tank",
        }
        champions = {name: {"name": name, "traits": [], "cost": 1,
                            "unlockable": False, "role": role}
                     for name, role in roles.items()}
        planner = _Planner({}, champions, [], "balanced", "", final_level=4)
        arrival = planner.evaluate(4, ["A old tank", "Z old carry", "Keep AP", "Keep tank"])
        goal = planner.evaluate(4, ["A new carry", "Z new tank", "Keep AP", "Keep tank"])
        phases = _upgrade_phases(planner, 4, arrival, goal)
        self.assertEqual([(phase["removed"], phase["added"]) for phase in phases], [
            (["A old tank"], ["Z new tank"]),
            (["Z old carry"], ["A new carry"]),
        ])
        self.assertEqual(phases[-1]["board"], goal)

    def test_many_late_legends_have_stable_arrival_and_gradual_exact_goal(self):
        data, goal = self.make_data()
        result = self.search(data, locked=goal, excluded=["Starter 10"])[0]
        self.assert_path(data, result, ["Starter 10"])
        final = result["buildup"][-1]
        self.assertNotEqual(final["board"]["champions"], result["champions"])
        self.assertGreater(len(final["upgrades"]), 1)
        self.assertTrue(all(len(p["removed"]) == 1 for p in final["upgrades"] if p["removed"]))
        for step in result["buildup"][:8]:
            self.assertTrue(all(name.startswith("Starter") for name in step["board"]["champions"]))
        snapshot = copy.deepcopy(result)
        final["goal_board"]["planned_stars"][goal[0]] = 4
        final["goal_board"]["champions"].append("changed")
        self.assertEqual(result["champions"], snapshot["champions"])
        self.assertEqual(final["upgrades"][-1], snapshot["buildup"][-1]["upgrades"][-1])
        text = format_buildup(snapshot, data["champions"])
        self.assertIn("Primary arrival", text)
        self.assertIn("Upgrade phase 1", text)
        self.assertIn("Then sell/remove", text)
        self.assertIn("Exact final-level goal", text)

    def test_baron_upgrade_path_preserves_two_slots_and_exact_stats(self):
        data, goal = self.make_data(baron=True)
        result = self.search(data, locked=goal)[0]
        self.assert_path(data, result)
        self.assertTrue(result["buildup"][-1]["upgrades"])
        self.assertIn("Baron Nashor", result["buildup"][-1]["goal_board"]["champions"])
        self.assertIn("Manual chain lock: Baron Nashor", format_buildup(result, data["champions"]))
        baron_phase = next(p for p in result["buildup"][-1]["upgrades"]
                           if "Baron Nashor" in p["added"])
        self.assertEqual(len(baron_phase["removed"]), 2)
        self.assertIn("https://example.test/baron", format_buildup(result, data["champions"]))

    def test_four_and_five_cost_timing_applies_to_every_board_and_phase(self):
        data = fixture()
        result = self.search(data, locked=["Late Guard", "Premium", "Akali (K/DA)"],
                             excluded=["A", "Xayah"])[0]
        self.assert_path(data, result, ["A", "Xayah"])
        self.assertIn("Late Guard", result["buildup"][-1]["goal_board"]["champions"])
        for step in result["buildup"]:
            for board in [step["board"], step["fallback"]] + [p["board"] for p in step["upgrades"]]:
                self.assertNotIn("Akali (True Damage)", board["champions"])

    def test_named_off_trait_helper_prepares_actual_goal_before_acquisition(self):
        data = fixture()
        result = self.search(data, size=7, locked=["Tibbers"])[0]
        phases = [phase for step in result["buildup"] for phase in [step] + step["upgrades"]]
        first = next(i for i, phase in enumerate(phases)
                     if "Tibbers" in phase["board"]["champions"])
        preparation = [phase for phase in phases[:first]
                       if "Annie" in phase["board"]["champions"]]
        self.assertTrue(preparation)
        self.assertTrue(any(phase["board"]["planned_stars"].get("Annie") == 2
                            for phase in preparation))
        self.assertTrue(any(check["name"] == "Tibbers" and "2-star Annie" in check["condition"]
                            for phase in preparation for check in phase.get("unlock_checks", [])))
        assert_explicit_stars(self, result)
        self.assertIn("replace after unlock is complete", format_buildup(result, data["champions"]))

    def test_retained_count_is_hard_beam_feasibility_not_small_bonus(self):
        data, _ = self.make_data()
        champions = {c["name"]: c for c in data["champions"]}
        traits = {t["name"]: t for t in data["traits"]}
        previous = {f"Starter {i}" for i in range(7)}
        pool = sorted(champions)
        weights = {name: 0 if name in previous else 100000 for name in pool}
        planner = _Planner(traits, champions, [], "balanced", "")
        for level, minimum in ((7, 6), (8, 5)):
            for order in (pool, list(reversed(pool))):
                board = planner._search(level, order, weights, False, previous)
                self.assertGreaterEqual(len(previous & set(board)), minimum)

    def test_hyperpop_chase_requires_actual_collab_band_but_ziggs_is_normal(self):
        data, _ = self.make_data()
        data["traits"].append({"name": "Hyperpop", "breakpoints": [2, 3], "unique": False})
        data["champions"].extend([
            {"name": "Ziggs", "traits": ["Hyperpop"], "cost": 1, "unlockable": False},
            {"name": "Kobuko", "traits": ["Hyperpop"], "cost": 1, "unlockable": True,
             "unlock_condition": "Field Ziggs holding 2 items.", "unlock_level": "Any",
             "unlock_source": "https://example.test/kobuko"},
            {"name": "Teemo", "traits": ["Hyperpop"], "cost": 5, "unlockable": True,
             "unlock_condition": "Collect 900 Hyperpop Hype.", "unlock_level": "Any",
             "unlock_source": "https://example.test/teemo"},
        ])
        plain = self.search(data, locked=["Ziggs"])[0]
        self.assertNotIn("Kobuko", plain["champions"])
        self.assertNotIn("Teemo", plain["champions"])
        for step in plain["buildup"]:
            self.assertFalse({"Kobuko", "Teemo"} & set(step["board"]["champions"]))
        supported = self.search(data, collab=["Ziggs"])[0]
        self.assertIn("Kobuko", supported["champions"])
        manual = self.search(data, locked=["Teemo"])[0]
        self.assertIn("Teemo", manual["champions"])
        self.assertNotIn("Kobuko", manual["champions"])
        self.assertIn("Manual chain lock: Teemo", format_buildup(manual, data["champions"]))
        prep = [entry for step in manual["buildup"] for entry in step["unlock_prep"]
                if entry["name"] == "Teemo"]
        self.assertTrue(any("Ziggs" in entry["helpers"] for entry in prep))
        self.assertTrue(any("Kobuko" in step["board"]["champions"] for step in manual["buildup"]))
        champions = {c["name"]: c for c in data["champions"]}
        self.assertNotIn("Kobuko", _chain_exclusions(champions, [], "Hyperpop", set()))

    def test_other_long_chains_require_relevant_support_or_explicit_lock(self):
        data = fixture()
        bands = {"Aphelios": "Heartsteel", "Gwen": "Ionic Spark",
                 "Mordekaiser": "Pentakill", "Taliyah": "Beach Pop"}
        for name, band in bands.items():
            data["champions"].append({
                "name": name, "traits": [band], "cost": 5, "unlockable": True,
                "unlock_condition": "Complete the sourced band task.", "unlock_level": "Any",
                "unlock_source": "https://example.test/task",
            })
        champions = {c["name"]: c for c in data["champions"]}
        blocked = _chain_exclusions(champions, [], "", set())
        required = set(bands) | {"Gu Feng Quest", "Akali (K/DA)", "Akali (True Damage)", "Baron Nashor"}
        self.assertTrue(required <= blocked)
        self.assertNotIn("Tibbers", blocked)
        self.assertNotIn("Baron Nashor", _chain_exclusions(champions, ["Baron Nashor"], "", set()))
        champions["Rakan"] = {
            "name": "Rakan", "traits": ["Boy Band"], "cost": 7, "unlockable": True,
        }
        champions["Smeech"] = {
            "name": "Smeech", "traits": ["Jazz"], "cost": 7, "unlockable": True,
        }
        self.assertIn("Rakan", _chain_exclusions(champions, [], "", set()))
        self.assertNotIn("Rakan", _chain_exclusions(champions, ["Rakan"], "", set()))
        self.assertNotIn("Smeech", _chain_exclusions(champions, [], "", set()))
        for name, band in bands.items():
            self.assertNotIn(name, _chain_exclusions(champions, [], band, set()))
            self.assertNotIn(name, _chain_exclusions(champions, [], "", {name}))
        results = self.search(fixture(), size=9, limit=4)
        for result in results:
            self.assertFalse(required & set(result["champions"]))
            for step in result["buildup"]:
                self.assertFalse(required & set(step["board"]["champions"]))

    def test_evelynn_prep_fields_three_distinct_kda_before_acquisition(self):
        data, _ = self.make_data()
        data["traits"].extend([
            {"name": "K/DA", "breakpoints": [3], "unique": False, "count_mode": "stars"},
            {"name": "Renegade", "breakpoints": [2], "unique": False},
        ])
        for name in ("Lillia", "Neeko", "Gragas"):
            data["champions"].append({"name": name, "traits": ["K/DA"], "cost": 1, "unlockable": False})
        data["champions"].append({
            "name": "Evelynn", "traits": ["K/DA", "Renegade"], "cost": 3, "unlockable": True,
            "unlock_condition": "Have 3 K/DA or Renegade champions.", "unlock_level": "Any",
            "unlock_source": "https://example.test/evelynn",
        })
        result = self.search(data, size=6, locked=["Evelynn"], stars={"Lillia": 3}, mode="high_value")[0]
        phases = actual_phases(result)
        first = next(i for i, phase in enumerate(phases)
                     if "Evelynn" in phase["board"]["champions"])
        prep_steps = [phase for phase in phases[:first]
                      if {"Lillia", "Neeko", "Gragas"} <= set(phase["board"]["champions"])]
        self.assertTrue(prep_steps)
        recipe = _prep_recipes({"Evelynn"}, {c["name"]: c for c in data["champions"]},
                               traits={t["name"]: t for t in data["traits"]})["Evelynn"]
        self.assertEqual(recipe["count"], 3)
        self.assertEqual(recipe["count_mode"], "distinct_champions")
        self.assertTrue(any(check["name"] == "Evelynn" for phase in prep_steps
                            for check in phase.get("unlock_checks", [])))
        text = format_buildup(result, data["champions"])
        self.assertIn("confirm the unlock", text)
        self.assertIn("stars do not replace distinct units", text)
        self.assert_path(data, result, mode="high_value")


    def test_source_worded_distinct_band_recipes_prepare_later_four_costs(self):
        for name, count, primary, secondary in (
            ("Alune", 4, "Heartsteel", "Prodigy"),
            ("Kayle", 4, "Pentakill", "Swiftstriker"),
            ("Zac", 5, "Beach Pop", "Juggernaut"),
        ):
            with self.subTest(name=name):
                data, _ = self.make_data()
                data["traits"].extend(
                    {"name": trait, "breakpoints": [count], "unique": False}
                    for trait in (primary, secondary)
                )
                helpers = {f"Helper {i}" for i in range(count)}
                data["champions"].extend(
                    {"name": helper, "traits": [primary], "cost": 1, "unlockable": False}
                    for helper in sorted(helpers)
                )
                data["champions"].append({
                    "name": name, "traits": [primary], "cost": 4, "unlockable": True,
                    "unlock_condition": f"Have {count} unique {primary} or {secondary} champions.",
                    "unlock_level": "Level 7", "unlock_source": "https://example.test/recipe",
                })
                result = self.search(data, size=8, locked=[name])[0]
                phases = actual_phases(result)
                first = next(i for i, phase in enumerate(phases)
                             if name in phase["board"]["champions"])
                self.assertTrue(any(helpers <= set(phase["board"]["champions"])
                                    for phase in phases[:first]))
                self.assertTrue(any(check["name"] == name for phase in phases[:first]
                                    for check in phase.get("unlock_checks", [])))
                self.assert_path(data, result)

    def test_forced_large_weighted_exchange_is_explicit_not_empty_filler(self):
        data, _ = self.make_data()
        data["champions"].append({
            "name": "Weighted", "traits": ["Padding"], "cost": 1, "slots": 3,
            "unlockable": False,
        })
        planner = _Planner({t["name"]: t for t in data["traits"]},
                           {c["name"]: c for c in data["champions"]}, [], "balanced", "", final_level=3)
        arrival = planner.evaluate(3, ["Starter 0", "Starter 1", "Starter 2"])
        goal = planner.evaluate(3, ["Weighted"])
        phases = _upgrade_phases(planner, 3, arrival, goal)
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[-1]["board"], goal)
        self.assertEqual(len(phases[0]["removed"]), 3)
        self.assertTrue(any("larger simultaneous exchange" in note for note in phases[0]["milestones"]))

    def test_full_board_merit_never_awards_unearned_trait_progress(self):
        traits = {
            "Near": {"name": "Near", "breakpoints": [3], "unique": False},
            "Padding": {"name": "Padding", "breakpoints": [99], "unique": False},
        }
        champions = {
            "Fixed": {"name": "Fixed", "traits": ["Near"], "cost": 1},
            "A Ordinary": {"name": "A Ordinary", "traits": ["Padding"], "cost": 1},
            "Z Near": {"name": "Z Near", "traits": ["Near"], "cost": 1},
        }
        planner = _Planner(traits, champions, [], "synergies", "")
        weights = {"Fixed": 1000, "A Ordinary": 0, "Z Near": 0}
        for order in (list(champions), list(reversed(champions))):
            self.assertEqual(planner._search(2, order, weights, False), ["A Ordinary", "Fixed"])


class UnlockPreparationAndChoiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_data = json.loads((Path(__file__).parent / "data" / "set19.json").read_text(encoding="utf-8"))
        cls.goal = ["Garen", "Rell", "Thresh", "Galio", "Viego", "Smeech",
                    "Vladimir", "Ezreal", "Lucian", "Mel"]
        cls.screenshot = recommend_with_buildups(
            cls.real_data, ["Garen", "Ezreal", "Lucian"], 10, cls.goal, [],
            0, "balanced", limit=1, stars={"Garen": 2, "Thresh": 2})[0]

    def test_screenshot_has_early_repeated_active_classical_before_thresh(self):
        result = self.screenshot
        first = next(step["level"] for step in result["buildup"]
                     if "Thresh" in step["board"]["champions"])
        setups = [(step["level"], prep) for step in result["buildup"]
                  for prep in step["preparation_boards"]
                  if prep["target"] == "Thresh" and prep["board"]]
        self.assertEqual(setups[0][0], 3)
        self.assertLess(setups[0][0], first)
        self.assertGreater(len([level for level, _ in setups if level < first]), 1)
        for _, prep in setups:
            self.assertNotIn("Thresh", prep["board"]["champions"])
            self.assertTrue(any(row["name"] == "Classical" and row["tier"] > 0
                                for row in prep["board"]["traits"]))
            self.assertIn("300", prep["condition"])
            self.assertTrue(prep["accumulating"])
            self.assertTrue(prep["source"].startswith("https://"))

    def test_screenshot_smeech_and_vladimir_have_real_prior_setups(self):
        result = self.screenshot
        for target in ("Smeech", "Vladimir"):
            phases = actual_phases(result)
            first = next(i for i, phase in enumerate(phases) if target in phase["board"]["champions"])
            setups = [phase["board"] for phase in phases[:first]
                      if (phase["board"].get("active", 0) >= 8 if target == "Smeech" else
                          phase["board"].get("positioning_constraint") == "one_front_two_rows")]
            self.assertTrue(setups, target)
        self.assertEqual(set(result["champions"]), set(self.goal))
        self.assertEqual(result["planned_stars"]["Thresh"], 2)

    def test_star_total_unlock_has_a_real_setup_not_a_nonexistent_trait(self):
        result = recommend_with_buildups(
            self.real_data, [], 7, ["Yasuo"], [], 0, "balanced", limit=1)[0]
        setup = next(prep for prep in result["buildup"][-1]["preparation_boards"]
                     if prep["target"] == "Yasuo")
        self.assertIsNotNone(setup["board"])
        self.assertNotIn("Yasuo", setup["board"]["champions"])
        self.assertTrue(any(row["name"] in ("True Damage", "Slayer") and row["count"]
                            for row in setup["board"]["traits"]))

    def test_chosen_classical_temporary_unit_is_used_in_thresh_setup(self):
        result = self.screenshot
        step = result["buildup"][2]
        champions = {unit["name"]: unit for unit in self.real_data["champions"]}
        option, replacement = next(
            (option, unit) for option in step["temporary_options"] for unit in option["options"]
            if unit != option["selected"] and "Classical" in champions[unit]["traits"])
        updated = replan_buildup(self.real_data, result, {"3": {option["slot"]: replacement}})
        prep = next(prep for prep in updated["buildup"][2]["preparation_boards"] if prep["target"] == "Thresh")
        self.assertIn(replacement, prep["board"]["champions"])
        self.assertTrue(any(row["name"] == "Classical" and row["tier"] > 0
                            for row in prep["board"]["traits"]))

    def test_vladimir_position_setup_waits_for_published_level(self):
        for step in self.screenshot["buildup"]:
            setup = next(prep for prep in step["preparation_boards"] if prep["target"] == "Vladimir")
            if step["level"] < 8:
                self.assertIsNone(setup["board"])
            else:
                self.assertIsNotNone(setup["board"])

    def choice_data(self):
        data = {"traits": [{"name": "Band", "breakpoints": [2, 4], "unique": False},
                           {"name": "Collab", "breakpoints": [3], "unique": False}],
                "champions": [
                    {"name": f"Temp{i:02}", "cost": 1, "traits": ["Band"], "unlockable": False,
                     "role": "Attack Tank" if i % 2 == 0 else "Attack Marksman"}
                    for i in range(12)] + [
                    {"name": f"Goal{i}", "cost": 4, "traits": ["Band"], "unlockable": False,
                     "role": "Attack Tank" if i % 2 == 0 else "Attack Marksman"}
                    for i in range(7)]}
        result = recommend_with_buildups(
            data, [], 7, [f"Goal{i}" for i in range(7)], ["Temp11"], 0,
            "balanced", limit=1)[0]
        return data, result

    def test_choice_changes_actual_arrival_and_downstream_but_not_goal(self):
        data, result = self.choice_data()
        original = copy.deepcopy((data, result))
        first = result["buildup"][0]
        option = next(row for row in first["temporary_options"] if len(row["options"]) > 1)
        selected = next(name for name in option["options"] if name != option["selected"]
                        and name not in result["buildup"][1]["board"]["champions"])
        choices = {"1": {option["slot"]: selected}}
        with patch("buildup.optimize", side_effect=AssertionError("The fixed goal must not be optimized again.")):
            fresh = replan_buildup(data, result, choices)
        self.assertIn(selected, fresh["buildup"][0]["board"]["champions"])
        self.assertIn(selected, fresh["buildup"][1]["retained"])
        self.assertNotEqual(fresh["buildup"][1]["board"], result["buildup"][1]["board"])
        self.assertEqual(fresh["champions"], result["champions"])
        self.assertEqual(fresh["planned_stars"], result["planned_stars"])
        self.assertEqual(fresh["buildup"][-1]["goal_board"], result["buildup"][-1]["goal_board"])
        self.assertEqual(fresh["temporary_choices"], choices)
        self.assertEqual((data, result), original)
        self.assertEqual(fresh["buildup"][0]["temporary_options"][0]["slot"], option["slot"])
        self.assertIn("Temporary choice", format_buildup(fresh, data["champions"]))
        self.assertEqual(replan_buildup(data, result), result)

    def test_invalid_overrides_reject_unavailable_excluded_unknown_and_bad_slots(self):
        data, result = self.choice_data()
        slot = result["buildup"][0]["temporary_options"][0]["slot"]
        original = copy.deepcopy(result)
        for choices in ([], {"0": {}}, {1: {}}, {"01": {}}, {"1": []},
                        {"1": {"no-slot": "Temp02"}},
                        {"1": {slot: "Unknown"}}, {"1": {slot: "Goal0"}},
                        {"1": {slot: "Temp11"}}, {"1": {slot: None}}):
            with self.subTest(choices=choices), self.assertRaises(ValueError):
                replan_buildup(data, result, choices)
        self.assertEqual(result, original)

    def test_excluded_classical_setup_is_unavailable_not_self_referential(self):
        data = copy.deepcopy(self.real_data)
        excluded = [unit["name"] for unit in data["champions"]
                    if "Classical" in unit["traits"] and unit["name"] != "Thresh"]
        with self.assertRaisesRegex(ValueError, "Unreachable unlock prerequisite for Thresh"):
            recommend_with_buildups(data, [], 7, ["Thresh"], excluded, 0,
                                   "balanced", limit=1)

    def test_actual_akali_stars_are_on_the_ordered_path_before_acquisition(self):
        goal = ["Leona", "Garen", "Ahri", "Akali (K/DA)", "Nidalee",
                "Lillia", "Sona", "Syndra", "Neeko", "Varus"]
        result = recommend_with_buildups(
            self.real_data, ["Varus", "Ahri", "Kennen", "Graves", "Gwen"], 10,
            goal, [], 0, "balanced", limit=1)[0]
        champions = {unit["name"]: unit for unit in self.real_data["champions"]}
        phases = actual_phases(result)
        first = next(i for i, phase in enumerate(phases)
                     if "Akali (K/DA)" in phase["board"]["champions"])
        def stars(board):
            return sum(board["planned_stars"][name] for name in board["champions"]
                       if name != "Akali (K/DA)" and "K/DA" in champions[name]["traits"])
        self.assertTrue(any(stars(phase["board"]) >= 12 for phase in phases[:first]))
        self.assertTrue(any(stars(phase["board"]) >= 12 and
                            any(check["name"] == "Akali (K/DA)" for check in phase.get("unlock_checks", []))
                            for phase in phases[:first]))
        assert_explicit_stars(self, result)
        self.assertEqual(set(result["champions"]), set(goal))
        self.assertEqual(replan_buildup(self.real_data, result), result)
        text = format_buildup(result, champions)
        self.assertIn("Plan star upgrade", text)
        self.assertIn("CHECK BEFORE BUYING", text)
        json.dumps(result)

    def test_other_actual_star_total_and_excluded_setup(self):
        result = recommend_with_buildups(
            self.real_data, [], 10, ["Mordekaiser"], [], 0, "balanced", limit=1)[0]
        champions = {unit["name"]: unit for unit in self.real_data["champions"]}
        phases = actual_phases(result)
        first = next(i for i, phase in enumerate(phases)
                     if "Mordekaiser" in phase["board"]["champions"])
        self.assertTrue(any(sum(phase["board"]["planned_stars"][name]
                                for name in phase["board"]["champions"]
                                if "Pentakill" in champions[name]["traits"]) >= 10
                            for phase in phases[:first]))
        assert_explicit_stars(self, result)
        excluded = [name for name, unit in champions.items()
                    if "K/DA" in unit["traits"] and name != "Akali (K/DA)"]
        with self.assertRaisesRegex(ValueError, "Unreachable unlock prerequisite"):
            recommend_with_buildups(self.real_data, [], 10, ["Akali (K/DA)"],
                                   excluded, 0, "balanced", limit=1)

    def test_globally_impossible_incidental_unlock_does_not_block_a_reachable_locked_goal(self):
        data = fixture()
        data["champions"] = [unit for unit in data["champions"]
                             if not unit["name"].startswith("KDA Helper ")]
        result = recommend_with_buildups(
            data, ["A", "B", "C", "D", "E"], 10, ["Sona"], [], 0,
            "high_value", limit=1)[0]
        self.assertIn("Sona", result["champions"])
        self.assertNotIn("Akali (K/DA)", result["champions"])
        self.assertTrue(any(check["name"] == "Sona" for phase in actual_phases(result)
                            for check in phase.get("unlock_checks", [])))
        with self.assertRaisesRegex(ValueError, r"Unreachable unlock prerequisite for Akali \(K/DA\)"):
            recommend_with_buildups(data, ["A", "B", "C", "D", "E"], 10,
                                   ["Akali (K/DA)"], [], 0, "high_value", limit=1)

    def test_preparation_floors_keep_defaults_and_only_replace_explicit_lower_ranks(self):
        data = {"traits": [{"name": "Band", "breakpoints": [3], "unique": False,
                            "count_mode": "stars"}],
                "champions": [{"name": name, "cost": 1, "traits": ["Band"]}
                              for name in ("A", "B", "C")] + [{
                                  "name": "Reward", "cost": 2, "traits": ["Band"],
                                  "unlockable": True, "unlock_level": "Any",
                                  "unlock_condition": "Have 5 star levels of Band champions.",
                                  "unlock_source": "https://example.test/stars"}]}
        for explicit in (False, True):
            result = recommend_with_buildups(
                data, [], 4, ["A", "B", "C", "Reward"], [], 0, "balanced", limit=1,
                stars={"A": 1} if explicit else {})[0]
            assert_explicit_stars(self, result)
            self.assertEqual(result["planned_stars"]["A"], 1 if explicit else 2)
            self.assertEqual(replan_buildup(data, result), result)
            self.assertEqual(any(phase.get("copy_replacements") for phase in actual_phases(result)),
                             explicit)


class EarlyAvailabilityScenarioTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.goal = ["A", "B", "C", "D", "E", "F", "Late Guard"]

    def search(self, goal=None, early=None, **changes):
        options = dict(data=self.data, collab=["A", "B", "C"], size=7,
                       locked=self.goal if goal is None else goal, excluded=[],
                       min_collab=0, mode="balanced", limit=1, early_availability=early)
        options.update(changes)
        return recommend_with_buildups(**options)[0]

    def test_empty_map_keeps_normal_gates_and_fixed_goal(self):
        baseline = self.search()
        self.assertEqual(self.search(early={}), baseline)
        self.assertEqual(replan_buildup(self.data, baseline, early_availability={}), baseline)
        self.assertEqual(baseline["early_availability"], {})
        self.assertTrue(all("Late Guard" not in step["board"]["champions"]
                            for step in baseline["buildup"][:6]))

    def test_final_four_cost_really_arrives_at_assumed_level_with_explicit_ranks(self):
        baseline = self.search()
        assumption = {"Late Guard": {"level": 3, "stars": 3}}
        before = copy.deepcopy((self.data, baseline, assumption))
        with patch("buildup.optimize", side_effect=AssertionError("Do not optimize fixed goals.")):
            result = replan_buildup(self.data, baseline, {}, assumption)
        for step in result["buildup"][:2]:
            self.assertNotIn("Late Guard", step["board"]["champions"])
        third = result["buildup"][2]
        self.assertIn("Late Guard", third["board"]["champions"])
        self.assertEqual(third["board"]["planned_stars"]["Late Guard"], 1)
        self.assertTrue(any(row == {"champion": "Late Guard", "from": 1, "to": 3}
                            for phase in third["upgrades"] for row in phase["star_upgrades"]))
        self.assertEqual(result["planned_stars"]["Late Guard"], 3)
        self.assertEqual(result["champions"], baseline["champions"])
        self.assertEqual(result["buildup"][-1]["goal_board"]["planned_stars"], result["planned_stars"])
        final = result["buildup"][-1]
        end = final["upgrades"][-1]["board"] if final["upgrades"] else final["board"]
        self.assertEqual(end["score"], result["score"])
        assert_explicit_stars(self, result)
        self.assertEqual((self.data, baseline, assumption), before)

    def test_nonfinal_early_carry_changes_actual_path_then_is_replaced(self):
        goal = ["A", "B", "C", "D", "E", "F", "G"]
        baseline = self.search(goal)
        early = {"Late Guard": {"level": 3, "stars": 2}}
        result = replan_buildup(self.data, baseline, {}, early)
        self.assertIn("Late Guard", result["buildup"][2]["board"]["champions"])
        self.assertNotIn("Late Guard", result["champions"])
        final = result["buildup"][-1]
        end = final["upgrades"][-1]["board"] if final["upgrades"] else final["board"]
        self.assertEqual(set(end["champions"]), set(goal))
        self.assertTrue(any("Late Guard" in phase["removed"] for phase in actual_phases(result)))
        assert_explicit_stars(self, result)

    def test_published_gates_are_strict_even_with_valid_sourced_quests(self):
        with self.assertRaisesRegex(ValueError, "Sona.*published unlock level 10"):
            self.search(size=10, early={"Sona": {"level": 9, "stars": 1}})
        guard = next(unit for unit in self.data["champions"] if unit["name"] == "Late Guard")
        guard.update(unlockable=True, unlock_level="Level 5",
                     unlock_condition="Spend 30 gold.", unlock_source="https://example.test/guard")
        with self.assertRaisesRegex(ValueError, "published unlock level 5"):
            self.search(early={"Late Guard": {"level": 3, "stars": 1}})
        result = self.search(early={"Late Guard": {"level": 5, "stars": 1}})
        first = next(step["level"] for step in result["buildup"]
                     if any(phase["board"] and "Late Guard" in phase["board"]["champions"]
                            for phase in [step] + step["upgrades"]))
        self.assertEqual(first, 5)
        phases = actual_phases(result)
        arrival = next(i for i, phase in enumerate(phases)
                       if phase["board"] and "Late Guard" in phase["board"]["champions"])
        self.assertTrue(any(check["name"] == "Late Guard" for phase in phases[:arrival]
                            for check in phase.get("unlock_checks", [])))

    def test_early_akali_waits_for_real_twelve_star_setup_and_check(self):
        goal = ["A", "B", "G", "KDA Helper 1", "KDA Helper 2", "KDA Helper 3", "Akali (K/DA)"]
        before = copy.deepcopy(self.data)
        result = self.search(goal, {"Akali (K/DA)": {"level": 2, "stars": 2}})
        phases = actual_phases(result)
        first = next(i for i, phase in enumerate(phases)
                     if phase["board"] and "Akali (K/DA)" in phase["board"]["champions"])
        check = next(phase for phase in phases[:first]
                     if any(row["name"] == "Akali (K/DA)" for row in phase.get("unlock_checks", [])))
        board = check["board"]
        self.assertNotIn("Akali (K/DA)", board["champions"])
        champion_map = {unit["name"]: unit for unit in self.data["champions"]}
        self.assertGreaterEqual(sum(board["planned_stars"][name] for name in board["champions"]
                                    if "K/DA" in champion_map[name]["traits"]), 12)
        self.assertTrue(all("Akali (K/DA)" not in phase["board"]["champions"]
                            for step in result["buildup"][:3] for phase in [step] + step["upgrades"]
                            if phase["board"]))
        self.assertEqual(set(result["champions"]), set(goal))
        self.assertEqual(self.data, before)
        assert_explicit_stars(self, result)

    def test_strict_map_validation_rejects_every_malformed_shape(self):
        baseline = self.search()
        before = copy.deepcopy((self.data, baseline))
        bad_maps = [
            [], "Late Guard", True, {"Unknown": {"level": 3, "stars": 1}},
            {"Late Guard": None}, {"Late Guard": {"level": 3}},
            {"Late Guard": {"level": 3, "stars": 1, "owned": True}},
            {"Late Guard": {"level": True, "stars": 1}},
            {"Late Guard": {"level": 3.0, "stars": 1}},
            {"Late Guard": {"level": 0, "stars": 1}},
            {"Late Guard": {"level": 8, "stars": 1}},
            {"Late Guard": {"level": 3, "stars": False}},
            {"Late Guard": {"level": 3, "stars": 2.5}},
            {"Late Guard": {"level": 3, "stars": 0}},
            {"Late Guard": {"level": 3, "stars": 5}},
            {"Baron Nashor": {"level": 1, "stars": 1}},
            {"Akali (K/DA)": {"level": 3, "stars": 1},
             "Akali (True Damage)": {"level": 3, "stars": 1}},
        ]
        for early in bad_maps:
            with self.subTest(early=early), self.assertRaises(ValueError):
                replan_buildup(self.data, baseline, {}, early)
        with self.assertRaisesRegex(ValueError, "Excluded champion"):
            self.search(excluded=["Xayah"], early={"Xayah": {"level": 3, "stars": 1}})
        self.assertEqual((self.data, baseline), before)

    def test_exclusive_final_goal_and_globally_impossible_setup_are_rejected(self):
        goal = ["A", "B", "G", "KDA Helper 1", "KDA Helper 2", "KDA Helper 3", "Akali (K/DA)"]
        baseline = self.search(goal, {"Akali (K/DA)": {"level": 4, "stars": 1}})
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            replan_buildup(self.data, baseline, {},
                           {"Akali (True Damage)": {"level": 3, "stars": 1}})
        with self.assertRaisesRegex(ValueError, "Unreachable unlock prerequisite"):
            self.search(goal=["A", "C", "D", "E", "F", "H", "I"],
                        excluded=["B", "G", "KDA Helper 1", "KDA Helper 2", "KDA Helper 3"],
                        early={"Akali (K/DA)": {"level": 3, "stars": 1}})
        unsupported = copy.deepcopy(self.data)
        unsupported["champions"].append(
            {"name": "Unsourced Seven", "cost": 7, "traits": ["Jazz"], "unlockable": False})
        with self.assertRaisesRegex(ValueError, "Unsupported early_availability"):
            self.search(data=unsupported, early={"Unsourced Seven": {"level": 3, "stars": 1}})

    def test_map_retention_choices_idempotence_and_clear_restore_defaults(self):
        data, baseline = UnlockPreparationAndChoiceTests.choice_data(self)
        early = {"Goal0": {"level": 3, "stars": 3}}
        scenario = replan_buildup(data, baseline, {}, early)
        first = scenario["buildup"][0]
        option = next(row for row in first["temporary_options"] if len(row["options"]) > 1)
        replacement = next(name for name in option["options"] if name != option["selected"])
        choices = {"1": {option["slot"]: replacement}}
        before = copy.deepcopy((data, baseline, scenario, choices, early))
        with patch("buildup.optimize", side_effect=AssertionError("Fixed goal replan must stay pure.")):
            updated = replan_buildup(data, scenario, choices)
            self.assertEqual(updated["early_availability"], early)
            self.assertEqual(updated["planning_context"]["early_availability"], early)
            self.assertEqual(updated["temporary_choices"], choices)
            self.assertEqual(replan_buildup(data, updated), updated)
            self.assertEqual(replan_buildup(data, updated, choices, copy.deepcopy(early)), updated)
            with self.assertRaisesRegex(ValueError, "temporary_choices"):
                replan_buildup(data, updated, early_availability={})
            cleared = replan_buildup(data, updated, {}, {})
            self.assertEqual(cleared, baseline)
        self.assertEqual((data, baseline, scenario, choices, early), before)
        self.assertEqual(cleared["planned_stars"]["Goal0"], 1)
        self.assertTrue(all("Goal0" not in step["board"]["champions"]
                            for step in cleared["buildup"][:6]))
        text = format_buildup(updated, data["champions"])
        self.assertIn("Static pre-game what-if", text)
        self.assertIn("not owned copies", text)
        self.assertIn("Goal0 from level 3, planned 3 stars", text)
        self.assertEqual(json.loads(json.dumps(updated))["early_availability"], early)

    def test_explicit_final_rank_uses_fresh_copy_replacement_even_at_final_availability(self):
        for level in (3, 7):
            with self.subTest(level=level):
                result = self.search(early={"Late Guard": {"level": level, "stars": 3}},
                                     stars={"Late Guard": 1})
                assert_explicit_stars(self, result)
                self.assertEqual(result["planned_stars"]["Late Guard"], 1)
                self.assertTrue(any(row == {"champion": "Late Guard", "from": 1, "to": 3}
                                    for phase in actual_phases(result)
                                    for row in phase.get("star_upgrades", [])))
                self.assertTrue(any(row == {"champion": "Late Guard", "from": 3, "to": 1}
                                    for phase in actual_phases(result)
                                    for row in phase.get("copy_replacements", [])))
                self.assertEqual(replan_buildup(self.data, result), result)

    def test_multiple_assumptions_share_real_slots_without_forcing_all_units(self):
        early = {"Late Guard": {"level": 1, "stars": 2},
                 "Premium": {"level": 1, "stars": 3}}
        result = self.search(early=early)
        first = result["buildup"][0]["board"]
        self.assertEqual(len(first["champions"]), 1)
        self.assertTrue(set(first["champions"]) <= set(early))
        self.assertEqual(set(result["early_availability"]), set(early))
        champion_map = {unit["name"]: unit for unit in self.data["champions"]}
        for step in result["buildup"]:
            for phase in [step] + step["upgrades"]:
                if phase["board"]:
                    self.assertEqual(sum(champion_map[name].get("slots", 1)
                                         for name in phase["board"]["champions"]), step["level"])
        assert_explicit_stars(self, result)


if __name__ == "__main__":
    unittest.main()
