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
from bisect import bisect_right
from functools import cache

from itertools import combinations
from champion_info import is_tank, star_text
from optimizer import (
    FRONTLINE_TRAITS, _evaluate_team, _frontline_anchor_value, _integer, _jazz_bonus,
    _jazz_links, _names, _strategy_masks, _trait_context_adjustment, _trait_value,
    _unit_strategy_value, _validate_data, optimize, planned_stars,
)


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


def _minimum_level(champion: dict) -> int:
    published = champion["unlock_level"] if champion.get("unlockable") else "Any"
    match = re.fullmatch(r"Level (\d+)", published) if isinstance(published, str) else None
    if published != "Any" and not match:
        raise ValueError(f"{champion['name']} has an invalid unlock level: {published!r}.")
    minimum = int(match.group(1)) if match else 1
    if champion["cost"] == 7 and champion.get("unlockable"):
        return max(minimum, QUEST_PLANNING_LEVELS.get(champion["name"], 9))
    return max(minimum, SHOP_PLANNING_LEVELS.get(champion["cost"], 1))


def _eligible(champion: dict, level: int) -> bool:
    if level < _minimum_level(champion):
        return False
    cost = champion["cost"]
    if cost == 7:
        return bool(champion.get("unlockable"))
    return 1 <= cost <= 5 and SHOP_ODDS[level - 1][cost - 1] > 0


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


def _prep_recipes(target, champions, excluded=()):
    """Source-worded distinct-unit recipes, never star-level substitutes."""
    recipes = {}
    for name in sorted(target):
        champion = champions[name]
        match = re.fullmatch(r"Have (\d+) (?:unique )?(.+?) champions\.",
                             champion.get("unlock_condition", ""))
        if not match:
            continue
        alternatives = match.group(2).split(" or ")
        count = int(match.group(1))
        candidates = [
            other for other, unit in champions.items() if other != name
            and not unit.get("unlockable") and other not in excluded
            and set(unit["traits"]) & set(alternatives)
        ]
        primary = [other for other in candidates if alternatives[0] in champions[other]["traits"]]
        if len(primary) >= count:
            candidates = primary
        recipes[name] = {
            "traits": alternatives, "count": count, "count_mode": "distinct_champions",
            "candidates": sorted(candidates, key=lambda other: (
                _minimum_level(champions[other]), champions[other]["cost"], other)),
        }
    return recipes


class _Planner:
    def __init__(self, traits, champions, collab, mode, sona_band,
                 requested_stars=None, final_level=None):
        self.traits = traits
        self.champions = champions
        self.collab = set(collab) | {"Sona"}
        self.mode = mode
        self.sona_band = sona_band
        self.requested_stars = dict(requested_stars or {})
        self.final_level = final_level
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
        for level in range(1, 12):
            requested = self.requested_stars if level >= 10 or level == final_level else None
            stars = planned_stars(champions, level, requested)
            self.stage_stars[level] = stars
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

    def board(self, level, eligible, target, previous, helpers, prepare_sona):
        if not eligible:
            return None
        target_traits = set().union(*(self.unit_traits[name] for name in target)) if target else set()
        # Never plan the other variant on a path toward a chosen exclusive unit.
        goal_groups = {self.unit_groups[name]: name for name in target if self.unit_groups[name]}
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
                + (85 if prepare_sona and name in self.collab else 0)
                - 9 * cost - rarity_penalty - 25 * int(champion.get("unlockable", False))
            )
        ranked = sorted(eligible, key=lambda name: (-weights[name], name))
        core = target | previous | helpers | (self.collab if prepare_sona else set())
        pool = [name for name in ranked if name in core]
        support = [name for name in ranked if name not in core]
        pool += support[:14]
        answer = self._search(level, pool, weights, prepare_sona, previous)
        cap = 2 if level >= 8 else 1
        if len(pool) < len(ranked) and (answer is None or len(previous - set(answer)) > cap):
            # A compact pool must not lose exact-slot or retention feasibility.
            answer = self._search(level, ranked, weights, prepare_sona, previous)
        return None if answer is None else self.evaluate(level, answer)

    def evaluate(self, level, names):
        return _evaluate_team(
            names, self.traits, self.champions, sorted(self.collab - {"Sona"}),
            self.mode, self.sona_band,
            self.requested_stars if level >= 10 or level == self.final_level else {},
            level=level,
        )

    def _search(self, level, pool, weights, prepare_sona, previous=frozenset()):
        # Retained count is a feasibility key, not merely a scoring bonus.
        # A high-merit low-retention prefix cannot prune a smooth completion.
        # Unearned tier progress guides partial prefixes, never full-board merit.
        empty = (0.0, (), (0,) * len(self.trait_names), 0, 0, 0, False, 0, 0.0, 0.0)
        buckets = [{(0, 0): [empty]}] + [{} for _ in range(level)]
        increments = self.stage_increments[level]
        context = self.stage_context[level]
        unit_values = self.stage_units[level]
        anchors = self.stage_anchors[level]
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
                    for merit, picks, old_counts, frontline, collab_count, mask, bridge, active, anchor, future in states:
                        counts = list(old_counts)
                        gain = weights[name] + unit_values[name]
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
                                            next_mask, next_bridge, next_active, next_anchor, next_future))
                    destination.sort(key=lambda state: (
                        -(state[0] + (state[-1] if filled + slots < level else 0.0)),
                        tuple(sorted(state[1])),
                    ))
                    del destination[8:]
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
                            choices.append((len(added), removed, added))
            if choices:
                _, removed, added = min(choices)
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


