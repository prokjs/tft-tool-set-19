"""Consumer-facing role swaps and conditional item-holder guidance regressions."""
import copy
import json
import unittest

from team_guidance import annotate_result, item_profile, replacement_priority


def champion(name, role=None, cost=1, slots=1):
    row = {"name": name, "cost": cost, "slots": slots, "traits": []}
    if role is not None:
        row.update(role=role, role_source="https://example.org/roles/" + name)
    return row


def board(names, stars=None, anchor=None):
    return {"champions": list(names), "planned_stars": dict(stars or {}),
            "frontline_anchor": anchor, "traits": [], "score": 123,
            "strategy_notes": ["Keep existing strategy guidance."]}


def guidance(champions, *boards):
    result = copy.deepcopy(next((row for row in reversed(boards) if row is not None), board([])))
    previous = set()
    result["buildup"] = []
    for level, row in enumerate(boards, 1):
        names = set(row["champions"]) if row else set()
        result["buildup"].append({"level": level, "board": copy.deepcopy(row),
                                  "fallback": None, "goal_board": None, "upgrades": [],
                                  "added": sorted(names - previous),
                                  "removed": sorted(previous - names)})
        previous = names
    annotate_result(result, champions)
    return result


class ItemProfileTests(unittest.TestCase):
    def test_tank_token_precedes_damage_prefix(self):
        for role in ("Magic Tank", "Attack Tank", "magic TANK"):
            with self.subTest(role=role):
                self.assertEqual(item_profile(champion("Tank", role)), "tank")

    def test_attack_magic_and_explicit_yone_flex(self):
        for role in ("Attack Marksman", "Attack Fighter", "Attack Assassin",
                     "Attack Caster", "Attack Specialist"):
            with self.subTest(role=role):
                self.assertEqual(item_profile(champion("Damage", role)), "ad")
        for role in ("Magic Marksman", "Magic Fighter", "Magic Caster", "Magic Specialist"):
            with self.subTest(role=role):
                self.assertEqual(item_profile(champion("Damage", role)), "ap")
        for role in ("Magic Fighter", "Attack Fighter", None):
            with self.subTest(yone_role=role):
                self.assertEqual(item_profile(champion("Yone", role)), "flex")

    def test_traits_and_unsupported_or_missing_roles_do_not_invent_profiles(self):
        for role in (None, "", "Attack Support", "Magic", "Tanker", "AntiTank", "Support"):
            row = champion("Unknown", role)
            row["traits"] = ["Bodyguard", "Arcanist", "Gunslinger"]
            with self.subTest(role=role):
                self.assertEqual(item_profile(row), "unknown")
        self.assertEqual(item_profile({"name": "Unknown", "role": None}), "unknown")


