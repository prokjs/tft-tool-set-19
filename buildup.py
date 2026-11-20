"""Conditional, target-aware offline level planning, not a combat/economy simulator.

Shop access uses TFT Flow Patch 19.1's cost 1..5 odds (levels 1..11):
https://tftflow.com/tables/set19/shop-odds-pool-size-xp-table
A nonzero shop chance AND a published unlock minimum are necessary, not a
promise of a copy. Low-odds hits receive planning penalties and warnings.
Seven-cost units are quest-only: Sona 10, Rakan 8, Smeech 9; Baron 9 is an
explicit late-game planning heuristic, NOT a published unlock minimum.
Guaranteed earlier quest-copy rewards are not modeled: these gates are
conservative planning assumptions. Tasks, items, combat outcomes, gold, and
quest progress are never simulated or declared complete from trait counts.
Intermediate boards assume one star; final primary and fallback boards use the
requested stars (including K/DA's star counting). These are not leveling,
rolling, or upgrade guarantees. Small target-specific beams prefer affordable
target members, retention, useful task helpers and synergies over perfect
trait optimization. A separate no-unlock board exists at every feasible level.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from functools import cache

from optimizer import FRONTLINE_TRAITS, _evaluate_team, _integer, _names, _validate_data, optimize


SHOP_SOURCE = "https://tftflow.com/tables/set19/shop-odds-pool-size-xp-table"
SHOP_ODDS = (
    (100, 0, 0, 0, 0), (100, 0, 0, 0, 0), (75, 25, 0, 0, 0),
    (55, 30, 15, 0, 0), (45, 33, 20, 2, 0), (30, 40, 25, 5, 0),
    (16, 30, 43, 10, 1), (15, 20, 32, 30, 3), (10, 17, 25, 33, 15),
    (5, 10, 20, 40, 25), (1, 2, 12, 50, 35),
)
QUEST_PLANNING_LEVELS = {"Sona": 10, "Rakan": 8, "Smeech": 9, "Baron Nashor": 9}
_RESULT_KEYS = ("champions", "traits", "active", "tiers", "frontline", "cost", "score")


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
    return minimum


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


class _Planner:
    def __init__(self, traits, champions, collab, mode, sona_band):
        self.traits = traits
        self.champions = champions
        self.collab = set(collab) | {"Sona"}
        self.mode = mode
        self.sona_band = sona_band
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
        # All intermediate units are one-star, including star-counting traits.
        self.values = []
        for name in self.trait_names:
            trait = traits[name]
            values = []
            for count in range(12):
                tier = bisect_right(trait["breakpoints"], count)
                previous = trait["breakpoints"][tier - 1] if tier else 0
                following = trait["breakpoints"][tier] if tier < len(trait["breakpoints"]) else None
                progress = (count - previous) / (following - previous) if following else 0
                values.append(float(tier > 0) if trait["unique"] else
                              100 * int(tier > 0) + 10 * tier + 8 * progress)
            self.values.append(values)

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
                240 * int(name in target) + 55 * int(name in previous)
                + 320 * int(name in helpers) + 9 * len(self.unit_traits[name] & target_traits)
                + (85 if prepare_sona and name in self.collab else 0)
                - 9 * cost - rarity_penalty - 25 * int(champion.get("unlockable", False))
            )
        ranked = sorted(eligible, key=lambda name: (-weights[name], name))
        core = target | previous | helpers | (self.collab if prepare_sona else set())
        pool = [name for name in ranked if name in core]
        support = [name for name in ranked if name not in core]
        pool += support[:14]
        answer = self._search(level, pool, weights, prepare_sona)
        if answer is None and len(pool) < len(ranked):
            # Compact pools must never turn a feasible weighted board into None.
            answer = self._search(level, ranked, weights, prepare_sona)
        return None if answer is None else _evaluate_team(
            answer, self.traits, self.champions, sorted(self.collab - {"Sona"}),
            self.mode, self.sona_band, {},
        )

    def _search(self, level, pool, weights, prepare_sona):
        # Sequential knapsack beam, partitioned by slot total AND exclusion mask.
        # Feasibility depends only on those keys and the remaining suffix, so
        # pruning objective alternatives cannot destroy exact-slot feasibility.
        # State: merit, names, trait counts, frontline, collab count.
        empty = (0.0, (), (0,) * len(self.trait_names), 0, 0)
        buckets = [{0: [empty]}] + [{} for _ in range(level)]
        for name in pool:
            champion = self.champions[name]
            slots = champion.get("slots", 1)
            group = self.unit_groups[name]
            contributions = tuple(self.index[trait] for trait in sorted(self.unit_traits[name]))
            is_frontline = int(bool(self.unit_traits[name] & FRONTLINE_TRAITS))
            is_collab = int(name in self.collab)
            for filled in range(level - slots, -1, -1):
                for blocked, states in list(buckets[filled].items()):
                    if blocked & group:
                        continue
                    key = blocked | group
                    destination = buckets[filled + slots].setdefault(key, [])
                    for merit, picks, old_counts, frontline, collab_count in states:
                        counts = list(old_counts)
                        gain = weights[name]
                        for index in contributions:
                            count = counts[index]
                            gain += self.values[index][count + 1] - self.values[index][count]
                            counts[index] += 1
                        if self.mode in ("balanced", "high_value"):
                            gain += 30 * (min(frontline + is_frontline, 2) - min(frontline, 2))
                        if prepare_sona:
                            # An objective to START the 15-inspiration task, never proof of completion.
                            gain += 100 * (min(collab_count + is_collab, 3) - min(collab_count, 3))
                            gain += 200 * int(collab_count < 3 <= collab_count + is_collab)
                        destination.append((merit + gain, picks + (name,), tuple(counts),
                                            frontline + is_frontline, collab_count + is_collab))
                    destination.sort(key=lambda state: (-state[0], tuple(sorted(state[1]))))
                    del destination[8:]
        finalists = [state for states in buckets[level].values() for state in states]
        if not finalists:
            return None
        winner = min(finalists, key=lambda state: (-state[0], tuple(sorted(state[1]))))
        return sorted(winner[1])


def recommend_with_buildups(
    data: dict, collab: list[str], size: int, locked: list[str], excluded: list[str],
    min_collab: int, mode: str, sona_band: str = "", limit: int = 12,
    stars: dict[str, int] | None = None,
) -> list[dict]:
    """Return optimizer teams plus complete conditional builds, without mutating inputs.

    All final candidates are eligibility-gated BEFORE optimization. Locks describe
    end goals, not forced early copies. Every level has an exact-slot board or an
    honest None, and an independent all-quests-incomplete fallback. Primary final
    stats exactly match the optimizer under the same eligible pool and star map.
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
            access = (f"planning level {minimum} or above" if champion["cost"] == 7 else
                      f"nonzero {champion['cost']}-cost shop odds and unlock level {champion.get('unlock_level', 'Any')}")
            raise ValueError(f"{name} cannot be planned at level {size}; requires {access}. "
                             f"Unlock task: {champion.get('unlock_condition', 'No special unlock requirement.')}")
    results = optimize(data, collab, size, locked, sorted(set(excluded) | gated),
                       min_collab, mode, sona_band, limit, stars)
    planner = _Planner(traits, champions, collab, mode, sona_band)
    excluded_set = set(excluded)
    availability = {
        level: sorted(name for name, champion in champions.items()
                      if name not in excluded_set and _eligible(champion, level))
        for level in range(1, size + 1)
    }
    # Cache immutable inputs only; each recommendation owns its result dictionaries.
    @cache
    def plan(level, target, previous, helpers, prepare_sona, fallback):
        eligible = availability[level]
        if fallback:
            eligible = [name for name in eligible if not champions[name].get("unlockable")]
        return planner.board(level, eligible, set(target), set(previous), set(helpers), prepare_sona)

    for result in results:
        target = set(result["champions"])
        helpers = _helpers(target, champions)
        previous = set()
        previous_fallback = set()
        steps = []
        for level in range(1, size + 1):
            final = level == size
            prepare_sona = "Sona" in target and not final
            board = ({key: result[key] for key in _RESULT_KEYS} if final else
                     plan(level, tuple(sorted(target)), tuple(sorted(previous)),
                          tuple(sorted(helpers)), prepare_sona, False))
            fallback = plan(level, tuple(sorted(target)), tuple(sorted(previous_fallback)),
                            tuple(sorted(helpers)), prepare_sona, True)
            # Cached boards are copied so consumers cannot change another build.
            board = _copy_board(board)
            fallback = _copy_board(fallback)
            if final and fallback:
                fallback = _evaluate_team(fallback["champions"], traits, champions,
                                          collab, mode, sona_band, stars or {})
            current = set(board["champions"]) if board else set()
            goals = sorted(name for name in target | current if champions[name].get("unlockable"))
            conditional = sorted(name for name in current if champions[name].get("unlockable"))
            unlocks = [_unlock(champions[name]) for name in conditional]
            milestones = []
            if not board:
                milestones.append("No eligible exact-slot primary board at this level; exclusions, shop access, weighted slots or exclusivity prevent it.")
            if fallback is None:
                milestones.append("No exact-slot no-unlock alternative exists at this level under these exclusions and slot constraints.")
            if unlocks:
                milestones.append("Conditional plan only: complete each listed task before fielding its champion; quest progress is not assumed or simulated.")
            for name in goals:
                minimum = _minimum_level(champions[name])
                if level < minimum:
                    milestones.append(f"{name}: wait until planning level {minimum}; published unlock minimum: {champions[name].get('unlock_level', 'Any')}.")
            for helper in sorted(helpers & current):
                helped = [name for name in goals if helper in _helpers({name}, champions)]
                for name in helped:
                    milestones.append(f"Field {helper} to work toward {name}: {champions[name]['unlock_condition']} Required upgrades/items/progress still need to be earned.")
            collab_count = sum(name in planner.collab for name in current)
            if "Sona" in target:
                milestones.append(
                    "Work toward Sona's 15 inspirations with 3 Collab active; this board does not prove any inspirations earned."
                    if collab_count >= 3 else
                    "Build toward 3 Collab to begin Sona's 15-inspiration task; no progress is assumed."
                )
            for name in sorted(current):
                champion = champions[name]
                cost = champion["cost"]
                if name not in previous and 1 <= cost <= 5 and SHOP_ODDS[level - 1][cost - 1] <= 5:
                    odds = SHOP_ODDS[level - 1][cost - 1]
                    milestones.append(f"Rare shop hit: {name}'s {cost}-cost tier has {odds}% odds per shop slot at level {level}; not a guaranteed copy.")
                if cost == 7:
                    milestones.append(f"{name} is quest-only, not a regular shop roll; unlock completion is conditional.")
                if name == "Baron Nashor":
                    milestones.append("Baron consumes 2 board slots. Level 9 is a conservative late-game planning heuristic, not a published unlock minimum (Any).")
            if level == 11:
                milestones.append("Level 11 requires a special level-cap extension; ordinary leveling is not assumed to reach it.")
            temporary = sorted(current - target)
            reason = ("Exact requested end composition; requested final star assumptions apply." if final else
                      "Prefer obtainable target members, retained units and useful synergies; temporary units bridge unavailable targets.")
            if not board:
                reason = "No feasible exact-slot board from eligible champions at this level; do not invent a unit or claim the next-level board is available early."
            elif temporary:
                reason += " Replace temporary units as target members become eligible; shop access is not a purchase guarantee."
            displayed = current | (set(fallback["champions"]) if fallback else set())
            step_stars = {name: (stars or {}).get(name, 1) if final else 1 for name in sorted(displayed)}
            steps.append({
                "level": level, "board": board, "fallback": fallback,
                "added": sorted(current - previous), "removed": sorted(previous - current),
                "retained": sorted(current & previous), "temporary": temporary,
                "unlocks": unlocks, "milestones": milestones, "stars": step_stars, "reason": reason,
            })
            previous = current
            previous_fallback = set(fallback["champions"]) if fallback else set()
        result["buildup"] = steps
        result["buildup_notes"] = [
            "Conservative planning, not combat, item, gold, rolling or quest-progress simulation. Every unlockable primary unit is conditional on its task.",
            "Intermediate primary and fallback boards assume 1 star. Final primary and fallback boards use requested stars; K/DA counts those assumptions.",
            "End-goal locks are not forced at earlier levels. Temporary units are replaced as target units become eligible; exclusions apply throughout.",
            "A nonzero ordinary-shop chance and published unlock minimum gate eligibility. Guaranteed earlier unlock-copy rewards are not modeled.",
            "Seven-cost units are quest-only. Baron level 9 is a planning heuristic, not its published minimum (Any); Sona 10, Rakan 8 and Smeech 9 retain their published gates.",
            "No-unlock alternatives exclude every unlockable unit and may not satisfy the final Collab minimum or end-goal locks. None means no exact-slot alternative exists.",
            f"Shop odds source: TFT Flow Patch 19.1 — {SHOP_SOURCE}",
        ]
        if size == 11:
            result["buildup_notes"].append("Level 11 requires a special level-cap extension, not ordinary leveling.")
    return results