def recommend_with_buildups(
    data: dict, collab: list[str], size: int, locked: list[str], excluded: list[str],
    min_collab: int, mode: str, sona_band: str = "", limit: int = 12,
    stars: dict[str, int] | None = None,
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
    gated = {name for name, champion in champions.items() if not _eligible(champion, size)}
    for name in locked:
        if name in gated:
            champion = champions[name]
            minimum = _minimum_level(champion)
            raise ValueError(f"{name} cannot be planned at level {size}; requires planning level {minimum} or above "
                             f"({champion['cost']}-cost; published unlock minimum: {champion.get('unlock_level', 'Any')}). "
                             f"This is conservative timing, not game impossibility. "
                             f"Unlock task: {champion.get('unlock_condition', 'No special unlock requirement.')}")
    default_blocked = _chain_exclusions(champions, collab, sona_band, set())
    chain_blocked = default_blocked - set(locked)
    results = optimize(data, collab, size, locked, sorted(set(excluded) | gated | chain_blocked),
                       min_collab, mode, sona_band, limit, stars)
    planner = _Planner(traits, champions, collab, mode, sona_band, stars, size)
    excluded_set = set(excluded)
    # Cache immutable inputs only; each recommendation owns its dictionaries.
    @cache
    def plan(level, target, previous, helpers, prepare_sona, fallback, dependencies, waiting):
        eligible = sorted(
            name for name, champion in champions.items()
            if name not in excluded_set and _eligible(champion, level)
            and (name not in chain_blocked or name in dependencies)
            and (not fallback or not champion.get("unlockable"))
            and name not in waiting
        )
        return planner.board(level, eligible, set(target), set(previous), set(helpers), prepare_sona)
    for result in results:
        result["collab_pool"] = [name for name in collab if name != "Sona"]
        result["sona_band"] = sona_band
        target = set(result["champions"])
        helpers = _dependency_helpers(target, champions)
        dependencies = helpers.copy()
        if "Teemo" in target or any(name in locked and "Hyperpop" in champions[name]["traits"] for name in target):
            dependencies.update(name for name, champion in champions.items()
                                if "Hyperpop" in champion["traits"])
        recipes = _prep_recipes(target, champions, excluded_set)
        prepared = set()
        previous = set()
        previous_fallback = set()
        steps = []
        for level in range(1, size + 1):
            final = level == size
            pending = {name for name in target - previous if champions[name].get("unlockable")}
            useful_helpers = _dependency_helpers(pending, champions)
            for name, recipe in recipes.items():
                if len(previous & set(recipe["candidates"])) >= recipe["count"]:
                    prepared.add(name)
            waiting = set(recipes) - prepared - previous
            recipe_goals = pending & recipes.keys()
            focus = min(recipe_goals, key=lambda name: (_minimum_level(champions[name]), name)) if recipe_goals else None
            if focus:
                recipe = recipes[focus]
                candidates = [other for other in recipe["candidates"]
                              if other not in excluded_set and _eligible(champions[other], level)]
                useful_helpers.update(candidates[:recipe["count"]])
                if focus in prepared:
                    # The setup roster has been shown, not the task completed.
                    # Prioritize this conditional acquisition over another chain.
                    useful_helpers.add(focus)
            if "Teemo" in pending:
                useful_helpers.update(name for name in dependencies - target
                                      if "Hyperpop" in champions[name]["traits"])
            useful_helpers.update(_dependency_helpers(useful_helpers - previous, champions))
            for name in (target | dependencies) - previous:
                if champions[name].get("unlockable"):
                    named = _helpers({name}, champions)
                    if named and not named & previous:
                        waiting.add(name)
            prepare_sona = "Sona" in pending
            board = plan(level, tuple(sorted(target)), tuple(sorted(previous)),
                         tuple(sorted(useful_helpers)), prepare_sona, False,
                         tuple(sorted(dependencies)), tuple(sorted(waiting)))
            if board is None and waiting:
                # A recipe can be impossible under explicit exclusions. Keep
                # the goal conditional rather than inventing prerequisite units.
                board = plan(level, tuple(sorted(target)), tuple(sorted(previous)),
                             tuple(sorted(useful_helpers)), prepare_sona, False,
                             tuple(sorted(dependencies)), ())
            fallback = plan(level, tuple(sorted(target)), tuple(sorted(previous_fallback)),
                            (), prepare_sona, True, (), ())
            board = _copy_board(board)
            fallback = _copy_board(fallback)
            current = set(board["champions"]) if board else set()
            goals = sorted(name for name in target | current if champions[name].get("unlockable"))
            goal = _copy_board({key: result[key] for key in _RESULT_KEYS}) if final else None
            upgrades = _upgrade_phases(planner, level, board, goal) if final else []
            prep_goals = target | (useful_helpers & dependencies)
            prep = [{**_unlock(champions[name]),
                     "helpers": sorted(_dependency_helpers({name}, champions) & current)}
                    for name in sorted(prep_goals - current) if champions[name].get("unlockable")]
            for item in prep:
                recipe = recipes.get(item["name"])
                if recipe:
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
                minimum = _minimum_level(champions[name])
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
                "retained": sorted(current & previous), "temporary": temporary,
                "unlocks": unlocks, "milestones": milestones, "stars": step_stars, "reason": reason,
            })
            previous = current
            previous_fallback = set(fallback["champions"]) if fallback else set()
        result["buildup"] = steps
        result["buildup_notes"] = [
            "Conservative planning, not combat, item, gold, rolling or quest-progress simulation. Every unlockable primary unit is conditional on its task.",
            "Before level 10, nonfinal primary and fallback boards assume one star. Final boards and level 10+ stages use planned stars, including default two-star four-cost Tanks; explicit overrides win.",
            "Planned upgrades are targets, not owned units. Fast-10 rankings apply conditional trait, Senna-preference and reroll-path heuristics, not gold or win-rate estimates.",
            "End-goal locks are not forced at earlier levels. Temporary units are replaced as target units become eligible; exclusions apply throughout.",
            "A nonzero ordinary-shop chance and published unlock minimum gate eligibility. Guaranteed earlier unlock-copy rewards are not modeled.",
            "Four-cost planning starts at level 7; five-cost planning starts at level 9. These realistic defaults are not claims earlier hits are impossible; published unlock minima still apply.",
            "No-unlock alternatives exclude every unlockable unit and may not satisfy the final Collab minimum or end-goal locks. None means no exact-slot alternative exists.",
            f"Shop odds source: TFT Flow Patch 19.1 — {SHOP_SOURCE}",
        ]
        result["buildup_notes"].extend([
            "Primary arrivals normally remove at most one unit per level below 8 and two at 8+. Final upgrades are separate exact-slot conditional exchanges: buy before selling and hold arrival until ready.",
            "Unrelated long unlock chains require an actual Collab native band or featured band. Purposeful dependencies of selected goals are allowed in the guide only; no quest progress is assumed.",
        ])
        for name in sorted(set(locked) & default_blocked):
            result["buildup_notes"].append(f"Manual chain lock: {name} overrides the default unrelated-chain restriction. Complete its task and dependencies; no acquired copies, stars, items or progress are assumed.")
        if size == 11:
            result["buildup_notes"].append("Level 11 requires a special level-cap extension, not ordinary leveling.")
    return results


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

    roster("Exact end goal (not the leveling arrival)", result)
    lines.extend("Note: " + note for note in result.get("buildup_notes", []))
    for step in result.get("buildup", []):
        lines.append(f"\nLevel {step['level']}")
        roster("Primary arrival", step["board"])
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