class ReplacementGuidanceTests(unittest.TestCase):
    def test_reversed_alphabetical_ambiguity_pairs_roles_not_names(self):
        champions = [champion("A old tank", "Magic Tank"),
                     champion("Z old carry", "Attack Marksman"),
                     champion("A new carry", "Attack Marksman"),
                     champion("Z new tank", "Attack Tank")]
        result = guidance(champions, board(["A old tank", "Z old carry"]),
                          board(["A new carry", "Z new tank"]))
        swaps = result["buildup"][1]["replacements"]
        self.assertEqual(swaps, [
            {"outgoing": ["A old tank"], "incoming": ["Z new tank"],
             "same_role": True, "profile": "tank"},
            {"outgoing": ["Z old carry"], "incoming": ["A new carry"],
             "same_role": True, "profile": "ad"}])

    def test_global_matching_reserves_flex_for_otherwise_unmatched_ap(self):
        champions = [champion("Yone", "Magic Fighter"),
                     champion("Z old AD", "Attack Fighter"),
                     champion("A new AD", "Attack Fighter"),
                     champion("Z new AP", "Magic Caster")]
        result = guidance(champions, board(["Yone", "Z old AD"]),
                          board(["A new AD", "Z new AP"]))
        swaps = result["buildup"][1]["replacements"]
        self.assertEqual({row["outgoing"][0]: row["incoming"][0] for row in swaps},
                         {"Yone": "Z new AP", "Z old AD": "A new AD"})
        self.assertTrue(all(row["same_role"] for row in swaps))

    def test_exact_combat_role_is_secondary_to_profile(self):
        champions = [champion("A old caster", "Attack Caster"),
                     champion("Z old marksman", "Attack Marksman"),
                     champion("A new marksman", "Attack Marksman"),
                     champion("Z new caster", "Attack Caster")]
        result = guidance(champions, board(["A old caster", "Z old marksman"]),
                          board(["A new marksman", "Z new caster"]))
        self.assertEqual([row["incoming"] for row in result["buildup"][1]["replacements"]],
                         [["Z new caster"], ["A new marksman"]])

    def test_phase_candidate_priority_prefers_roles_over_alphabetical_choice(self):
        champions = {row["name"]: row for row in (
            champion("A old tank", "Magic Tank"), champion("Z old AD", "Attack Marksman"),
            champion("A new AD", "Attack Marksman"), champion("Z new tank", "Attack Tank"))}
        choices = [(old, new) for old in ("A old tank", "Z old AD")
                   for new in ("A new AD", "Z new tank")]
        chosen = min(choices, key=lambda pair: (-replacement_priority([pair[0]], [pair[1]], champions), pair))
        self.assertEqual(chosen, ("Z old AD", "A new AD"))
        self.assertGreater(replacement_priority(["A old tank"], ["Z new tank"], champions),
                           replacement_priority(["A old tank"], ["A new AD"], champions))
        self.assertGreater(replacement_priority(["Z old AD"], ["A new AD"], champions),
                           replacement_priority(["Z old AD"], ["Z new tank"], champions))

    def test_role_mismatch_and_unknown_roles_are_honest_roster_fallbacks(self):
        for old_role, new_role in (("Magic Tank", "Magic Caster"), (None, None)):
            champions = [champion("Old", old_role), champion("New", new_role)]
            result = guidance(champions, board(["Old"]), board(["New"]))
            self.assertEqual(result["buildup"][1]["replacements"], [
                {"outgoing": ["Old"], "incoming": ["New"],
                 "same_role": False, "profile": "roster"}])
            self.assertEqual(result["buildup"][1]["item_transfers"], [])

    def test_level_growth_is_not_fabricated_as_a_sale(self):
        champions = [champion("Tank", "Magic Tank"), champion("Carry", "Attack Marksman")]
        result = guidance(champions, board(["Tank"]), board(["Tank", "Carry"]))
        step = result["buildup"][1]
        self.assertEqual(step["added"], ["Carry"])
        self.assertEqual(step["removed"], [])
        self.assertEqual(step["replacements"], [])
        self.assertEqual(step["item_transfers"], [])

    def test_growth_addition_is_left_out_of_actual_replacement(self):
        champions = [champion("Old", "Attack Marksman"),
                     champion("A growth", "Magic Tank"), champion("Z replacement", "Attack Marksman")]
        step = guidance(champions, board(["Old"]), board(["A growth", "Z replacement"]))["buildup"][1]
        self.assertEqual(step["added"], ["A growth", "Z replacement"])
        self.assertEqual(step["replacements"], [
            {"outgoing": ["Old"], "incoming": ["Z replacement"],
             "same_role": True, "profile": "ad"}])

    def test_baron_groups_two_outgoing_units_without_duplicate_incoming(self):
        champions = [champion("Tank A", "Attack Tank"), champion("Tank B", "Magic Tank"),
                     champion("Baron Nashor", "Magic Tank", 7, slots=2)]
        step = guidance(champions, board(["Tank A", "Tank B"]), board(["Baron Nashor"]))["buildup"][1]
        self.assertEqual(step["replacements"], [
            {"outgoing": ["Tank A", "Tank B"], "incoming": ["Baron Nashor"],
             "same_role": True, "profile": "slot_exchange"}])
        reverse = guidance(champions, board(["Baron Nashor"]), board(["Tank A", "Tank B"]))["buildup"][1]
        self.assertEqual(reverse["replacements"], [
            {"outgoing": ["Baron Nashor"], "incoming": ["Tank A", "Tank B"],
             "same_role": True, "profile": "slot_exchange"}])

    def test_baron_can_use_new_level_slot_without_invented_second_sale(self):
        champions = [champion("Old tank", "Magic Tank"),
                     champion("Baron Nashor", "Magic Tank", 7, slots=2)]
        step = guidance(champions, board(["Old tank"]), board(["Baron Nashor"]))["buildup"][1]
        self.assertEqual(step["replacements"], [
            {"outgoing": ["Old tank"], "incoming": ["Baron Nashor"],
             "same_role": True, "profile": "slot_exchange"}])


