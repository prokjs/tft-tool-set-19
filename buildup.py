"""Conditional, target-aware offline level planning, not a combat/economy simulator.

Shop access uses TFT Flow Patch 19.1's cost 1..5 odds (levels 1..11):
https://tftflow.com/tables/set19/shop-odds-pool-size-xp-table
A nonzero shop chance AND a published unlock minimum are necessary, not a
promise of a copy. Default planning waits for level 7 four-costs and level 9
five-costs; these are conservative timing choices, not game impossibilities.
Seven-cost units are quest-only: Sona 10, Rakan 8, Smeech 9; Baron 9 is an
explicit late-game planning heuristic, NOT a published unlock minimum.
Guaranteed earlier quest-copy rewards are not modeled: these gates are
conservative planning assumptions. Tasks, items, combat outcomes, gold, and
quest progress are never simulated or declared complete from trait counts.
Nonfinal boards before level 10 assume one star. Final and level 10+ boards use
planned upgrades, including default two-star four-cost Tanks and explicit star
overrides (also counted by K/DA), never upgrade guarantees. Small beams prefer
target members, retention, useful task helpers and synergies over perfect
trait optimization. A separate no-unlock board exists at every feasible level.
"""

from __future__ import annotations

import re
from copy import deepcopy
from bisect import bisect_right

from itertools import combinations
from champion_info import is_tank, star_text
from optimizer import (
    FRONTLINE_TRAITS, _evaluate_team, _frontline_anchor_value, _integer, _jazz_bonus,
    _jazz_links, _names, _strategy_masks, _trait_context_adjustment, _trait_value,
    _late_carry_value, _unit_strategy_value, _validate_data, optimize, planned_stars,
)
from team_guidance import annotate_result, carry_flags, item_profile, replacement_priority
from positioning import annotate_positioning
from trait_support import annotate_trait_support
from legal_notice import PURPOSE_NOTICE, LIABILITY_NOTICE


SHOP_SOURCE = "https://tftflow.com/tables/set19/shop-odds-pool-size-xp-table"
SHOP_ODDS = (
    (100, 0, 0, 0, 0), (100, 0, 0, 0, 0), (75, 25, 0, 0, 0),
    (55, 30, 15, 0, 0), (45, 33, 20, 2, 0), (30, 40, 25, 5, 0),
    (16, 30, 43, 10, 1), (15, 20, 32, 30, 3), (10, 17, 25, 33, 15),
    (5, 10, 20, 40, 25), (1, 2, 12, 50, 35),
)
QUEST_PLANNING_LEVELS = {"Sona": 10, "Rakan": 8, "Smeech": 9, "Baron Nashor": 9}
SHOP_PLANNING_LEVELS = {1: 1, 2: 3, 3: 4, 4: 7, 5: 9}
CHAIN_BANDS = {
    "Aphelios": "Heartsteel", "Akali (K/DA)": "K/DA",
    "Akali (True Damage)": "True Damage", "Gwen": "Ionic Spark",
    "Mordekaiser": "Pentakill", "Taliyah": "Beach Pop",
}
_RESULT_KEYS = (
    "champions", "traits", "active", "tiers", "frontline", "cost", "score",
    "planned_stars", "frontline_anchor", "strategy_notes",
)


def _unlock(champion: dict) -> dict:
    result = {
        "name": champion["name"],
        "condition": champion["unlock_condition"],
        "level": champion["unlock_level"],
        "source": champion["unlock_source"],
    }
    if champion.get("unlock_note"):
        result["note"] = champion["unlock_note"]
    return result


def _minimum_level(champion: dict, early_availability=None) -> int:
    published = champion["unlock_level"] if champion.get("unlockable") else "Any"
    match = re.fullmatch(r"Level (\d+)", published) if isinstance(published, str) else None
    if published != "Any" and not match:
        raise ValueError(f"{champion['name']} has an invalid unlock level: {published!r}.")
    minimum = int(match.group(1)) if match else 1
    if champion["cost"] == 7 and champion.get("unlockable"):
        ordinary = max(minimum, QUEST_PLANNING_LEVELS.get(champion["name"], 9))
    else:
        ordinary = max(minimum, SHOP_PLANNING_LEVELS.get(champion["cost"], 1))
    assumption = (early_availability or {}).get(champion["name"])
    return min(ordinary, max(minimum, assumption["level"])) if assumption else ordinary


def _eligible(champion: dict, level: int, early_availability=None) -> bool:
    assumption = (early_availability or {}).get(champion["name"])
    if assumption and level < assumption["level"]:
        return _eligible(champion, level)
    if level < _minimum_level(champion, early_availability):
        return False
    cost = champion["cost"]
    if cost == 7:
        return bool(champion.get("unlockable"))
    return 1 <= cost <= 5 and (bool(assumption) or SHOP_ODDS[level - 1][cost - 1] > 0)


