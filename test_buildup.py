"""Consumer-visible deterministic buildup regressions; no network or UI required."""

import copy
import json
import unittest

from buildup import SHOP_SOURCE, format_buildup, recommend_with_buildups
from optimizer import optimize


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
        self.assertEqual(first[1], other)

    def test_sona_never_appears_at_level_nine_and_early_shops_are_cheap(self):
        result = self.search(locked=["Sona"])[0]
        for step in result["buildup"][:-1]:
            self.assertNotIn("Sona", step["board"]["champions"])
        self.assertIn("Sona", result["buildup"][-1]["board"]["champions"])
        for step in result["buildup"][:2]:
            self.assertTrue(all(self.champions[name]["cost"] == 1 for name in step["board"]["champions"]))
        early = result["buildup"][2]["board"]
        self.assertGreaterEqual(next(row["count"] for row in early["traits"] if row["name"] == "Collab"), 3)
        with self.assertRaisesRegex(ValueError, "Sona.*level 9.*level 10"):
            self.search(size=9, locked=["Sona"])
        for candidate in self.search(size=9, limit=3):
            self.assertNotIn("Sona", candidate["champions"])

    def test_final_gating_and_exact_optimizer_stats(self):
        result = self.search(size=3, min_collab=0, limit=4, stars={"B": 3})
        # At level 3 only the ordinary one/two-cost champions are shop-eligible.
        excluded = [name for name, c in self.champions.items() if c["cost"] > 2]
        expected = optimize(self.data, ["A", "B", "C", "D", "E"], 3,
                            [], excluded, 0, "balanced", limit=4, stars={"B": 3})
        self.assertEqual([{key: item[key] for key in expected[0]} for item in result], expected)
        for item in result:
            self.assertEqual(item["buildup"][-1]["board"], {key: item[key] for key in expected[0]})
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
        self.assertIn("B (3★)", format_buildup(result, self.champions))

    def test_temporary_swaps_and_target_specific_paths(self):
        first = self.search(size=5, min_collab=0, locked=["A", "B", "C", "Late Guard", "Xayah"])[0]
        second = self.search(size=5, min_collab=0, locked=["E", "F", "G", "H", "Tibbers"])[0]
        self.assertNotEqual(first["buildup"][0]["board"]["champions"],
                            second["buildup"][0]["board"]["champions"])
        self.assertTrue(first["buildup"][3]["temporary"])
        self.assertIn("Xayah", first["buildup"][-1]["added"])
        self.assertTrue(first["buildup"][-1]["removed"])
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

    def test_rare_shop_hit_is_explicit_and_locks_wait_for_published_minimum(self):
        result = self.search(size=5, min_collab=0, locked=["Xayah"])[0]
        self.assertTrue(any("2%" in item for item in result["buildup"][-1]["milestones"]))
        tibbers = self.champions["Tibbers"]
        tibbers["unlock_level"] = "Level 7"
        with self.assertRaisesRegex(ValueError, "Tibbers.*Level 7"):
            self.search(size=6, min_collab=0, locked=["Tibbers"])


if __name__ == "__main__":
    unittest.main()