class ItemHolderGuidanceTests(unittest.TestCase):
    def test_fielded_holders_do_not_double_count_yone_or_mislabel_tanks(self):
        champions = [champion("Tank", "Magic Tank", 4), champion("AD", "Attack Marksman", 3),
                     champion("AP", "Magic Caster", 3), champion("Yone", "Magic Fighter", 4),
                     champion("Unfielded premium", "Attack Marksman", 7)]
        result = guidance(champions, board(["Tank", "AD", "AP", "Yone"]))
        expected = {"tank": "Tank", "ad": "AD", "ap": "AP", "flex": "Yone"}
        self.assertEqual(result["item_holders"], expected)
        self.assertEqual(result["buildup"][0]["board"]["item_holders"], expected)
        self.assertEqual(result["buildup"][0]["item_transfers"], [])
        self.assertEqual(result["buildup"][0]["replacements"], [])

    def test_three_star_cheap_holders_beat_one_star_premium(self):
        champions = [champion("Cheap carry", "Attack Marksman", 1),
                     champion("Premium carry", "Attack Marksman", 5),
                     champion("Cheap tank", "Magic Tank", 1), champion("Premium tank", "Magic Tank", 4)]
        result = guidance(champions, board([row["name"] for row in champions],
                                          {"Cheap carry": 3, "Cheap tank": 3}, "Premium tank"))
        self.assertEqual(result["item_holders"]["ad"], "Cheap carry")
        self.assertEqual(result["item_holders"]["tank"], "Cheap tank")

    def test_specialist_rarity_does_not_automatically_outrank_real_carry(self):
        champions = [champion("Specialist", "Attack Specialist", 5),
                     champion("Carry", "Attack Marksman", 4)]
        result = guidance(champions, board(["Specialist", "Carry"]))
        self.assertEqual(result["item_holders"]["ad"], "Carry")

    def test_continuity_breaks_strength_ties_without_hiding_stronger_new_holder(self):
        champions = [champion("Z retained", "Attack Marksman", 3),
                     champion("A newcomer", "Attack Marksman", 3),
                     champion("Premium", "Attack Marksman", 5)]
        result = guidance(champions, board(["Z retained"]), board(["Z retained", "A newcomer"]),
                          board(["Z retained", "A newcomer", "Premium"]))
        self.assertEqual(result["buildup"][1]["board"]["item_holders"]["ad"], "Z retained")
        self.assertEqual(result["item_holders"]["ad"], "Premium")

    def test_anchor_must_still_be_a_tank_and_fallback_has_own_fielded_holders(self):
        champions = [champion("False anchor", "Magic Fighter", 5),
                     champion("Tank", "Attack Tank", 1), champion("Fallback AD", "Attack Caster", 2)]
        result = board(["False anchor", "Tank"])
        result["buildup"] = [{"board": copy.deepcopy(result),
                              "fallback": board(["Fallback AD"], anchor="False anchor"), "upgrades": []}]
        annotate_result(result, champions)
        self.assertEqual(result["item_holders"]["tank"], "Tank")
        self.assertEqual(result["buildup"][0]["fallback"]["item_holders"],
                         {"tank": None, "ad": "Fallback AD", "ap": None, "flex": None})

    def test_no_suitable_holder_is_none_not_a_trait_guess(self):
        row = champion("Unknown", None, 7)
        row["traits"] = ["Bodyguard", "Arcanist"]
        result = guidance([row], board(["Unknown"], anchor="Unknown"))
        self.assertEqual(result["item_holders"], dict.fromkeys(("tank", "ad", "ap", "flex")))
        self.assertEqual(result["buildup"][0]["item_transfers"], [])

    def test_retained_holder_requires_remover_or_later_sale_not_free_transfer(self):
        champions = [champion("Old carry", "Attack Marksman", 1),
                     champion("New carry", "Attack Marksman", 4)]
        step = guidance(champions, board(["Old carry"]), board(["Old carry", "New carry"]))["buildup"][1]
        transfer, = step["item_transfers"]
        self.assertEqual((transfer["kind"], transfer["outgoing"], transfer["incoming"], transfer["method"]),
                         ("ad", "Old carry", "New carry", "remover_or_later_sale"))
        self.assertIn("remains fielded", transfer["condition"])
        self.assertIn("item remover", transfer["condition"])
        self.assertIn("later sale", transfer["condition"])
        self.assertEqual(step["replacements"], [])

    def test_sold_holder_transfer_is_type_compatible_and_buy_before_sell(self):
        champions = [champion("Old AP", "Magic Caster", 1), champion("New AP", "Magic Caster", 4)]
        transfer, = guidance(champions, board(["Old AP"]), board(["New AP"]))["buildup"][1]["item_transfers"]
        self.assertEqual(transfer["method"], "sell_outgoing")
        self.assertEqual(transfer["kind"], "ap")
        self.assertIn("Secure New AP first", transfer["condition"])
        self.assertIn("actually holds AP items", transfer["condition"])

    def test_yone_receives_ad_or_ap_without_becoming_both_holders(self):
        for kind, role in (("ad", "Attack Marksman"), ("ap", "Magic Caster")):
            with self.subTest(kind=kind):
                champions = [champion("Old", role), champion("Yone", "Magic Fighter", 4)]
                result = guidance(champions, board(["Old"]), board(["Yone"]))
                self.assertEqual(result["item_holders"],
                                 {"tank": None, "ad": None, "ap": None, "flex": "Yone"})
                transfer, = result["buildup"][1]["item_transfers"]
                self.assertEqual((transfer["kind"], transfer["incoming"]), (kind, "Yone"))
                self.assertIn("alternatives depending on the actual build", transfer["condition"])

    def test_leaving_yone_has_conditional_ad_ap_alternatives_never_cross_type(self):
        champions = [champion("Yone", "Magic Fighter", 4), champion("AD", "Attack Marksman", 4),
                     champion("AP", "Magic Caster", 4)]
        step = guidance(champions, board(["Yone"]), board(["AD", "AP"]))["buildup"][1]
        self.assertEqual([(row["kind"], row["outgoing"], row["incoming"]) for row in step["item_transfers"]],
                         [("ad", "Yone", "AD"), ("ap", "Yone", "AP")])
        self.assertTrue(all("alternatives" in row["condition"] for row in step["item_transfers"]))
        only_ap = guidance(champions, board(["Yone"]), board(["AP"]))["buildup"][1]["item_transfers"]
        self.assertEqual([row["kind"] for row in only_ap], ["ap"])

    def test_missing_primary_board_does_not_invent_holder_or_transfers(self):
        champions = [champion("AD", "Attack Marksman", 3)]
        result = guidance(champions, board(["AD"]), None, board(["AD"]))
        self.assertIsNone(result["buildup"][1]["board"])
        for step in result["buildup"][1:]:
            self.assertEqual(step["replacements"], [])
            self.assertEqual(step["item_transfers"], [])

    def test_phase_chronology_and_detached_final_boards_share_holders(self):
        champions = [champion("Start", "Attack Marksman", 1),
                     champion("Middle", "Attack Marksman", 3),
                     champion("Finish", "Attack Marksman", 5),
                     champion("Tank", "Magic Tank", 3)]
        arrival = board(["Start", "Tank"])
        final = board(["Finish", "Tank"])
        result = copy.deepcopy(final)
        result["buildup"] = [{"board": arrival, "fallback": None, "goal_board": copy.deepcopy(final),
                              "upgrades": [{"board": board(["Middle", "Tank"])}, {"board": copy.deepcopy(final)}]}]
        annotate_result(result, champions)
        step = result["buildup"][0]
        first, second = step["upgrades"]
        self.assertEqual([(row["outgoing"], row["incoming"]) for row in first["item_transfers"]],
                         [("Start", "Middle")])
        self.assertEqual([(row["outgoing"], row["incoming"]) for row in second["item_transfers"]],
                         [("Middle", "Finish")])
        self.assertEqual(second["replacements"][0]["outgoing"], ["Middle"])
        self.assertEqual(result["item_holders"], step["goal_board"]["item_holders"])
        self.assertEqual(step["goal_board"], second["board"])

    def test_retained_yone_needs_remover_for_a_new_dedicated_carry(self):
        champions = [champion("Yone", "Magic Fighter", 4),
                     champion("AD", "Attack Marksman", 4)]
        step = guidance(champions, board(["Yone"]), board(["Yone", "AD"]))["buildup"][1]
        transfer, = step["item_transfers"]
        self.assertEqual((transfer["kind"], transfer["outgoing"], transfer["incoming"]),
                         ("ad", "Yone", "AD"))
        self.assertEqual(transfer["method"], "remover_or_later_sale")
        self.assertIn("actually holds AD items", transfer["condition"])
        self.assertIn("alternatives", transfer["condition"])
        self.assertIn("item remover", transfer["condition"])

    def test_final_continuity_tie_is_consistent_across_all_goal_copies(self):
        champions = [champion("Z retained", "Attack Marksman", 4),
                     champion("A newcomer", "Attack Marksman", 4), champion("Setup", None)]
        final = board(["Z retained", "A newcomer"])
        result = copy.deepcopy(final)
        result["buildup"] = [{"board": board(["Z retained", "Setup"]), "fallback": None,
                              "goal_board": copy.deepcopy(final), "upgrades": [{"board": copy.deepcopy(final)}]}]
        annotate_result(result, champions)
        step = result["buildup"][0]
        for row in (result, step["goal_board"], step["upgrades"][0]["board"]):
            self.assertEqual(row["item_holders"]["ad"], "Z retained")

    def test_level_ten_prefers_premium_except_actually_fielded_reroll(self):
        champions = [champion("Cheap", "Attack Marksman", 2),
                     champion("Premium", "Attack Marksman", 5),
                     champion("Tank", "Magic Tank", 3)]
        for stars, field_tank, expected in (
                ({"Cheap": 2}, False, "Premium"),
                ({"Cheap": 3}, False, "Cheap"),
                ({"Cheap": 2, "Tank": 3}, True, "Cheap"),
                ({"Cheap": 2, "Tank": 3}, False, "Premium")):
            with self.subTest(stars=stars, field_tank=field_tank):
                names = ["Cheap", "Premium"] + (["Tank"] if field_tank else [])
                result = board(names, stars)
                result["level"] = 10
                annotate_result(result, champions)
                self.assertEqual(result["item_holders"]["ad"], expected)
                if field_tank:
                    self.assertEqual(result["item_holders"]["tank"], "Tank")

    def test_late_context_uses_weighted_slots_not_star_map_length(self):
        champions = [champion("Cheap", "Attack Marksman", 2),
                     champion("Premium", "Attack Marksman", 5),
                     champion("Tank", "Magic Tank", 4, slots=8)]
        result = board(["Cheap", "Premium", "Tank"], {"Cheap": 2})
        annotate_result(result, champions)
        self.assertEqual(result["item_holders"]["ad"], "Premium")
        early = board(["Cheap", "Premium"], {"Cheap": 2, **{str(i): 1 for i in range(9)}})
        annotate_result(early, champions)
        self.assertEqual(early["item_holders"]["ad"], "Cheap")

    def test_late_final_omits_cheap_other_profiles_but_keeps_forced_fallback(self):
        champions = [champion("Premium", "Attack Marksman", 5),
                     champion("Cheap AP", "Magic Caster", 2),
                     champion("Yone", "Attack Fighter", 2)]
        result = board(["Premium", "Cheap AP", "Yone"], {"Cheap AP": 2, "Yone": 2})
        result["level"] = 10
        annotate_result(result, champions)
        self.assertEqual(result["item_holders"],
                         {"tank": None, "ad": "Premium", "ap": None, "flex": None})
        forced = board(["Cheap AP", "Yone"], {"Cheap AP": 2})
        forced["level"] = 10
        annotate_result(forced, champions)
        self.assertEqual(forced["item_holders"]["ap"], "Cheap AP")
        self.assertEqual(forced["item_holders"]["flex"], "Yone")

    def test_level_nine_temporary_items_transfer_at_ten_with_consistent_final_copies(self):
        champions = [champion("Tristana", "Attack Marksman", 2),
                     champion("Lucian", "Attack Marksman", 5)]
        final = board(["Tristana", "Lucian"], {"Tristana": 2})
        arrival = board(["Tristana"], {"Tristana": 2})
        result = copy.deepcopy(final)
        result["buildup"] = [
            {"level": 9, "board": copy.deepcopy(arrival), "upgrades": []},
            {"level": 10, "board": arrival,
             "preparation_boards": [{"board": copy.deepcopy(arrival)}],
             "goal_board": copy.deepcopy(final),
             "upgrades": [{"board": copy.deepcopy(final)}]}]
        annotate_result(result, champions)
        step = result["buildup"][1]
        phase = step["upgrades"][0]
        for row in (result["buildup"][0]["board"], step["board"],
                    step["preparation_boards"][0]["board"]):
            self.assertEqual(row["item_holders"]["ad"], "Tristana")
            build, = row["planned_item_builds"]
            self.assertEqual((build["holder"], build["target"]), ("Tristana", "Lucian"))
        self.assertEqual(step["item_transfers"], [])
        transfer, = phase["item_transfers"]
        self.assertEqual((transfer["outgoing"], transfer["incoming"], transfer["method"]),
                         ("Tristana", "Lucian", "remover_or_later_sale"))
        for row in (result, step["goal_board"], phase["board"]):
            self.assertEqual(row["item_holders"]["ad"], "Lucian")
            self.assertEqual(row["item_holders"], result["item_holders"])
            self.assertEqual(row["planned_item_builds"], result["planned_item_builds"])
        self.assertEqual(step["goal_board"], phase["board"])
        before = copy.deepcopy(result)
        annotate_result(result, champions)
        self.assertEqual(result, before)

    def test_late_temporary_category_survives_until_compatible_upgrade(self):
        champions = [champion("AD", "Attack Marksman", 5),
                     champion("Cheap AD", "Attack Marksman", 2),
                     champion("Cheap AP", "Magic Caster", 2),
                     champion("Premium AP", "Magic Caster", 5)]
        arrival = board(["Cheap AD", "AD", "Cheap AP"], {"Cheap AD": 2, "Cheap AP": 2})
        final = board(["Cheap AD", "AD", "Cheap AP", "Premium AP"],
                      {"Cheap AD": 2, "Cheap AP": 2})
        result = copy.deepcopy(final)
        result["buildup"] = [{"level": 10, "board": arrival,
                              "preparation_boards": [{"board": copy.deepcopy(arrival)}],
                              "goal_board": copy.deepcopy(final),
                              "upgrades": [{"board": copy.deepcopy(arrival)},
                                           {"board": copy.deepcopy(final)}]}]
        annotate_result(result, champions)
        step = result["buildup"][0]
        for row in (step["board"], step["preparation_boards"][0]["board"],
                    step["upgrades"][0]["board"]):
            self.assertEqual(row["item_holders"]["ap"], "Cheap AP")
            self.assertEqual(row["item_holders"]["ad"], "AD")
        phase = step["upgrades"][1]
        self.assertEqual(phase["board"]["item_holders"]["ap"], "Premium AP")
        transfer, = phase["item_transfers"]
        self.assertEqual((transfer["outgoing"], transfer["incoming"], transfer["method"]),
                         ("Cheap AP", "Premium AP", "remover_or_later_sale"))

    def test_json_serializable_and_source_metadata_and_rosters_are_unchanged(self):
        champions = [champion("Tank", "Magic Tank", 4), champion("AD", "Attack Marksman", 3)]
        old = board(["Tank"], {"Tank": 2}, "Tank")
        new = board(["Tank", "AD"], {"Tank": 2, "AD": 3}, "Tank")
        champions_before, old_before, new_before = copy.deepcopy((champions, old, new))
        result = guidance(champions, old, new)
        self.assertEqual(champions, champions_before)
        self.assertEqual(old, old_before)
        self.assertEqual(new, new_before)
        for step, source in zip(result["buildup"], (old, new)):
            actual = {key: step["board"][key] for key in source}
            self.assertEqual(actual, source)
        self.assertEqual(json.loads(json.dumps(result)), result)
        serialized = copy.deepcopy(result)
        annotate_result(result, champions)
        self.assertEqual(result, serialized)

