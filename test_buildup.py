"""Consumer-visible deterministic buildup regressions; no network or UI required."""

import copy
import json
import unittest

from buildup import SHOP_SOURCE, _Planner, _chain_exclusions, _upgrade_phases, format_buildup, recommend_with_buildups
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
                  "Have 12 K/DA star levels without Akali (True Damage) unlocked.",
                  exclusive_group="akali"),
            quest("Akali (True Damage)", ["Arcane"], 5,
                  "Earn Bling without Akali (K/DA) unlocked.", exclusive_group="akali"),
        ],
    }


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

    def test_all_twelve_get_complete_deterministic_nonmutating_builds(self):
        before = copy.deepcopy(self.data)
        arguments = {"stars": {"B": 3}, "limit": 12}
        options_before = copy.deepcopy(arguments)
        first = self.search(**arguments)
        self.assertEqual(len(first), 12)
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
                    self.assertEqual(final["goal_board"], expected_board)
                    self.assertEqual(final["upgrades"][-1]["board"] if final["upgrades"] else final["board"],
                                     expected_board)
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
            expected = sum(final["stars"][name] for name in board["champions"]
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
        for step in result["buildup"]:
            level = step["level"]
            overrides = requested if level >= 10 else {}
            union = {}
            for key in ("board", "fallback"):
                board = step[key]
                if board is None:
                    continue
                union.update(board["planned_stars"])
                expected = _evaluate_team(
                    board["champions"], {t["name"]: t for t in self.data["traits"]},
                    self.champions, result["collab_pool"], "balanced", "", overrides,
                    level=level,
                )
                self.assertEqual(board, expected)
                if level < 10:
                    self.assertTrue(all(stars == 1 for stars in board["planned_stars"].values()))
                else:
                    if "Late Guard" in board["champions"]:
                        self.assertEqual(board["planned_stars"]["Late Guard"], 2)
                    if "B" in board["champions"]:
                        self.assertEqual(board["planned_stars"]["B"], 4)
            self.assertEqual(step["stars"], union)
        tenth = result["buildup"][9]["board"]
        self.assertIn("Late Guard", tenth["champions"])
        self.assertEqual(tenth["frontline_anchor"], "Late Guard")
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
            previous = current

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
        # This future unlock task does not imply its helper is upgraded on-board.
        for step in result["buildup"][:-1]:
            if "Annie" in step["stars"]:
                self.assertEqual(step["stars"]["Annie"], 1)

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
            self.assertLessEqual(len(previous - current), 2 if level >= 8 else 1)
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
                self.assertLessEqual(len(phase["removed"]), 2)
                self.assertEqual(phase["stars"], phase["board"]["planned_stars"])
                self.assertEqual({u["name"] for u in phase["unlocks"]},
                                 {n for n in names if champions[n].get("unlockable")})
                self.assertIn("Buy", phase["milestones"][0])
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
                self.assertEqual(item, expected)
            if level < len(result["buildup"]):
                self.assertIsNone(step["goal_board"])
                self.assertEqual(step["upgrades"], [])
            previous, previous_fallback = current, fallback_names
        final = result["buildup"][-1]
        expected = {key: result[key] for key in final["goal_board"]}
        self.assertEqual(final["goal_board"], expected)
        self.assertEqual(final["upgrades"][-1]["board"] if final["upgrades"] else final["board"],
                         final["goal_board"])
        self.assertIsNot(final["goal_board"], result)
        json.dumps(result)

    def test_many_late_legends_have_stable_arrival_and_gradual_exact_goal(self):
        data, goal = self.make_data()
        result = self.search(data, locked=goal, excluded=["Starter 10"])[0]
        self.assert_path(data, result, ["Starter 10"])
        final = result["buildup"][-1]
        self.assertNotEqual(final["board"]["champions"], result["champions"])
        self.assertGreater(len(final["upgrades"]), 1)
        self.assertTrue(all(len(p["removed"]) == 1 for p in final["upgrades"]))
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
        task = next(p for p in preparation[0]["unlock_prep"] if p["name"] == "Tibbers")
        self.assertIn("Annie", task["helpers"])
        self.assertIn("2-star Annie holding 1 item", task["condition"])
        self.assertEqual(preparation[0]["board"]["planned_stars"]["Annie"], 1)
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
        first = next(i for i, step in enumerate(result["buildup"])
                     if "Evelynn" in step["board"]["champions"])
        prep_steps = [step for step in result["buildup"][:first]
                      if {"Lillia", "Neeko", "Gragas"} <= set(step["board"]["champions"])]
        self.assertTrue(prep_steps)
        recipe = next(p for p in prep_steps[0]["unlock_prep"] if p["name"] == "Evelynn")
        self.assertEqual(recipe["count"], 3)
        self.assertEqual(recipe["count_mode"], "distinct_champions")
        self.assertEqual(set(recipe["helpers"]), {"Lillia", "Neeko", "Gragas"})
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
                first = next(i for i, step in enumerate(result["buildup"])
                             if name in step["board"]["champions"])
                self.assertTrue(any(helpers <= set(step["board"]["champions"])
                                    for step in result["buildup"][:first]))
                self.assertGreaterEqual(first + 1, 7)
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


if __name__ == "__main__":
    unittest.main()