def _copy_board(board):
    if board is None:
        return None
    return {**board, "champions": list(board["champions"]),
            "traits": [dict(row) for row in board["traits"]]}


def format_buildup(result: dict, champions) -> str:
    """Plain-text shared copy/export format for an already-computed build."""
    champion_map = champions if isinstance(champions, dict) else {c["name"]: c for c in champions}
    lines = ["Final team: " + ", ".join(result["champions"])]
    lines.extend("Note: " + note for note in result.get("buildup_notes", []))
    for step in result.get("buildup", []):
        board = step["board"]
        lines.append(f"\nLevel {step['level']}")
        if board is None:
            lines.append("Primary: no feasible exact-slot board.")
        else:
            units = [f"{name} ({step['stars'].get(name, 1)}★" +
                     (", conditional unlock" if champion_map.get(name, {}).get("unlockable") else "") + ")"
                     for name in board["champions"]]
            lines.append("Primary: " + ", ".join(units))
            lines.append(_format_traits(board))
        for label, key in (("Add", "added"), ("Remove", "removed"), ("Keep", "retained"),
                           ("Temporary", "temporary")):
            lines.append(f"{label}: " + (", ".join(step[key]) or "none"))
        for unlock in step["unlocks"]:
            lines.append(f"Conditional {unlock['name']} [{unlock['level']}]: {unlock['condition']}")
            if unlock["source"]:
                lines.append("  Source: " + unlock["source"])
            if unlock.get("note"):
                lines.append("  " + unlock["note"])
        fallback = step["fallback"]
        lines.append("No-unlock alternative: " +
                     (", ".join(f"{name} ({step['stars'].get(name, 1)}★)" for name in fallback["champions"])
                      if fallback else "no feasible exact-slot board"))
        if fallback:
            lines.append(_format_traits(fallback))
        lines.extend("Milestone: " + item for item in step["milestones"])
        lines.append("Reason: " + step["reason"])
    return "\n".join(lines)


def _format_traits(board):
    traits = [f"{row['name']} {row['count']} (tier {row['tier']})" for row in board["traits"]]
    return "Traits: " + (", ".join(traits) or "none")