class ArchetypeRoutingTests(unittest.TestCase):
    def test_item_trios_identify_non_marksman_attack_speed_carries(self):
        from item_builds import item_archetype
        for name, role in (("Jhin", "Attack Specialist"), ("Xayah", "Attack Caster")):
            with self.subTest(name=name):
                self.assertEqual(item_archetype(champion(name, role)), "attack_speed")

    def test_explicit_roles_precede_items_and_specialist_sustain_is_fighter(self):
        from item_builds import item_archetype
        catalog = {"Unit": {"items": ["Bloodthirster", "Sterak's Gage",
                                     "Guinsoo's Rageblade"]}}
        self.assertEqual(item_archetype(champion("Unit", "Attack Assassin"), catalog), "assassin")
        self.assertEqual(item_archetype(champion("Unit", "Attack Tank"), catalog), "tank")
        catalog["Unit"]["items"].remove("Guinsoo's Rageblade")
        self.assertEqual(item_archetype(champion("Unit", "Attack Specialist"), catalog), "fighter")
        self.assertEqual(item_archetype(champion("Unit", None), catalog), "general")

    def test_archetype_priority_precedes_temporary_strength(self):
        cases = [
            ("Jhin", "Attack Specialist", "Attack Marksman", "Attack Caster"),
            ("Akali (K/DA)", "Attack Assassin", "Attack Assassin", "Attack Fighter"),
            ("Akali (K/DA)", "Attack Assassin", "Attack Fighter", "Attack Caster"),
            ("Vi", "Attack Fighter", "Attack Fighter", "Attack Marksman"),
        ]
        for target, role, preferred_role, stronger_role in cases:
            with self.subTest(target=target, preferred=preferred_role):
                roster = [champion(target, role, 5),
                          champion("Preferred", preferred_role, 1),
                          champion("Stronger", stronger_role, 4)]
                result = guidance(roster, board(["Preferred", "Stronger"]), board([target]))
                temporary = result["buildup"][0]["board"]
                self.assertEqual(temporary["item_holders"]["ad"], "Preferred")
                build, = temporary["planned_item_builds"]
                self.assertEqual(build["holder"], "Preferred")
                self.assertIn(build["holder_match"], ("same_archetype", "related_melee"))
                transfer, = result["buildup"][1]["item_transfers"]
                self.assertEqual((transfer["outgoing"], transfer["incoming"]), ("Preferred", target))

    def test_target_rank_delays_named_handoff_across_nested_boards(self):
        roster = [champion("Jhin", "Attack Specialist", 4),
                  champion("Temporary", "Attack Marksman", 2)]
        early = board(["Jhin", "Temporary"], {"Temporary": 2})
        final = board(["Jhin", "Temporary"], {"Jhin": 2, "Temporary": 2})
        result = copy.deepcopy(final)
        result["buildup"] = [{"board": copy.deepcopy(early), "fallback": copy.deepcopy(early),
                              "goal_board": copy.deepcopy(early),
                              "preparation_boards": [{"board": copy.deepcopy(early)}],
                              "upgrades": [{"board": copy.deepcopy(early)},
                                           {"board": copy.deepcopy(final)}]}]
        annotate_result(result, roster)
        step = result["buildup"][0]
        for candidate in (step["board"], step["fallback"], step["goal_board"],
                          step["preparation_boards"][0]["board"], step["upgrades"][0]["board"]):
            self.assertEqual(candidate["item_holders"]["ad"], "Temporary")
            build, = candidate["planned_item_builds"]
            self.assertEqual((build["target"], build["holder_match"]), ("Jhin", "same_archetype"))
        self.assertEqual(result["item_holders"], step["upgrades"][-1]["board"]["item_holders"])
        self.assertEqual(result["item_holders"]["ad"], "Jhin")
        before = copy.deepcopy(result)
        annotate_result(result, roster)
        self.assertEqual(result, before)

    def test_damage_fallback_never_uses_incompatible_ap(self):
        roster = [champion("Jhin", "Attack Specialist", 5),
                  champion("AD", "Attack Caster", 1),
                  champion("AP", "Magic Caster", 5)]
        result = guidance(roster, board(["AD", "AP"]), board(["Jhin"]))
        build, = result["buildup"][0]["board"]["planned_item_builds"]
        self.assertEqual((build["holder"], build["holder_match"]), ("AD", "damage_fallback"))

    def test_routed_flex_holder_does_not_accept_incompatible_ap_items(self):
        roster = [champion("Yone", "Attack Fighter", 5),
                  champion("Ahri", "Magic Caster", 5),
                  champion("Temporary", "Attack Fighter", 1)]
        result = guidance(roster, board(["Temporary"]), board(["Yone", "Ahri"]))
        early = result["buildup"][0]["board"]
        self.assertEqual(early["item_holders"]["flex"], "Temporary")
        ap = next(row for row in early["planned_item_builds"] if row["kind"] == "ap")
        self.assertIsNone(ap["holder"])
        self.assertEqual(ap["holder_match"], "unassigned")
        transfers = result["buildup"][1]["item_transfers"]
        self.assertEqual([(row["kind"], row["outgoing"], row["incoming"]) for row in transfers],
                         [("ad", "Temporary", "Yone")])

    def test_next_level_keeps_last_phase_holder_without_duplicate_handoff(self):
        roster = [champion("Lucian", "Attack Marksman", 5),
                  champion("Generic AD", "Attack Caster", 5),
                  champion("Weak Marksman", "Attack Marksman", 1)]
        result = board(["Lucian"], {"Lucian": 2})
        result["buildup"] = [
            {"level": 7, "board": board(["Generic AD"]),
             "upgrades": [{"board": board(["Weak Marksman"])}]},
            {"level": 8, "board": board(["Weak Marksman"])},
        ]
        annotate_result(result, roster)
        self.assertEqual(result["buildup"][0]["upgrades"][0]["item_transfers"][0]["incoming"],
                         "Weak Marksman")
        self.assertEqual(result["buildup"][1]["board"]["item_holders"]["ad"], "Weak Marksman")
        self.assertEqual(result["buildup"][1]["item_transfers"], [])
        self.assertEqual(result["buildup"][1]["replacements"], [])



if __name__ == "__main__":
    unittest.main()
