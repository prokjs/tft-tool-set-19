"""Numeric, level, variant and complete bundled-unlock regression coverage."""
import json
from pathlib import Path
import unittest

from tools.update_unlocks import parse_blitz, parse_guide


def guide(*rows):
    blocks = ["Updated on Nov 19, 2026"]
    for name, condition, level in rows:
        blocks.append(
            f"#### [{name}](https://example.org/champion)\n\n"
            f"****Unlock Condition********:**** {condition}  \n"
            f"****Level Requirement****: {level}\n")
    return "\n".join(blocks)


class UnlockParsingTests(unittest.TestCase):
    def test_numeric_tasks_do_not_confuse_stars_items_stage_and_player_level(self):
        entries, _ = parse_guide(guide(
            ("Kobuko", "2-star Ziggs with 1 Item", "Any"),
            ("Tibbers", "Have a 2-star Annie holding 1 item.", "Any"),
            ("Seraphine", "Reroll 4 times before the Stage 2 carousel.", "Any"),
            ("Wukong", "Have allies deal 25,000 damage while fielding Xayah.", "Any"),
            ("Jinx", "Have an Ionic Spark or Hitmaker champion holding 3 items.", "Level 7"),
            ("Sona", "Have Collab champions become inspired 15 times.", "Level 10"),
        ))
        self.assertEqual(entries["Kobuko"]["unlock_condition"], "2-star Ziggs with 1 Item")
        self.assertEqual(entries["Tibbers"]["unlock_level"], "Any")
        self.assertEqual(entries["Seraphine"]["unlock_condition"],
                         "Reroll 4 times before the Stage 2 carousel.")
        self.assertIn("25,000 damage", entries["Wukong"]["unlock_condition"])
        self.assertEqual(entries["Jinx"]["unlock_level"], "Level 7")
        self.assertIn("3 items", entries["Jinx"]["unlock_condition"])
        self.assertEqual(entries["Sona"]["unlock_level"], "Level 10")
        self.assertIn("15 times", entries["Sona"]["unlock_condition"])

    def test_variants_retain_reciprocal_exclusions_and_distinct_thresholds(self):
        entries, _ = parse_guide(guide(
            ("Akali K/DA", "Have 12 unique star levels of K/DA without True Damage Akali unlocked.", "Any"),
            ("Akali True Damage", "Earn 375 True Damage Bling without K/DA Akali unlocked.", "Any"),
        ))
        self.assertEqual(set(entries), {"Akali (K/DA)", "Akali (True Damage)"})
        self.assertIn("12 unique star levels", entries["Akali (K/DA)"]["unlock_condition"])
        self.assertIn("without True Damage Akali unlocked", entries["Akali (K/DA)"]["unlock_condition"])
        self.assertIn("375 True Damage Bling", entries["Akali (True Damage)"]["unlock_condition"])
        self.assertIn("without K/DA Akali unlocked", entries["Akali (True Damage)"]["unlock_condition"])

    def test_html_retains_both_streaks_and_minimum_level(self):
        content = """<article><p>Updated on Nov 19, 2026</p><h4>Rakan</h4>
        <p><strong>Unlock Condition:</strong> Win 4 combats in a row at level 8 or above,
        and lose 3 combats in a row at level 8 or above.<br>
        <strong>Level Requirement:</strong> Level 8</p></article>"""
        entries, _ = parse_guide(content)
        self.assertEqual(entries["Rakan"]["unlock_level"], "Level 8")
        self.assertEqual(entries["Rakan"]["unlock_condition"],
                         "Win 4 combats in a row at level 8 or above, and lose 3 combats in a row at level 8 or above.")

    def test_blitz_variant_links_and_numeric_quests_survive_markup(self):
        content = """<html><head><meta property="article:modified_time"
        content="2026-11-19T21:26:21.539Z"></head><body>
        <table><tr><th>Champion</th><th>Quest</th></tr>
        <tr><td><a href="/tft/set19/champions/akali-kda"><img>Akali</a></td>
        <td>12 unique star levels of <a>K/DA</a>, without the other Akali unlocked</td></tr>
        <tr><td><a href="/tft/set19/champions/akali-true-damage">Akali</a></td>
        <td>Earn 350 True Damage Bling, without the other Akali unlocked</td></tr></table>
        </body></html>"""
        entries, _ = parse_blitz(content)
        self.assertEqual(set(entries), {"Akali (K/DA)", "Akali (True Damage)"})
        self.assertEqual(entries["Akali (K/DA)"],
                         "12 unique star levels of K/DA, without the other Akali unlocked")
        self.assertIn("350 True Damage Bling", entries["Akali (True Damage)"])

    def test_incomplete_level_does_not_silently_drop_a_champion(self):
        with self.assertRaisesRegex(ValueError, "Incomplete guide requirement"):
            parse_guide(guide(("Sona", "Have Collab champions become inspired 15 times.", "")))


class BundledUnlockCoverageTests(unittest.TestCase):
    def test_all_current_unlockables_have_complete_specific_requirements(self):
        data = json.loads((Path(__file__).parent / "data" / "set19.json").read_text(encoding="utf-8"))
        champions = {row["name"]: row for row in data["champions"]}
        unlocks = {name: row for name, row in champions.items() if row["unlockable"]}
        self.assertEqual(set(unlocks), {
            "Kobuko", "Ashe", "Lulu", "Samira", "Seraphine", "Tristana", "Evelynn", "Tibbers",
            "Alistar", "Alune", "Jinx", "Kayle", "Thresh", "Vayne", "Wukong", "Yasuo", "Zac",
            "Akali (K/DA)", "Akali (True Damage)", "Aphelios", "Gwen", "Janna", "Lee Sin",
            "Mordekaiser", "Pyke", "Taliyah", "Teemo", "Vladimir", "Baron Nashor", "Rakan",
            "Smeech", "Sona",
        })
        for name, row in unlocks.items():
            with self.subTest(champion=name):
                self.assertTrue(row["unlock_condition"].strip())
                self.assertNotIn("required number", row["unlock_condition"].lower())
                self.assertNotIn("unlock required", row["unlock_condition"].lower())
                self.assertRegex(row["unlock_level"], r"^(Any|Level [0-9]+)$")
        for name in ("Nasus", "Renata Glasc", "Galio", "Syndra"):
            with self.subTest(starting_champion=name):
                self.assertFalse(champions[name]["unlockable"])
                self.assertNotIn("unlock_condition", champions[name])
        self.assertIn("all 5 members", unlocks["Baron Nashor"]["unlock_condition"])
        self.assertIn("60,000 damage", unlocks["Baron Nashor"]["unlock_condition"])


if __name__ == "__main__":
    unittest.main()