def _validate_early_availability(value, champions, size, excluded=(), goal=()):
    """Validate static pre-game assumptions, never inventory or quest completion."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("early_availability must be an object keyed by canonical champion names.")
    groups = {champions[name]["exclusive_group"]: name for name in goal
              if champions[name].get("exclusive_group")}
    result = {}
    for name, record in value.items():
        if not isinstance(name, str) or name not in champions:
            raise ValueError(f"Unknown early_availability champion: {name!r}.")
        if name in excluded:
            raise ValueError(f"Excluded champion {name} cannot have an early_availability assumption.")
        if not isinstance(record, dict) or set(record) != {"level", "stars"}:
            raise ValueError(f"early_availability for {name} must contain only level and stars.")
        level, stars = record["level"], record["stars"]
        if type(level) is not int or not 1 <= level <= size:
            raise ValueError(f"early_availability level for {name} must be an integer from 1 to {size}.")
        if type(stars) is not int or not 1 <= stars <= 4:
            raise ValueError(f"early_availability stars for {name} must be an integer from 1 to 4.")
        champion = champions[name]
        if champion["cost"] not in (1, 2, 3, 4, 5, 7) or (
                champion["cost"] == 7 and not champion.get("unlockable")):
            raise ValueError(f"Unsupported early_availability champion: {name}.")
        if champion.get("slots", 1) > level:
            raise ValueError(f"early_availability for {name} needs more board slots than level {level}.")
        if champion.get("unlockable"):
            if any(not isinstance(champion.get(field), str) or not champion[field].strip()
                   for field in ("unlock_condition", "unlock_level", "unlock_source")):
                raise ValueError(f"{name} is missing valid sourced unlock data.")
            published = champion["unlock_level"]
            minimum = re.fullmatch(r"Level (\d+)", published)
            if published != "Any" and minimum is None:
                raise ValueError(f"{name} has an invalid unlock level: {published!r}.")
            if minimum and level < int(minimum.group(1)):
                raise ValueError(f"early_availability for {name} cannot precede its published unlock level {minimum.group(1)}.")
        group = champion.get("exclusive_group")
        if group and groups.get(group, name) != name:
            raise ValueError(f"early_availability for {name} conflicts with mutually exclusive {groups[group]}.")
        if group:
            groups[group] = name
        result[name] = dict(record)
    return result


def _helpers(target: set[str], champions: dict[str, dict]) -> set[str]:
    """Only positive named requirements; 'without ... Akali' is not a helper."""
    helpers = set()
    for name in sorted(target):
        champion = champions[name]
        if not champion.get("unlockable"):
            continue
        positive = re.split(r"\bwithout\b", champion["unlock_condition"],
                            maxsplit=1, flags=re.IGNORECASE)[0]
        for other in champions:
            if other != name and re.search(r"(?<!\w)" + re.escape(other) + r"(?!\w)", positive):
                helpers.add(other)
    return helpers


def _chain_exclusions(champions, collab, sona_band, locked):
    """Default chase policy, separate from shop/unlock eligibility and hard locks."""
    bands = {trait for name in collab for trait in champions[name]["traits"]}
    if sona_band:
        bands.add(sona_band)
    blocked = set()
    for name, champion in champions.items():
        if not champion.get("unlockable") or name in locked:
            continue
        native = set(champion["traits"])
        required = ("Hyperpop" if "Hyperpop" in native else
                    "Gu Feng" if "Gu Feng" in native else CHAIN_BANDS.get(name))
        if required and required not in bands:
            blocked.add(name)
        if name == "Baron Nashor" and not ({"Baron Nashor", "Malzahar"} & set(collab)):
            blocked.add(name)
        if name == "Rakan" and name not in collab:
            blocked.add(name)
    return blocked


def _dependency_helpers(target, champions):
    """Named prerequisites are purposeful setup, including prerequisites of helpers."""
    helpers = set()
    pending = set(target)
    while pending:
        found = _helpers(pending, champions) - helpers - set(target)
        helpers.update(found)
        pending = found
    return helpers


def _prep_recipes(target, champions, excluded=(), traits=None, collab=(), sona_band=""):
    """Translate all bundled task families into sourced setup requirements.

    A recipe describes an opportunity to work on a task, never earned progress.
    Unknown/future wording remains a manual task rather than a guessed threshold.
    """
    recipes = {}
    active_bands = {
        "Thresh": "Classical", "Lulu": "Hyperpop", "Tristana": "Hyperpop",
        "Vayne": "Rock", "Akali (True Damage)": "True Damage",
        "Gwen": "Ionic Spark", "Teemo": "Hyperpop", "Sona": "Collab",
    }
    for name in sorted(target):
        unit = champions[name]
        if not unit.get("unlockable"):
            continue
        condition = unit["unlock_condition"]
        recipe = {"kind": "manual", "candidates": [], "count": 0,
                  "requirements": condition, "accumulating": False}
        match = re.fullmatch(r"Have (\d+) (?:unique )?(.+?) champions\.", condition)
        if match and "star levels of" not in condition:
            recipe.update(kind="distinct", traits=match.group(2).split(" or "),
                          count=int(match.group(1)), count_mode="distinct_champions")
        elif name in active_bands:
            band = active_bands[name]
            recipe.update(kind="active", traits=[band], accumulating=True,
                          count=(traits or {}).get(band, {}).get("breakpoints", [2])[0])
        elif "star levels of" in condition:
            bands = re.search(r"(\d+) (?:unique )?star levels of (.+?)(?: champions\.| without|\.|$)", condition)
            if bands:
                recipe.update(kind="stars", traits=re.split(r" and | or ", bands.group(2)),
                              count=int(bands.group(1)), count_mode="star_levels")
        elif "holding" in condition and not _helpers({name}, champions):
            band_text = re.search(r"Have (?:a|an) (.+?) champion holding", condition)
            if band_text:
                recipe.update(kind="holder", traits=re.split(r",?\s+or\s+|,\s*", band_text.group(1)),
                              count=1)
        elif name == "Alistar":
            recipe.update(kind="role", count=1)
        elif name == "Vladimir":
            recipe.update(kind="position", count=1)
        elif name == "Smeech":
            recipe.update(kind="many_traits", count=8)
        elif name == "Taliyah":
            recipe.update(kind="costs", count=5, traits=["Beach Pop"])
        named = _helpers({name}, champions)
        if recipe["kind"] == "manual" and named:
            recipe.update(kind="named", count=1, candidates=sorted(named),
                          accumulating=any(word in condition for word in ("damage", "interest")))
        if recipe.get("traits"):
            recipe["candidates"] = [
                other for other, champion in champions.items()
                if other != name and other not in excluded
                and (bool(set(champion["traits"]) & set(recipe["traits"])) or
                     "Collab" in recipe["traits"] and other in collab or
                     other == "Sona" and sona_band in recipe["traits"])
            ]
        elif recipe["kind"] == "role":
            recipe["candidates"] = [
                other for other, champion in champions.items()
                if not champion.get("unlockable") and
                any(role in champion.get("role", "").lower() for role in ("caster", "marksman"))
            ]
        recipe["candidates"] = sorted(
            (other for other in recipe["candidates"] if other not in excluded),
            key=lambda other: (bool(champions[other].get("unlockable")),
                               _minimum_level(champions[other]), champions[other]["cost"], other))
        recipes[name] = recipe
    return recipes


def _globally_unreachable(traits, champions, collab, size, locked, excluded, sona_band, stars,
                          early_availability=None):
    """Necessary, optimistic setup bounds before selecting any final composition.

    At-most-slot bounds never force final locks onto a preparation board. Unknown
    quests stay conditional; a finite dependency closure prevents circular credit.
    """
    stars = stars if isinstance(stars, dict) else {}
    recipes = _prep_recipes(set(champions), champions, excluded, traits, collab, sona_band)
    locked_groups = {champions[name]["exclusive_group"]: name for name in locked
                     if champions[name].get("exclusive_group")}
    pool = {name for name, unit in champions.items()
            if name not in excluded and _eligible(unit, size, early_availability) and
            locked_groups.get(unit.get("exclusive_group"), name) == name}
    membership = {name: (set(unit["traits"]) - {"Collab"}) |
                  ({"Collab"} if name in collab or name == "Sona" else set()) |
                  ({sona_band} if name == "Sona" and sona_band else set())
                  for name, unit in champions.items()}
    possible = {name for name in pool if not champions[name].get("unlockable")}

    def maximum(names, weight):
        grouped = {}
        for name in names:
            key = ("exclusive", champions[name]["exclusive_group"]) if champions[name].get(
                "exclusive_group") else ("unit", name)
            grouped.setdefault(key, []).append(name)
        best = [0] * (size + 1)
        for alternatives in grouped.values():
            before = best[:]
            for name in alternatives:
                slots, value = champions[name].get("slots", 1), weight(name)
                for capacity in range(slots, size + 1):
                    best[capacity] = max(best[capacity], before[capacity - slots] + value)
        return best[size]

    while True:
        additions = set()
        for name in sorted(pool - possible):
            recipe = recipes[name]
            own_group = champions[name].get("exclusive_group")
            helpers = {other for other in possible if other != name and
                       (not own_group or champions[other].get("exclusive_group") != own_group)}
            candidates = helpers & set(recipe["candidates"])
            kind = recipe["kind"]
            if kind in ("stars", "distinct", "active", "holder", "role"):
                bands = recipe.get("traits", [])
                groups = ([{other for other in candidates if band in membership[other]}
                           for band in bands] if kind == "distinct" else [candidates])
                star_count = kind == "stars" or kind == "active" and any(
                    traits.get(band, {}).get("count_mode") == "stars" for band in bands)
                ready = any(maximum(group, lambda other: (4 if (stars or {}).get(other) == 4 or
                                    (early_availability or {}).get(other, {}).get("stars") == 4 else 3)
                                    if star_count else 1) >= recipe["count"] for group in groups)
            elif kind == "named":
                named = _helpers({name}, champions)
                groups = [champions[other].get("exclusive_group") for other in named
                          if champions[other].get("exclusive_group")]
                ready = (named <= helpers and len(groups) == len(set(groups)) and
                         sum(champions[other].get("slots", 1) for other in named) <= size)
            elif kind == "costs":
                ready = all(any(champions[other]["cost"] == cost for other in helpers)
                            for cost in range(1, 6)) and maximum(
                                candidates, lambda other: 1) >= 3
            elif kind == "many_traits":
                ready = sum(maximum(helpers, lambda other: (
                    (4 if (stars or {}).get(other) == 4 or
                     (early_availability or {}).get(other, {}).get("stars") == 4 else 3)
                    if trait.get("count_mode") == "stars" else 1)
                    if band in membership[other] else 0) >= trait["breakpoints"][0]
                            for band, trait in traits.items() if not trait["unique"]) >= recipe["count"]
            else:
                ready = True
            if ready:
                additions.add(name)
        if not additions:
            break
        possible.update(additions)
    return {name for name in pool - possible if champions[name].get("unlockable")}


def _preparation_board(planner, breadth, level, name, recipe, target, excluded, preferred=()):
    """Return an attainable setup roster, or an explicit unavailable/manual task."""
    champions = planner.champions
    item = {**_unlock(champions[name]), "target": name, "board": None,
            "requirements": recipe["requirements"], "kind": recipe["kind"],
            "accumulating": recipe["accumulating"], "status": "manual",
            "note": "Complete and confirm the sourced task before buying this target. No progress, items, stars, gold or combat outcomes are assumed."}
    item["planning_level"] = _minimum_level(champions[name], planner.early_availability)
    item["published_unlock_level"] = champions[name]["unlock_level"]
    if champions[name].get("unlock_note"):
        item["note"] += " " + champions[name]["unlock_note"]
    kind = recipe["kind"]
    if kind == "manual":
        item["note"] += " This is a manual economy, streak, reroll or ensemble task; a lineup alone cannot satisfy it."
        return item
    if kind == "position":
        minimum = re.fullmatch(r"Level (\d+)", item["published_unlock_level"])
        if minimum and level < int(minimum.group(1)):
            item.update(status="unavailable", note=item["note"] +
                        " The positioning unlock check is unavailable before its published player-level requirement.")
            return item
    allowed_unlocks = set(recipe["candidates"]) | _dependency_helpers({name}, champions)
    goal_groups = {planner.unit_groups[unit]: unit for unit in set(target) | set(planner.early_availability)
                   if planner.unit_groups[unit]}
    eligible = [
        unit for unit, champion in champions.items()
        if unit != name and unit not in excluded and planner.eligible(unit, level)
        and (not champion.get("unlockable") or unit in allowed_unlocks)
        and (not planner.unit_groups[unit] or
             goal_groups.get(planner.unit_groups[unit], unit) == unit)
    ]
    preferred = set(preferred)
    candidates = sorted((unit for unit in recipe["candidates"] if unit in eligible),
                        key=lambda unit: (unit not in preferred, recipe["candidates"].index(unit)))
    required = []
    if kind in ("distinct", "active"):
        if kind == "distinct" and len(recipe["traits"]) > 1:
            groups = [[unit for unit in candidates if band in planner.unit_traits[unit]]
                      for band in recipe["traits"]]
            candidates = next((group for group in groups if len(group) >= recipe["count"]), [])
        required = candidates[:recipe["count"]]
        if len(required) < recipe["count"]:
            item.update(status="unavailable", note=item["note"] +
                        " Not enough eligible distinct setup champions at this level under the exclusions and shop gates.")
            return item
    elif kind in ("named", "holder", "role", "stars"):
        if kind == "stars":
            used, required_groups = 0, set()
            for unit in candidates:
                group = planner.unit_groups[unit]
                slots = champions[unit].get("slots", 1)
                if used + slots <= level and (not group or group not in required_groups):
                    required.append(unit)
                    if len(required) * 2 >= recipe["count"]:
                        break
                    used += slots
                    if group:
                        required_groups.add(group)
        else:
            required = candidates[:1] if kind != "named" else list(candidates)
        if kind == "named" and set(required) != _helpers({name}, champions):
            item.update(status="unavailable", note=item["note"] + " A named prerequisite is excluded or unavailable.")
            return item
        if not required:
            item.update(status="unavailable", note=item["note"] + " No eligible sourced setup holder/helper is available.")
            return item
    elif kind == "costs":
        for cost in range(1, 6):
            options = [unit for unit in eligible if champions[unit]["cost"] == cost
                       and not champions[unit].get("unlockable")]
            if not options:
                item.update(status="unavailable", note=item["note"] +
                            " A champion of each ordinary cost is not yet eligible.")
                return item
            required.append(min(options, key=lambda unit: (
                "Beach Pop" not in planner.unit_traits[unit], unit not in target, unit)))
        beach = [unit for unit in candidates if unit not in required]
        while sum("Beach Pop" in planner.unit_traits[unit] for unit in required) < 3 and beach:
            required.append(beach.pop(0))
        item["positioning_requirement"] = "Place a champion of each cost in an actual Beach Pop hex. Instruments/hex state must be checked in game; generic placement is not proof."
    slot_count = lambda names: sum(champions[unit].get("slots", 1) for unit in names)
    if slot_count(required) > level:
        item.update(status="unavailable", note=item["note"] + " The sourced setup needs more board slots.")
        return item
    eligible = [unit for unit in eligible if not champions[unit].get("unlockable") or unit in required]
    searcher = breadth if kind == "many_traits" else planner
    weights = {unit: (100000 if unit in required else
                      200 * int(unit in preferred) + 20 * int(unit in target) - champions[unit]["cost"]) for unit in eligible}
    picks = searcher._search(level, sorted(eligible), weights, False,
                            beam_width=32 if kind == "many_traits" else 8)
    if picks is None or not set(required) <= set(picks):
        item.update(status="unavailable", note=item["note"] + " No exact-slot setup satisfies these constraints.")
        return item
    ranks = {unit: 1 for unit in picks}
    if kind == "stars":
        helpers = [unit for unit in required if unit in picks]
        for rank in (2, 3, 4):
            for unit in helpers:
                if sum(ranks[helper] for helper in helpers) >= recipe["count"]:
                    break
                if rank < 4 or planner.requested_stars.get(unit) == 4 or (
                        planner.early_availability.get(unit, {}).get("stars") == 4 and
                        level >= planner.early_availability[unit]["level"]):
                    ranks[unit] = rank
        if sum(ranks[unit] for unit in helpers) < recipe["count"]:
            item.update(status="unavailable", note=item["note"] +
                        " The eligible setup cannot reach the required star total without the locked target.")
            return item
    if kind == "named":
        for unit in required:
            if re.search(rf"(\d+)-star {re.escape(unit)}(?:\W|$)", recipe["requirements"]):
                ranks[unit] = int(re.search(
                    rf"(\d+)-star {re.escape(unit)}(?:\W|$)", recipe["requirements"]).group(1))
    for unit in picks:
        assumption = planner.early_availability.get(unit)
        if assumption and level >= assumption["level"]:
            ranks[unit] = max(ranks[unit], assumption["stars"])
    board = planner.evaluate(level, picks, ranks)
    if kind == "many_traits" and board["active"] < recipe["count"]:
        item.update(status="unavailable", note=item["note"] +
                    f" Search found only {board['active']} active non-unique traits, not the required {recipe['count']}; acquisition is not ready.")
        return item
    if kind == "active":
        band = recipe["traits"][0]
        if not any(row["name"] == band and row["tier"] > 0 for row in board["traits"]):
            item.update(status="unavailable", note=item["note"] + " Required band is not active.")
            return item
    if kind == "position":
        board["positioning_constraint"] = "one_front_two_rows"
        item["positioning_requirement"] = "Exactly ONE champion in rows 0–1 (front two rows); every other champion in rows 2–3. Hold this setup for the actual unlock check; default tank positioning is not valid for this task."
    if kind == "stars":
        item["note"] += f" Plan and earn the displayed {recipe['count']}-star setup before checking the unlock; planned copies are not owned copies."
    if recipe["accumulating"]:
        item["note"] += " Hold/revisit this active setup across combats until the displayed accumulated requirement is actually earned; changing levels does not finish it."
    item.update(board=_copy_board(board), status="conditional_setup",
                helpers=required, conditional_dependencies=[
                    _unlock(champions[unit]) for unit in picks if champions[unit].get("unlockable")])
    return item


def _temporary_selections(planner, level, board, target, previous, excluded,
                          chain_blocked, dependencies, protected, slots, choices):
    """Exchange actual temporary slots, validating every offered alternative."""
    if board is None:
        if choices:
            raise ValueError(f"No temporary choices are available at level {level}.")
        return board, []
    current = set(board["champions"])
    options = []
    assigned = set()
    for slot in slots:
        occupants = current - target - assigned
        if not occupants:
            if slot in choices:
                raise ValueError(f"Temporary slot {slot!r} is unavailable at level {level}.")
            continue
        selected = slot if slot in occupants else min(
            occupants, key=lambda unit: (-replacement_priority([slot], [unit], planner.champions), unit))
        base = current - {selected}
        original = planner.champions[slot]
        cap = 2 if level >= 8 else 1
        valid = [selected]
        for name, champion in planner.champions.items():
            if name in current or name in target or name in excluded or champion.get("unlockable"):
                continue
            if name in chain_blocked and name not in dependencies or not planner.eligible(name, level):
                continue
            if champion.get("slots", 1) != planner.champions[selected].get("slots", 1):
                continue
            if selected in protected and name not in protected[selected]:
                continue
            group = planner.unit_groups[name]
            if group and any(planner.unit_groups[other] == group
                             for other in base | target | set(planner.early_availability)):
                continue
            if len(previous - (base | {name})) > max(cap, len(previous - current)):
                continue
            profile = item_profile(original)
            same_role = profile != "unknown" and item_profile(champion) == profile
            same_traits = bool(planner.unit_traits[name] & planner.unit_traits[slot])
            if not same_role and not same_traits:
                continue
            valid.append(name)
        valid[1:] = sorted(valid[1:], key=lambda name: (
            -replacement_priority([slot], [name], planner.champions),
            -len(planner.unit_traits[name] & planner.unit_traits[slot]),
            planner.champions[name]["cost"], name))
        chosen = choices.get(slot, selected)
        if chosen not in valid:
            raise ValueError(f"Invalid temporary choice {chosen!r} for {slot!r} at level {level}.")
        current = base | {chosen}
        assigned.add(chosen)
        options.append({"slot": slot, "selected": chosen, "options": valid,
                        "planned_stars": {name: planner.stage_stars[level][name] for name in valid}})
    unknown = set(choices) - {option["slot"] for option in options}
    if unknown:
        raise ValueError(f"Unknown temporary slot {sorted(unknown)[0]!r} at level {level}.")
    return _copy_board(planner.evaluate(level, sorted(current))), options


class _Planner:
    def __init__(self, traits, champions, collab, mode, sona_band,
                 requested_stars=None, final_level=None, early_availability=None):
        self.traits = traits
        self.champions = champions
        self.collab = set(collab) | {"Sona"}
        self.mode = mode
        self.sona_band = sona_band
        self.requested_stars = dict(requested_stars or {})
        self.final_level = final_level
        self.early_availability = _validate_early_availability(
            early_availability, champions, final_level or 11)
        self.trait_names = sorted(traits)
        self.index = {name: index for index, name in enumerate(self.trait_names)}
        self.groups = {name: 1 << index for index, name in enumerate(sorted({
            c["exclusive_group"] for c in champions.values() if c.get("exclusive_group")
        }))}
        self.unit_traits = {}
        self.unit_groups = {}
        for name, champion in champions.items():
            names = set(champion["traits"]) - {"Collab"}
            if name in self.collab:
                names.add("Collab")
            if name == "Sona" and sona_band:
                names.add(sona_band)
            self.unit_traits[name] = names
            self.unit_groups[name] = self.groups.get(champion.get("exclusive_group"), 0)
        self.contributions = {
            name: tuple(self.index[trait] for trait in sorted(names))
            for name, names in self.unit_traits.items()
        }
        self.bits, self.jazz_partners, self.jazz_direct = _jazz_links(
            traits, champions, collab, sona_band,
        )
        self.jazz_index = self.index.get("Jazz")
        self.star_traits = {
            self.index[name] for name, trait in traits.items()
            if trait.get("count_mode") == "stars"
        }
        self.frontline = {
            name: int(is_tank(champions[name]) or bool(names & FRONTLINE_TRAITS))
            for name, names in self.unit_traits.items()
        }
        # Eleven slots at up to four planned stars can contribute 44 counts.
        self.values = []
        self.tiers = []
        self.active_values = []
        self.progress_values = []
        for name in self.trait_names:
            trait = traits[name]
            values = []
            tiers = []
            active_values = []
            future_values = []
            for count in range(45):
                tier = bisect_right(trait["breakpoints"], count)
                previous = trait["breakpoints"][tier - 1] if tier else 0
                following = trait["breakpoints"][tier] if tier < len(trait["breakpoints"]) else None
                progress = (count - previous) / (following - previous) if following else 0
                completion_value = 8.0
                if following is not None and mode != "synergies":
                    completion_value = 0.5 * (_trait_value(tier + 1, mode) - _trait_value(tier, mode))
                values.append(float(tier > 0) if trait["unique"] else _trait_value(tier, mode))
                future_values.append(0.0 if trait["unique"] else completion_value * progress)
                tiers.append(tier)
                active_values.append(int(tier > 0 and not trait["unique"]))
            self.values.append(values)
            self.tiers.append(tiers)
            self.active_values.append(active_values)
            self.progress_values.append(future_values)
        self.stage_stars = {}
        self.stage_increments = {}
        self.stage_context = {}
        self.stage_units = {}
        self.stage_anchors = {}
        self.stage_carry_flags = {}
        for level in range(1, 12):
            requested = self.requested_stars if level >= 10 or level == final_level else None
            stars = planned_stars(champions, level, requested, sona_band)
            for name, assumption in self.early_availability.items():
                if level >= assumption["level"]:
                    stars[name] = max(stars[name], assumption["stars"])
            self.stage_stars[level] = stars
            self.stage_carry_flags[level] = {
                name: carry_flags(champion, stars[name]) for name, champion in champions.items()
            }
            self.stage_increments[level] = {
                name: tuple((index, stars[name] if index in self.star_traits else 1)
                            for index in indices)
                for name, indices in self.contributions.items()
            }
            td_mask, hype_mask = _strategy_masks(
                traits, champions, sona_band, stars, self.bits,
            )
            context = []
            for name, ready_mask in (("Gu Feng", 0), ("True Damage", td_mask),
                                     ("Hyperpop", hype_mask)):
                if name not in self.index or traits[name]["unique"]:
                    continue
                index = self.index[name]
                tables = [
                    tuple(_trait_context_adjustment(
                        name, self.tiers[index][count], count, mode, level,
                        carry_ready=ready, hype_ready=ready,
                    ) for count in range(45))
                    for ready in (False, True)
                ]
                context.append((index, ready_mask, tables))
            self.stage_context[level] = tuple(context)
            self.stage_units[level] = {
                name: _unit_strategy_value(champion, stars[name], level, mode)
                for name, champion in champions.items()
            }
            self.stage_anchors[level] = {
                name: (_frontline_anchor_value(champion, stars[name], level)
                       if mode in ("balanced", "high_value") else 0)
                for name, champion in champions.items()
            }

    def eligible(self, name, level):
        return _eligible(self.champions[name], level, self.early_availability)

    def board(self, level, eligible, target, previous, helpers, prepare_sona):
        if not eligible:
            return None
        target_traits = set().union(*(self.unit_traits[name] for name in target)) if target else set()
        assumed = {name for name, record in self.early_availability.items()
                   if level >= record["level"]}
        # Never plan the other variant on a path toward a chosen exclusive unit.
        goal_groups = {self.unit_groups[name]: name for name in target | set(self.early_availability)
                       if self.unit_groups[name]}
        eligible = [name for name in eligible if not self.unit_groups[name] or
                    self.unit_groups[name] not in goal_groups or
                    goal_groups[self.unit_groups[name]] == name]
        weights = {}
        for name in eligible:
            champion = self.champions[name]
            cost = champion["cost"]
            odds = SHOP_ODDS[level - 1][cost - 1] if cost <= 5 else 100
            rarity_penalty = max(0, 10 - odds) * 15
            weights[name] = (
                600 * int(name in target) + 450 * int(name in previous)
                + 850 * int(name in helpers) + 12 * len(self.unit_traits[name] & target_traits)
                + 10000 * int(name in assumed)
                + (85 if prepare_sona and name in self.collab else 0)
                - 9 * cost - rarity_penalty - 25 * int(champion.get("unlockable", False))
            )
        ranked = sorted(eligible, key=lambda name: (-weights[name], name))
        core = target | previous | helpers | assumed | (self.collab if prepare_sona else set())
        pool = [name for name in ranked if name in core]
        support = [name for name in ranked if name not in core]
        pool += support[:14]
        answer = self._search(level, pool, weights, prepare_sona, previous)
        cap = 2 if level >= 8 else 1
        if len(pool) < len(ranked) and (answer is None or len(previous - set(answer)) > cap):
            # A compact pool must not lose exact-slot or retention feasibility.
            answer = self._search(level, ranked, weights, prepare_sona, previous)
        return None if answer is None else self.evaluate(level, answer)

    def evaluate(self, level, names, stars=None):
        return _evaluate_team(
            names, self.traits, self.champions, sorted(self.collab - {"Sona"}),
            self.mode, self.sona_band,
            stars if stars is not None else self.stage_stars[level],
            level=level,
        )

    def _search(self, level, pool, weights, prepare_sona, previous=frozenset(), beam_width=8):
        # Retained count is a feasibility key, not merely a scoring bonus.
        # A high-merit low-retention prefix cannot prune a smooth completion.
        # Unearned tier progress guides partial prefixes, never full-board merit.
        empty = (0.0, (), (0,) * len(self.trait_names), 0, 0, 0, False, 0, 0.0, 0, 0.0)
        buckets = [{(0, 0): [empty]}] + [{} for _ in range(level)]
        increments = self.stage_increments[level]
        context = self.stage_context[level]
        unit_values = self.stage_units[level]
        anchors = self.stage_anchors[level]
        carry_metadata = self.stage_carry_flags[level]
        for name in pool:
            champion = self.champions[name]
            slots = champion.get("slots", 1)
            group = self.unit_groups[name]
            contributions = increments[name]
            is_frontline = self.frontline[name]
            is_collab = int(name in self.collab)
            bit = self.bits[name]
            partners = self.jazz_partners[name]
            direct = name in self.jazz_direct
            for filled in range(level - slots, -1, -1):
                for (blocked, retained), states in list(buckets[filled].items()):
                    if blocked & group:
                        continue
                    key = (blocked | group, retained + int(name in previous))
                    destination = buckets[filled + slots].setdefault(key, [])
                    for merit, picks, old_counts, frontline, collab_count, mask, bridge, active, anchor, flags, future in states:
                        counts = list(old_counts)
                        gain = weights[name] + unit_values[name]
                        next_flags = flags | carry_metadata[name]
                        gain += _late_carry_value(next_flags, level, self.mode) - _late_carry_value(flags, level, self.mode)
                        next_active = active
                        next_future = future
                        for index, increment in contributions:
                            count = counts[index]
                            next_count = count + increment
                            gain += self.values[index][next_count] - self.values[index][count]
                            next_future += self.progress_values[index][next_count] - self.progress_values[index][count]
                            next_active += (self.active_values[index][next_count]
                                            - self.active_values[index][count])
                            counts[index] = next_count
                        next_mask = mask | bit
                        for index, ready_mask, tables in context:
                            gain += (tables[int(bool(next_mask & ready_mask))][counts[index]]
                                     - tables[int(bool(mask & ready_mask))][old_counts[index]])
                        next_anchor = max(anchor, anchors[name])
                        gain += next_anchor - anchor
                        next_bridge = bridge or direct or bool(mask & partners)
                        jazz_tier = next_jazz_tier = 0
                        if self.jazz_index is not None:
                            jazz_tier = self.tiers[self.jazz_index][old_counts[self.jazz_index]]
                            next_jazz_tier = self.tiers[self.jazz_index][counts[self.jazz_index]]
                        gain += (_jazz_bonus(next_active, next_jazz_tier, next_bridge, self.mode)
                                 - _jazz_bonus(active, jazz_tier, bridge, self.mode))
                        if self.mode in ("balanced", "high_value"):
                            gain += 30 * (min(frontline + is_frontline, 2) - min(frontline, 2))
                            gain += 20 * (int(len(picks) + 1 > frontline + is_frontline)
                                          - int(len(picks) > frontline))
                        if self.mode == "high_value":
                            gain += 30 * champion["cost"]
                        if prepare_sona:
                            # An objective to START the 15-inspiration task, never proof of completion.
                            gain += 100 * (min(collab_count + is_collab, 3) - min(collab_count, 3))
                            gain += 200 * int(collab_count < 3 <= collab_count + is_collab)
                        destination.append((merit + gain, picks + (name,), tuple(counts),
                                            frontline + is_frontline, collab_count + is_collab,
                                            next_mask, next_bridge, next_active, next_anchor, next_flags, next_future))
                    destination.sort(key=lambda state: (
                        -(state[0] + (state[-1] if filled + slots < level else 0.0)),
                        tuple(sorted(state[1])),
                    ))
                    del destination[beam_width:]
        minimum = max(0, len(previous) - (2 if level >= 8 else 1))
        feasible = buckets[level]
        if feasible:
            # Weighted/exclusion inputs can force a larger exchange. In that
            # case preserve as much as possible and disclose it in the guide.
            minimum = min(minimum, max(retained for _, retained in feasible))
        finalists = [state for (_, retained), states in feasible.items()
                     if retained >= minimum for state in states]
        if not finalists:
            return None
        winner = min(finalists, key=lambda state: (-state[0], tuple(sorted(state[1]))))
        return sorted(winner[1])


def _upgrade_phases(planner, level, arrival, goal):
    """Buy-before-sell, exact-slot exchanges; never invent a bench or empty filler."""
    if arrival is None:
        return []
    current = set(arrival["champions"])
    target = set(goal["champions"])
    phases = []
    slots = lambda names: sum(planner.champions[name].get("slots", 1) for name in names)
    while current != target:
        outgoing, incoming = sorted(current - target), sorted(target - current)
        exchange = None
        # Prefer one-for-one, then the two-for-one needed by Baron. Larger
        # artificial weighted fixtures remain honest, complete exchanges.
        for count in range(1, len(outgoing) + 1):
            choices = []
            for removed in combinations(outgoing, count):
                budget = slots(removed)
                for added_count in range(1, len(incoming) + 1):
                    for added in combinations(incoming, added_count):
                        if slots(added) == budget:
                            choices.append((-replacement_priority(removed, added, planner.champions),
                                            len(added), removed, added))
            if choices:
                _, _, removed, added = min(choices)
                exchange = (set(removed), set(added))
                break
        if exchange is None:
            raise ValueError("Final upgrade path has inconsistent exact-slot goals.")
        removed, added = exchange
        following = (current - removed) | added
        board = planner.evaluate(level, sorted(following))
        unlocks = [_unlock(planner.champions[name]) for name in sorted(following)
                   if planner.champions[name].get("unlockable")]
        milestones = [
            "Buy the listed additions before selling the outgoing units; hold the arrival board until the required hits and unlock tasks are complete. No owned copies, bench space or gold are assumed.",
        ]
        if len(removed) > 2 or len(added) > 2:
            milestones.append("These weighted slot sizes require a larger simultaneous exchange; no empty-slot or invented filler board is shown.")
        if any(planner.champions[name].get("slots", 1) == 2 for name in removed | added):
            milestones.append("Baron / weighted two-slot exchange: secure all incoming units before freeing both required slots.")
        phases.append({
            "board": _copy_board(board), "added": sorted(added),
            "removed": sorted(removed), "retained": sorted(current & following),
            "reason": "Conditional final-level upgrade, not an instant leveling pivot. Buy before selling; replace setup helpers only after the relevant unlock is complete.",
            "stars": dict(board["planned_stars"]), "unlocks": unlocks,
            "milestones": milestones,
        })
        current = following
    return phases


def _enforce_path(planner, breadth, result, target, excluded, blocked, choices):
    """Order real setup, earned-star goals and conditional checks before acquisition."""
    champions = planner.champions
    approved = set()
    previous = None
    all_recipes = _prep_recipes(set(champions), champions, excluded, planner.traits,
                               planner.collab - {"Sona"}, planner.sona_band)
    groups = {planner.unit_groups[name]: name for name in target | set(planner.early_availability)
              if planner.unit_groups[name]}
    for step in result["buildup"]:
        level = step["level"]
        desired = step["board"]
        old_names = set(previous["champions"]) if previous else set()
        old_ranks = previous["planned_stars"] if previous else {}
        safe = {name for name, unit in champions.items()
                if name not in excluded and (name not in blocked or name in approved) and planner.eligible(name, level)
                and (not unit.get("unlockable") or name in approved)}
        if desired and set(desired["champions"]) <= safe:
            arrival_names = desired["champions"]
        else:
            arrival = planner.board(level, sorted(safe), target, old_names, set(), False)
            arrival_names = arrival["champions"] if arrival else None
        current = (planner.evaluate(level, arrival_names,
                   {name: old_ranks.get(name, 1) for name in arrival_names})
                   if arrival_names else None)
        step["board"] = _copy_board(current)
        phases = []

        def move(board):
            nonlocal current
            if current is None:
                if board is None:
                    return
                current = planner.evaluate(level, board["champions"],
                                           {name: 1 for name in board["champions"]})
                phases.append({
                    "board": _copy_board(current), "added": list(current["champions"]),
                    "removed": [], "retained": [], "stars": dict(current["planned_stars"]),
                    "star_upgrades": [], "unlock_checks": [],
                    "unlocks": [_unlock(champions[name]) for name in current["champions"]
                                if champions[name].get("unlockable")],
                    "milestones": ["Buy the conditionally unlocked additions before fielding; no copies or gold are assumed."],
                    "reason": "First feasible exact-slot board after the conditional unlock check.",
                })
            roster_goal = planner.evaluate(level, board["champions"], {
                name: current["planned_stars"].get(name, 1) for name in board["champions"]})
            for phase in _upgrade_phases(planner, level, current, roster_goal):
                # The shared exchange search chooses rosters, not unearned star ranks.
                phase["board"] = planner.evaluate(level, phase["board"]["champions"], {
                    name: current["planned_stars"].get(name, 1)
                    for name in phase["board"]["champions"]})
                phase["stars"] = dict(phase["board"]["planned_stars"])
                phase["star_upgrades"], phase["unlock_checks"] = [], []
                phases.append(phase)
                current = phase["board"]
            ranks = board["planned_stars"]
            upgrades = [{"champion": name, "from": current["planned_stars"][name], "to": ranks[name]}
                        for name in board["champions"]
                        if ranks[name] > current["planned_stars"][name]]
            # A preparation helper may be sold later; never silently downgrade retained copies.
            final_ranks = {name: max(ranks[name], current["planned_stars"][name])
                           for name in board["champions"]}
            if upgrades:
                current = planner.evaluate(level, board["champions"], final_ranks)
                phases.append({
                    "board": _copy_board(current), "added": [], "removed": [],
                    "retained": list(current["champions"]), "stars": dict(current["planned_stars"]),
                    "star_upgrades": upgrades, "unlock_checks": [],
                    "unlocks": [_unlock(champions[name]) for name in current["champions"]
                                if champions[name].get("unlockable")],
                    "milestones": ["Buy and combine the required copies before counting these planned stars; no copies or gold are assumed."],
                    "reason": "Explicit same-level planned star upgrades, not automatic leveling gains.",
                })
            if board.get("positioning_constraint"):
                current["positioning_constraint"] = board["positioning_constraint"]

        def ensure(name, visiting):
            if name in approved or not champions[name].get("unlockable"):
                return
            if name in visiting:
                raise ValueError(f"Unreachable unlock prerequisite for {name}: circular locked dependencies.")
            published = re.fullmatch(r"Level (\d+)", champions[name]["unlock_level"])
            if published and level < int(published.group(1)):
                raise ValueError(f"Unreachable unlock prerequisite for {name} at level {level}: wait for its published unlock level.")
            recipe = all_recipes[name]
            unavailable = set(excluded) | (set(blocked) - set(recipe["candidates"])
                                           - _dependency_helpers({name}, champions))
            unavailable |= {unit for unit in champions if planner.unit_groups[unit]
                            and groups.get(planner.unit_groups[unit], unit) != unit}
            unavailable |= set(visiting)
            preferred = set(choices.get(str(level), {}).values()) | (
                set(current["champions"]) if current else set())
            while True:
                prep = _preparation_board(planner, breadth, level, name, recipe, target,
                                          unavailable, preferred)
                if prep["status"] == "unavailable":
                    raise ValueError(f"Unreachable unlock prerequisite for {name} at level {level}: {prep['note']}")
                failed = None
                for helper in (prep["board"]["champions"] if prep["board"] else []):
                    if champions[helper].get("unlockable"):
                        try:
                            ensure(helper, visiting | {name})
                        except ValueError as error:
                            if not str(error).startswith("Unreachable unlock prerequisite"):
                                raise
                            failed = helper
                            break
                if failed is not None:
                    unavailable.add(failed)
                    continue
                if prep["board"]:
                    move(prep["board"])
                break
            check = {**_unlock(champions[name]), "manual": recipe["kind"] == "manual",
                     "note": "Hold this setup until the sourced task is actually completed; confirm the unlock before buying the target. No progress is simulated."}
            phases.append({
                "board": _copy_board(current), "added": [], "removed": [],
                "retained": list(current["champions"]) if current else [],
                "stars": dict(current["planned_stars"]) if current else {},
                "star_upgrades": [], "unlock_checks": [check],
                "unlocks": [_unlock(champions[unit]) for unit in (current["champions"] if current else [])
                            if champions[unit].get("unlockable")],
                "milestones": [check["note"]], "reason": "Conditional unlock check before the buy-before-sell handoff.",
            })
            approved.add(name)

        # Capture attainable setups before later exact-slot boards need their helpers' slots.
        for setup in step["preparation_boards"]:
            if setup["target"] in approved or not setup["board"]:
                continue
            if any(rank > max(2, planner.requested_stars.get(name, 1))
                   for name, rank in setup["board"]["planned_stars"].items()):
                continue
            try:
                ensure(setup["target"], set())
            except ValueError as error:
                if not str(error).startswith("Unreachable unlock prerequisite"):
                    raise

        goals = [desired] if desired else []
        if step["goal_board"]:
            goals.append(step["goal_board"])
        for goal in goals:
            # Acquire all prerequisites before any dependent target is fielded.
            try:
                for name in goal["champions"]:
                    if champions[name].get("unlockable"):
                        ensure(name, set())
            except ValueError as error:
                if goal is step["goal_board"] or not str(error).startswith("Unreachable unlock prerequisite"):
                    raise
                continue
            if goal is step["goal_board"] and planner.early_availability:
                ranks = dict(goal["planned_stars"])
                for name in goal["champions"]:
                    assumption = planner.early_availability.get(name)
                    if assumption and level >= assumption["level"]:
                        ranks[name] = max(ranks[name], assumption["stars"])
                if ranks != goal["planned_stars"]:
                    move(planner.evaluate(level, goal["champions"], ranks))
            move(goal)
            if step["goal_board"] is goal and current:
                replacements = [{"champion": name, "from": current["planned_stars"][name],
                                 "to": goal["planned_stars"][name]}
                                for name in goal["champions"]
                                if name in planner.requested_stars and
                                current["planned_stars"][name] > goal["planned_stars"][name]]
                if replacements:
                    ranks = dict(current["planned_stars"])
                    ranks.update({row["champion"]: row["to"] for row in replacements})
                    current = planner.evaluate(level, goal["champions"], ranks)
                    phases.append({
                        "board": _copy_board(current), "added": [], "removed": [],
                        "retained": list(current["champions"]), "stars": dict(current["planned_stars"]),
                        "star_upgrades": [], "unlock_checks": [], "copy_replacements": replacements,
                        "unlocks": [_unlock(champions[name]) for name in current["champions"]
                                    if champions[name].get("unlockable")],
                        "milestones": ["Buy fresh copies at the exact final planned ranks before selling the upgraded preparation copies; transfer released items after the sale. No retained copy loses stars."],
                        "reason": "Explicit replacement of upgraded preparation copies to preserve the exact final-star target.",
                    })
                step["goal_board"] = _copy_board(current)
        if current and planner.early_availability and step["goal_board"] is None:
            # A locked desired carry can be delayed by its real setup. Still
            # explicitly earn scenario floors for the legal units actually held.
            ranks = dict(current["planned_stars"])
            for name in current["champions"]:
                assumption = planner.early_availability.get(name)
                if assumption and level >= assumption["level"]:
                    ranks[name] = max(ranks[name], assumption["stars"])
            move(planner.evaluate(level, current["champions"], ranks))
        step["upgrades"] = phases
        arrival = step["board"]
        names = set(arrival["champions"]) if arrival else set()
        step.update(added=sorted(names - old_names), removed=sorted(old_names - names),
                    retained=sorted(names & old_names), temporary=sorted(names - target),
                    star_upgrades=[], unlock_checks=[])
        step["unlocks"] = [_unlock(champions[name]) for name in sorted(names)
                           if champions[name].get("unlockable")]
        step["stars"] = dict(arrival["planned_stars"]) if arrival else {}
        previous = _copy_board(current)
    final = result["buildup"][-1]
    end = final["upgrades"][-1]["board"] if final["upgrades"] else final["board"]
    if end is None or set(end["champions"]) != target:
        raise ValueError("Unreachable unlock prerequisite: exact final roster cannot be reached.")
    defaults = planned_stars(champions, planner.final_level, planner.requested_stars,
                             planner.sona_band)
    if any(end["planned_stars"][name] > defaults[name] for name in target):
        result["buildup_notes"].append(
            "Final default ranks retain mandatory preparation and static-scenario upgrades; final scores and trait counts are reevaluated. Explicit requested ranks remain authoritative."
            if planner.early_availability else
            "Final default ranks retain mandatory preparation upgrades; final scores and trait counts are reevaluated. Explicit requested ranks remain authoritative.")
    for key in _RESULT_KEYS:
        result[key] = deepcopy(end[key])


def recommend_with_buildups(
    data: dict, collab: list[str], size: int, locked: list[str], excluded: list[str],
    min_collab: int, mode: str, sona_band: str = "", limit: int = 12,
    stars: dict[str, int] | None = None, early_availability: dict | None = None,
) -> list[dict]:
    """Return optimizer teams plus complete conditional builds, without mutating inputs.

    Shop timing and chase restrictions apply before final optimization. ``board``
    is a stable arrival, ``goal_board`` a detached final-only exact end goal;
    ``upgrades`` holds conditional exact-slot exchanges reaching that goal.
    """
    traits, champions = _validate_data(data)
    for champion in champions.values():
        if champion.get("unlockable"):
            for field in ("unlock_condition", "unlock_level", "unlock_source"):
                if not isinstance(champion.get(field), str) or not champion[field].strip():
                    raise ValueError(f"{champion['name']} is missing {field}; refresh the unlock data.")
    _integer(size, "size", 1)
    if size > 11:
        raise ValueError("size must be between 1 and 11 slots.")
    collab = _names(collab, "collab")
    locked = _names(locked, "locked")
    excluded = _names(excluded, "excluded")
    for label, names in (("collab", collab), ("locked", locked), ("excluded", excluded)):
        unknown = set(names) - champions.keys()
        if unknown:
            raise ValueError(f"Unknown {label} champion: {sorted(unknown)[0]}.")
    if set(locked) & set(excluded):
        raise ValueError("A champion cannot be both locked and excluded.")
    early = _validate_early_availability(early_availability, champions, size, excluded, locked)
    gated = {name for name, champion in champions.items() if not _eligible(champion, size, early)}
    for name in locked:
        if name in gated:
            champion = champions[name]
            minimum = _minimum_level(champion, early)
            raise ValueError(f"{name} cannot be planned at level {size}; requires planning level {minimum} or above "
                             f"({champion['cost']}-cost; published unlock minimum: {champion.get('unlock_level', 'Any')}). "
                             f"This is conservative timing, not game impossibility. "
                             f"Unlock task: {champion.get('unlock_condition', 'No special unlock requirement.')}")
    default_blocked = _chain_exclusions(champions, collab, sona_band, set())
    chain_blocked = default_blocked - set(locked) - set(early)
    assumption_groups = {champions[name]["exclusive_group"]: name for name in early
                         if champions[name].get("exclusive_group")}
    conflicts = {name for name, unit in champions.items() if unit.get("exclusive_group")
                 and assumption_groups.get(unit["exclusive_group"], name) != name}
    unreachable = _globally_unreachable(traits, champions, collab, size, set(locked) | set(early),
                                       set(excluded), sona_band, stars, early)
    if set(early) & unreachable:
        name = min(set(early) & unreachable)
        raise ValueError(f"Unreachable unlock prerequisite for early_availability champion {name}.")
    for name in locked:
        if name in unreachable:
            raise ValueError(f"Unreachable unlock prerequisite for {name}: no eligible global setup satisfies {champions[name]['unlock_condition']}")
    results = optimize(data, collab, size, locked, sorted(set(excluded) | gated | chain_blocked | unreachable | conflicts),
                       min_collab, mode, sona_band, limit, stars)
    context = {"collab": collab, "size": size, "locked": locked, "excluded": excluded,
               "mode": mode, "sona_band": sona_band, "stars": dict(stars or {}),
               "early_availability": early}
    reachable, failures = [], []
    for result in results:
        try:
            reachable.append(_build_buildup(data, result, context))
        except ValueError as error:
            if not str(error).startswith("Unreachable unlock prerequisite"):
                raise
            failures.append(error)
    if not reachable and failures and any(champions[name].get("unlockable") for name in locked):
        raise failures[0]
    return reachable


def replan_buildup(data, result, temporary_choices=None, early_availability=None):
    """Pure fixed-goal replan; choices are {str(level): {baseline_slot: name}}.

    Only canonical, currently offered temporary replacements are accepted.
    Neither the final optimizer nor mutable caller-owned objects are reused.
    Omitted/null availability retains the context map; {} clears it. A changed
    map requires empty temporary choices and regenerates their baseline slots.
    """
    if not isinstance(result, dict) or not isinstance(result.get("planning_context"), dict):
        raise ValueError("This result has no buildup planning context; request a fresh recommendation.")
    choices = deepcopy(result.get("temporary_choices", {})) if temporary_choices is None else temporary_choices
    if not isinstance(choices, dict):
        raise ValueError("temporary_choices must be an object keyed by level strings.")
    size = result["planning_context"]["size"]
    for level, selections in choices.items():
        if not isinstance(level, str) or level not in {str(n) for n in range(1, size + 1)}:
            raise ValueError(f"Invalid temporary-choice level: {level!r}.")
        if not isinstance(selections, dict) or any(
                not isinstance(slot, str) or not isinstance(name, str)
                for slot, name in selections.items()):
            raise ValueError("Each temporary choice must map canonical slot names to champion names.")
    context = deepcopy(result["planning_context"])
    _, champions = _validate_data(data)
    old_early = context.get("early_availability", {})
    early = _validate_early_availability(
        old_early if early_availability is None else early_availability,
        champions, size, context["excluded"], result["champions"])
    changed = early != old_early
    if changed and choices:
        raise ValueError("Changing early_availability requires temporary_choices:{} to rebuild baseline temporary slots.")
    context["early_availability"] = early
    fresh = deepcopy(result)
    return _build_buildup(data, fresh, context, deepcopy(choices),
                         None if changed else deepcopy(result["temporary_slots"]))


def _build_buildup(data, result, context, temporary_choices=None, baseline_slots=None):
    traits, champions = _validate_data(data)
    collab, size = context["collab"], context["size"]
    locked, excluded = context["locked"], context["excluded"]
    mode, sona_band, stars = context["mode"], context["sona_band"], context["stars"]
    early = _validate_early_availability(context.get("early_availability"), champions, size,
                                         excluded, result["champions"])
    context["early_availability"] = early
    default_blocked = _chain_exclusions(champions, collab, sona_band, set())
    chain_blocked = default_blocked - set(locked) - set(early)
    planner = _Planner(traits, champions, collab, mode, sona_band, stars, size, early)
    gated = [name for name in result["champions"] if not planner.eligible(name, size)]
    if gated:
        raise ValueError(f"{gated[0]} cannot be planned at level {size} without early_availability.")
    unreachable = _globally_unreachable(
        traits, champions, collab, size, set(result["champions"]) | set(early),
        set(excluded), sona_band, stars, early)
    if (set(result["champions"]) | set(early)) & unreachable:
        name = min((set(result["champions"]) | set(early)) & unreachable)
        raise ValueError(f"Unreachable unlock prerequisite for {name}.")
    # Rebuild fixed goals from requested/default ranks, not stale scenario floors.
    # Mandatory preparation floors are earned again by path enforcement below.
    final_ranks = planned_stars(champions, size, stars, sona_band)
    for name, assumption in early.items():
        if name not in stars:
            final_ranks[name] = max(final_ranks[name], assumption["stars"])
    goal = planner.evaluate(size, result["champions"], final_ranks)
    for key in _RESULT_KEYS:
        result[key] = deepcopy(goal[key])
    excluded_set = set(excluded)
    choices = temporary_choices or {}
    slots_by_level = deepcopy(baseline_slots) if baseline_slots is not None else {}
    result["planning_context"] = deepcopy(context)
    result["temporary_choices"] = deepcopy(choices)
    result["early_availability"] = deepcopy(early)
    def plan(level, target, previous, helpers, prepare_sona, fallback, dependencies, waiting):
        eligible = sorted(
            name for name, champion in champions.items()
            if name not in excluded_set and planner.eligible(name, level)
            and (name not in chain_blocked or name in dependencies)
            and (not fallback or not champion.get("unlockable"))
            and (not champion.get("unlockable") or name in set(target) | set(dependencies) | set(early))
            and name not in waiting
        )
        return planner.board(level, eligible, set(target), set(previous), set(helpers), prepare_sona)
    for result in [result]:
        result["collab_pool"] = [name for name in collab if name != "Sona"]
        result["sona_band"] = sona_band
        target = set(result["champions"])
        planning_targets = target | set(early)
        helpers = _dependency_helpers(planning_targets, champions)
        dependencies = helpers.copy()
        if "Teemo" in target or any(name in locked and "Hyperpop" in champions[name]["traits"] for name in target):
            dependencies.update(name for name, champion in champions.items()
                                if "Hyperpop" in champion["traits"])
        recipes = _prep_recipes(planning_targets | dependencies, champions, excluded_set, traits, collab, sona_band)
        breadth = (_Planner(traits, champions, collab, "synergies", sona_band, stars, size, early)
                   if "Smeech" in recipes else planner)
        if breadth is not planner:
            # Smeech needs breadth, not extra tiers, rarity or a pretty goal overlap.
            breadth.values = [
                [10000.0 * int(tier > 0 and not traits[name]["unique"]) for tier in tiers]
                for name, tiers in zip(breadth.trait_names, breadth.tiers)
            ]
        prepared = set()
        previous = set()
        previous_fallback = set()
        steps = []
        for level in range(1, size + 1):
            final = level == size
            pending = {name for name in planning_targets - previous if champions[name].get("unlockable")}
            useful_helpers = _dependency_helpers(pending, champions)
            for name, recipe in recipes.items():
                if recipe["kind"] in ("distinct", "active") and any(
                        sum(band in planner.unit_traits[unit] for unit in previous & set(recipe["candidates"])) >= recipe["count"]
                        for band in recipe["traits"]):
                    prepared.add(name)
            waiting = {name for name, recipe in recipes.items()
                       if recipe["kind"] in ("distinct", "active") and name not in prepared | previous}
            recipe_goals = (pending & recipes.keys()) - prepared
            focus = min(recipe_goals, key=lambda name: (_minimum_level(champions[name], early), name)) if recipe_goals else None
            if focus and recipes[focus]["kind"] in ("distinct", "active", "named", "holder", "stars", "role"):
                recipe = recipes[focus]
                candidates = [other for other in recipe["candidates"]
                              if other not in excluded_set and planner.eligible(other, level)]
                useful_helpers.update(candidates[:recipe["count"]])
            if "Teemo" in pending:
                useful_helpers.update(name for name in dependencies - target
                                      if "Hyperpop" in champions[name]["traits"])
            useful_helpers.update(_dependency_helpers(useful_helpers - previous, champions))
            for name in (planning_targets | dependencies) - previous:
                if champions[name].get("unlockable"):
                    named = _helpers({name}, champions)
                    if named and not named & previous:
                        waiting.add(name)
            prepare_sona = "Sona" in pending
            board = plan(level, tuple(sorted(target)), tuple(sorted(previous)),
                         tuple(sorted(useful_helpers)), prepare_sona, False,
                         tuple(sorted(dependencies)), tuple(sorted(waiting)))
            fallback = plan(level, tuple(sorted(target)), tuple(sorted(previous_fallback)),
                            (), prepare_sona, True, (), ())
            level_key = str(level)
            slots = slots_by_level.setdefault(level_key, sorted(set(board["champions"]) - target) if board else [])
            named_helpers = _dependency_helpers(pending, champions)
            setup_helpers = set(useful_helpers)
            if board:
                for name, recipe in recipes.items():
                    held = set(board["champions"]) & set(recipe["candidates"])
                    if name in pending and recipe["kind"] in ("distinct", "active") and any(
                            sum(band in planner.unit_traits[unit] for unit in held) >= recipe["count"]
                            for band in recipe["traits"]):
                        setup_helpers.update(held)
            protected = {name: ({name} if name in named_helpers else None)
                         for name in setup_helpers}
            for recipe in recipes.values():
                if recipe["kind"] in ("distinct", "active", "stars", "holder", "role"):
                    for helper in (setup_helpers & set(recipe["candidates"])) - named_helpers:
                        allowed = set(recipe["candidates"])
                        protected[helper] = (allowed if protected[helper] is None
                                             else protected[helper] & allowed)
            protected = {name: allowed if allowed is not None else {name}
                         for name, allowed in protected.items()}
            board, temporary_options = _temporary_selections(
                planner, level, board, target, previous, excluded_set, chain_blocked,
                dependencies, protected, slots, choices.get(level_key, {}))
            preparation_boards = [
                _preparation_board(planner, breadth, level, name, recipe, target, excluded_set,
                                   choices.get(level_key, {}).values())
                for name, recipe in recipes.items()
            ]
            fallback = _copy_board(fallback)
            current = set(board["champions"]) if board else set()
            goals = sorted(name for name in planning_targets | current if champions[name].get("unlockable"))
            goal = _copy_board({key: result[key] for key in _RESULT_KEYS}) if final else None
            upgrades = []
            prep_goals = planning_targets | (useful_helpers & dependencies)
            prep = [{**_unlock(champions[name]),
                     "helpers": sorted(_dependency_helpers({name}, champions) & current)}
                    for name in sorted(prep_goals - current) if champions[name].get("unlockable")]
            for item in prep:
                recipe = recipes.get(item["name"])
                if recipe and recipe.get("count_mode") == "distinct_champions":
                    item.update({key: recipe[key] for key in ("traits", "count", "count_mode")})
                    item["helpers"] = sorted(current & set(recipe["candidates"]))
                if item["name"] == "Teemo":
                    item["helpers"] = sorted(name for name in current & dependencies
                                             if "Hyperpop" in champions[name]["traits"])
            conditional = sorted(name for name in current if champions[name].get("unlockable"))
            unlocks = [_unlock(champions[name]) for name in conditional]
            milestones = []
            if not board:
                milestones.append("No eligible exact-slot primary board at this level; exclusions, shop access, weighted slots or exclusivity prevent it.")
            if fallback is None:
                milestones.append("No exact-slot no-unlock alternative exists at this level under these exclusions and slot constraints.")
            if unlocks:
                milestones.append("Conditional plan only: complete each listed task before fielding its champion; quest progress is not assumed or simulated.")
            for item in prep:
                milestones.append(f"Unlock prep for {item['name']}: {item['condition']} "
                                  "Required stars, items and progress must be earned, not inferred from this board. "
                                  "Keep useful setup helpers; replace after unlock is complete.")
                if item.get("count_mode") == "distinct_champions":
                    milestones.append(f"{item['name']}: field {item['count']} distinct champions from "
                                      f"{' or '.join(item['traits'])}; planned stars do not replace distinct units. "
                                      "Play the required combat / confirm the unlock, then buy the champion; the roster alone does not complete the task.")
            for name in goals:
                minimum = _minimum_level(champions[name], early)
                if level < minimum:
                    milestones.append(f"{name}: wait until planning level {minimum}; published unlock minimum: {champions[name].get('unlock_level', 'Any')}.")
            for helper in sorted(useful_helpers & current):
                for name in sorted(pending):
                    if helper in _dependency_helpers({name}, champions):
                        milestones.append(f"Field {helper} to work toward {name}: {champions[name]['unlock_condition']} Replace after unlock is complete; required upgrades/items/progress still need to be earned.")
            collab_count = sum(name in planner.collab for name in current)
            if "Sona" in target:
                milestones.append(
                    "Work toward Sona's 15 inspirations with 3 Collab active; this board does not prove any inspirations earned."
                    if collab_count >= 3 else
                    "Build toward 3 Collab to begin Sona's 15-inspiration task; no progress is assumed."
                )
            for name in sorted(current | (target if final else set())):
                champion = champions[name]
                if champion["cost"] == 7:
                    milestones.append(f"{name} is quest-only, not a regular shop roll; unlock completion is conditional.")
                if name == "Baron Nashor":
                    milestones.append("Baron consumes 2 board slots. Level 9 is a conservative late-game planning heuristic, not a published unlock minimum (Any).")
            if level == 11:
                milestones.append("Level 11 requires a special level-cap extension; ordinary leveling is not assumed to reach it.")
            temporary = sorted(current - target)
            reason = "Stable arrival: retain most previous units, add obtainable goals and make small replacements; purposeful off-trait helpers prepare future unlocks."
            if upgrades:
                reason += " Hold this arrival until hits/tasks are ready, then follow the separate buy-before-sell upgrades; the exact end goal is not an instant pivot."
            cap = 2 if level >= 8 else 1
            for label, old, new in (
                ("Primary", previous, current),
                ("No-unlock alternative", previous_fallback,
                 set(fallback["champions"]) if fallback else set()),
            ):
                if len(old - new) > cap:
                    milestones.append(f"{label}: weighted slots/exclusions force {len(old - new)} removals beyond the usual {cap}; no smooth exact-slot completion exists. No empty filler is invented.")
            if not board:
                reason = "No feasible exact-slot board from eligible champions at this level; do not invent a unit or claim the next-level board is available early."
            elif temporary:
                reason += " Replace useful temporary setup only after the relevant unlock is complete; shop access is not a purchase guarantee."
            step_stars = dict(fallback["planned_stars"]) if fallback else {}
            if board:
                step_stars.update(board["planned_stars"])
            steps.append({
                "level": level, "board": board, "fallback": fallback,
                "added": sorted(current - previous), "removed": sorted(previous - current),
                "goal_board": goal, "upgrades": upgrades, "unlock_prep": prep,
                "temporary_options": temporary_options, "preparation_boards": preparation_boards,
                "retained": sorted(current & previous), "temporary": temporary,
                "unlocks": unlocks, "milestones": milestones, "stars": step_stars, "reason": reason,
            })
            previous = current
            previous_fallback = set(fallback["champions"]) if fallback else set()
        result["buildup"] = steps
        result["buildup_notes"] = [
            "Conservative planning, not combat, item, gold, rolling or quest-progress simulation. Every unlockable primary unit is conditional on its task.",
            "Primary arrivals preserve previously displayed stars; same-level upgrade phases explicitly plan each increase before any unlock counts it. Planned copies must still be earned.",
            "Planned upgrades are targets, not owned units. Fast-10 rankings apply conditional trait, Senna-preference and reroll-path heuristics, not gold or win-rate estimates.",
            "End-goal locks are not forced at earlier levels. Temporary units are replaced as target units become eligible; exclusions apply throughout.",
            "A nonzero ordinary-shop chance and published unlock minimum gate eligibility. Guaranteed earlier unlock-copy rewards are not modeled.",
            "Four-cost planning starts at level 7; five-cost planning starts at level 9. These realistic defaults are not claims earlier hits are impossible; published unlock minima still apply.",
            "No-unlock alternatives exclude every unlockable unit and may not satisfy the final Collab minimum or end-goal locks. None means no exact-slot alternative exists.",
            f"Shop odds source: TFT Flow Patch 19.1 — {SHOP_SOURCE}",
        ]
        if early:
            result["buildup_notes"].append(
                "Static pre-game what-if scenario only: early availability and planned stars are assumptions, not owned copies or completed unlock tasks. Published requirements and real setup/check phases still apply.")
            result["buildup_notes"].extend(
                f"Early availability: {name} from level {record['level']}, planned {record['stars']} stars."
                for name, record in sorted(early.items()))
        result["buildup_notes"].extend([
            "Primary arrivals normally remove at most one unit per level below 8 and two at 8+. Final upgrades are separate exact-slot conditional exchanges: buy before selling and hold arrival until ready.",
            "Unrelated long unlock chains require an actual Collab native band or featured band. Purposeful dependencies of selected goals are allowed in the guide only; no quest progress is assumed.",
            "Suggested item holders use sourced combat roles and planned-star/rarity heuristics, not a combat or item-inventory simulation. Yone can use AD or AP items as one chosen flex build.",
            "Replacements prefer compatible item roles. Move items after selling an outgoing holder; if it remains fielded, an item remover or a later actual sale is required.",
            PURPOSE_NOTICE,
            LIABILITY_NOTICE,
        ])
        for name in sorted(set(locked) & default_blocked):
            result["buildup_notes"].append(f"Manual chain lock: {name} overrides the default unrelated-chain restriction. Complete its task and dependencies; no acquired copies, stars, items or progress are assumed.")
        if size == 11:
            result["buildup_notes"].append("Level 11 requires a special level-cap extension, not ordinary leveling.")
        result["temporary_slots"] = slots_by_level
        _enforce_path(planner, breadth, result, target, excluded_set, chain_blocked, choices)
        annotate_result(result, champions)
        annotate_trait_support(result, champions, traits)
        annotate_positioning(result, champions)
    return result


def _copy_board(board):
    if board is None:
        return None
    return {**board, "champions": list(board["champions"]),
            "traits": [dict(row) for row in board["traits"]],
            "planned_stars": dict(board["planned_stars"]),
            "strategy_notes": list(board["strategy_notes"])}


def format_buildup(result: dict, champions) -> str:
    """Copy/export arrivals, complete upgrades, tasks and the exact end goal."""
    champion_map = champions if isinstance(champions, dict) else {c["name"]: c for c in champions}
    lines = []

    def roster(label, board):
        if board is None:
            lines.append(label + ": no feasible exact-slot board.")
            return
        lines.append(label + ": " + ", ".join(
            f"{name} ({star_text(board['planned_stars'].get(name, 1))}" +
            (", conditional unlock" if champion_map.get(name, {}).get("unlockable") else "") + ")"
            for name in board["champions"]
        ))
        lines.append(_format_traits(board))
        holders = board["item_holders"]
        lines.append("Suggested item holders (if those items are available): " + "; ".join(
            f"{label}: {holders[kind] or ('no dedicated holder' if kind in ('ad', 'ap') else 'no suitable holder')}"
            for kind, label in (("tank", "Tank"), ("ad", "AD carry"), ("ap", "AP carry"),
                                ("flex", "AD/AP flex — choose one build"))
        ))
        for build in board.get("planned_item_builds", []):
            holder = build["holder"] or "unassigned — no suitable holder"
            lines.append(f"Planned {build['kind'].upper()} items for {build['target']} on {holder}: "
                         + ", ".join(build["items"]) + f" [{build['label']}]")
            lines.append("  " + build["note"])
            lines.append("  Item plan source: " + build["source"])
        lines.append("Personal trait-tier activation (not damage efficiency): " + "; ".join(
            name + ": " + (f"{support['percent']:g}%" if support["percent"] is not None else "n/a")
            for name, support in board["champion_trait_support"].items()
        ))
        if board["frontline_anchor"]:
            lines.append("Frontline anchor: " + board["frontline_anchor"])
        lines.extend("Strategy: " + note for note in board["strategy_notes"])

    def task(unlock, label):
        lines.append(f"{label} {unlock['name']} [{unlock['level']}]: {unlock['condition']}")
        if unlock.get("helpers"):
            lines.append("  Fielded setup helpers: " + ", ".join(unlock["helpers"]))
        if unlock.get("count_mode") == "distinct_champions":
            lines.append(f"  Requires {unlock['count']} distinct champions from " +
                         " or ".join(unlock["traits"]) + "; stars do not replace distinct units.")
        if unlock["source"]:
            lines.append("  Source: " + unlock["source"])
        if unlock.get("note"):
            lines.append("  " + unlock["note"])

    def changes(phase):
        for replacement in phase["replacements"]:
            label = ("role match: " + replacement["profile"] if replacement["same_role"] else
                     "weighted slot exchange" if replacement["profile"] == "slot_exchange" else
                     "roster replacement — different item roles")
            lines.append("Replace " + ", ".join(replacement["outgoing"]) + " → " +
                         ", ".join(replacement["incoming"]) + " (" + label + ").")
        for transfer in phase["item_transfers"]:
            method = ("buy the incoming unit, then sell the outgoing holder to release its items"
                      if transfer["method"] == "sell_outgoing" else
                      "the old holder stays fielded: use an item remover or wait until you actually sell it")
            lines.append(f"{transfer['kind'].upper()} items: {transfer['outgoing']} → "
                         f"{transfer['incoming']}; {method}. {transfer['condition']}")
        for upgrade in phase.get("star_upgrades", []):
            lines.append(f"Plan star upgrade: {upgrade['champion']} {upgrade['from']} → {upgrade['to']} stars; earn and combine the copies first.")
        for replacement in phase.get("copy_replacements", []):
            lines.append(f"Buy fresh {replacement['champion']} at {replacement['to']} stars before selling the {replacement['from']}-star preparation copy.")
        for check in phase.get("unlock_checks", []):
            task(check, "CHECK BEFORE BUYING —")

    roster("Exact end goal (not the leveling arrival)", result)
    lines.extend("Note: " + note for note in result.get("buildup_notes", []))
    for step in result.get("buildup", []):
        lines.append(f"\nLevel {step['level']}")
        for preparation in step.get("preparation_boards", []):
            task(preparation, "PREPARE BEFORE ARRIVAL —")
            lines.append("  Setup status: " + preparation["status"])
            roster("  Actual unlock setup for " + preparation["target"], preparation["board"])
            lines.append("  Requirements: " + preparation["requirements"])
            if preparation.get("positioning_requirement"):
                lines.append("  Position requirement: " + preparation["positioning_requirement"])
            for dependency in preparation.get("conditional_dependencies", []):
                task(dependency, "  First unlock setup dependency")
        for option in step.get("temporary_options", []):
            lines.append("Temporary choice " + option["slot"] + ": " + option["selected"] +
                         " (options: " + ", ".join(option["options"]) + ")")
        roster("Primary arrival", step["board"])
        changes(step)
        for label, key in (("Add", "added"), ("Remove", "removed"), ("Keep", "retained"),
                           ("Temporary", "temporary")):
            lines.append(f"{label}: " + (", ".join(step[key]) or "none"))
        for unlock in step["unlocks"]:
            task(unlock, "Conditional")
        for unlock in step.get("unlock_prep", []):
            task(unlock, "Future unlock prep for")
        roster("No-unlock alternative", step["fallback"])
        lines.extend("Milestone: " + item for item in step["milestones"])
        lines.append("Reason: " + step["reason"])
        if step.get("goal_board") is not None:
            roster("Exact final-level goal after upgrades", step["goal_board"])
        for index, phase in enumerate(step.get("upgrades", []), 1):
            roster(f"Upgrade phase {index} — buy before selling", phase["board"])
            changes(phase)
            for label, key in (("Buy/add", "added"), ("Then sell/remove", "removed"), ("Keep", "retained")):
                lines.append(f"{label}: " + (", ".join(phase[key]) or "none"))
            for unlock in phase["unlocks"]:
                task(unlock, "Conditional")
            lines.extend("Milestone: " + item for item in phase["milestones"])
            lines.append("Reason: " + phase["reason"])
    return "\n".join(lines)


def _format_traits(board):
    traits = [f"{row['name']} {row['count']} (tier {row['tier']})" for row in board["traits"]]
    return "Traits: " + (", ".join(traits) or "none")
