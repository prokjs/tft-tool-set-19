"""Deterministic, approximate trait search for offline Set 19 planning.

Teams fill an exact slot budget (not necessarily that many champions). Beam
search is not exhaustive and does not promise a global optimum. Nonunique
active traits and their tiers drive scoring; unique traits are shown separately.
Balanced mode adds a composition proxy: reward the first two units carrying
Bodyguard, Juggernaut, or Frontman and at least one other unit. High-value mode
adds the same balance proxy plus 30 points per gold of base champion cost, versus
100 per active trait and 10 per trait tier. Cost is a rarity/value proxy, not a
combat simulation. Other modes break equal rankings toward cheaper teams.
"""

from __future__ import annotations

import heapq
import json
from bisect import bisect_right
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any


BAND_TRAITS = (
    "Beach Pop", "Classical", "EDM", "Gu Feng", "Heartsteel", "Hyperpop",
    "Illbeats", "Ionic Spark", "Jazz", "K/DA", "Pentakill", "R&B", "Rock",
    "True Damage",
)
FRONTLINE_TRAITS = frozenset({"Bodyguard", "Juggernaut", "Frontman"})


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}.")
    return value


def _names(value: Any, name: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a list of champion names.")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} contains an invalid name.")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicates.")
    return list(value)


def _validate_data(data: Any) -> tuple[dict[str, dict], dict[str, dict]]:
    if not isinstance(data, dict):
        raise ValueError("Data must be a JSON object.")
    if not isinstance(data.get("traits"), list) or not isinstance(data.get("champions"), list):
        raise ValueError("Data must contain traits and champions lists.")
    traits: dict[str, dict] = {}
    for trait in data["traits"]:
        if not isinstance(trait, dict):
            raise ValueError("Each trait must be an object.")
        name = trait.get("name")
        if not isinstance(name, str) or not name.strip() or name in traits:
            raise ValueError("Trait names must be nonempty and unique.")
        points = trait.get("breakpoints")
        if not isinstance(points, list) or not points:
            raise ValueError(f"{name} must have breakpoints.")
        for point in points:
            _integer(point, f"{name} breakpoint", 1)
        if points != sorted(set(points)):
            raise ValueError(f"{name} breakpoints must increase strictly.")
        if not isinstance(trait.get("unique"), bool):
            raise ValueError(f"{name} must specify whether it is unique.")
        if trait.get("count_mode", "units") not in ("units", "stars"):
            raise ValueError(f"{name} has an invalid count_mode.")
        traits[name] = trait
    champions: dict[str, dict] = {}
    for champion in data["champions"]:
        if not isinstance(champion, dict):
            raise ValueError("Each champion must be an object.")
        name = champion.get("name")
        if not isinstance(name, str) or not name.strip() or name in champions:
            raise ValueError("Champion names must be nonempty and unique.")
        _integer(champion.get("cost"), f"{name} cost")
        _integer(champion.get("slots", 1), f"{name} slots", 1)
        group = champion.get("exclusive_group")
        if group is not None and (not isinstance(group, str) or not group.strip()):
            raise ValueError(f"{name} has an invalid exclusive_group.")
        champion_traits = champion.get("traits")
        if not isinstance(champion_traits, list) or any(
            not isinstance(trait, str) or trait not in traits for trait in champion_traits
        ):
            raise ValueError(f"{name} has invalid traits.")
        champions[name] = champion
    return traits, champions


def load_data(path: str | Path) -> dict:
    """Load and validate UTF-8 data; file, JSON, and schema errors are ValueError."""
    try:
        with open(path, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as error:
        raise ValueError(f"Cannot load Set 19 data: {error}") from error
    _validate_data(data)
    return data


@dataclass(frozen=True, slots=True)
class _State:
    picks: tuple[int, ...]
    counts: tuple[int, ...]
    active: int
    tiers: int
    unique: int
    progress: float
    frontline: int
    cost: int
    collab: int
    mask: int
    exclusive: int


def _evaluate_team(
    names: list[str], trait_map: dict[str, dict], champion_map: dict[str, dict],
    collab: list[str], mode: str, sona_band: str, stars: dict[str, int],
) -> dict:
    """Evaluate a legal board with prevalidated inputs; shared with buildup."""
    counts: dict[str, int] = {}
    dynamic_collab = set(collab) | {"Sona"}
    frontline = 0
    cost = 0
    for name in names:
        champion = champion_map[name]
        traits = set(champion["traits"]) - {"Collab"}
        if name in dynamic_collab:
            traits.add("Collab")
        if name == "Sona" and sona_band:
            traits.add(sona_band)
        frontline += int(bool(traits & FRONTLINE_TRAITS))
        cost += champion["cost"]
        for trait in traits:
            increment = stars.get(name, 1) if trait_map[trait].get("count_mode") == "stars" else 1
            counts[trait] = counts.get(trait, 0) + increment
    rows = []
    active = tiers = unique = 0
    for name, count in sorted(counts.items()):
        trait = trait_map[name]
        points = trait["breakpoints"]
        tier = bisect_right(points, count)
        if trait["unique"]:
            unique += int(tier > 0)
        else:
            active += int(tier > 0)
            tiers += tier
        rows.append({
            "name": name, "count": count,
            "tier": points[tier - 1] if tier else 0,
            "next": points[tier] if tier < len(points) else None,
            "unique": trait["unique"],
        })
    score = active * 100.0 + tiers * 10.0 + unique
    if mode in ("balanced", "high_value"):
        score += 30.0 * min(frontline, 2)
        score += 20.0 * int(len(names) > frontline)
    if mode == "high_value":
        score += 30.0 * cost
    return {
        "champions": sorted(names), "traits": rows, "active": active, "tiers": tiers,
        "frontline": frontline, "cost": cost, "score": score,
    }


def optimize(
    data: dict,
    collab: list[str],
    size: int,
    locked: list[str],
    excluded: list[str],
    min_collab: int,
    mode: str,
    sona_band: str = "",
    limit: int = 12,
    stars: dict[str, int] | None = None,
) -> list[dict]:
    """Return diverse, deterministic approximate teams, or raise ValueError.

    ``size`` is an exact 1..11 slot budget. Each champion occurs at most once.
    Collab is granted to the selected group and always to Sona, never inherited
    from another champion's static data. A selected Sona band is added once.
    Star-counting traits use the per-champion 1..4 ``stars`` map (default one).
    ``active`` and ``tiers`` exclude unique traits; ``tiers`` sums ordinal levels.
    Trait-row ``tier`` is the reached breakpoint (zero when inactive).
    ``score`` is a composition heuristic, not predicted viability. Exact suffix
    feasibility protects Collab, weighted slots, and mutually exclusive variants.
    The finite beam still approximates objective optimization.
    """
    trait_map, champion_map = _validate_data(data)
    _integer(size, "size", 1)
    if size > 11:
        raise ValueError("size must be between 1 and 11 slots.")
    _integer(min_collab, "min_collab")
    if min_collab not in (0, 3, 5):
        raise ValueError("min_collab must be 0, 3, or 5.")
    _integer(limit, "limit", 1)
    if mode not in ("synergies", "balanced", "high_value"):
        raise ValueError("mode must be 'synergies', 'balanced', or 'high_value'.")
    collab = _names(collab, "collab")
    locked = _names(locked, "locked")
    excluded = _names(excluded, "excluded")
    for label, names in (("collab", collab), ("locked", locked), ("excluded", excluded)):
        unknown = set(names) - champion_map.keys()
        if unknown:
            raise ValueError(f"Unknown {label} champion: {sorted(unknown)[0]}.")
    if set(locked) & set(excluded):
        raise ValueError("A champion cannot be both locked and excluded.")
    if not isinstance(sona_band, str) or (
        sona_band and (sona_band not in BAND_TRAITS or sona_band not in trait_map)
    ):
        raise ValueError("sona_band must be empty or a valid band trait.")
    if stars is None:
        stars = {}
    if not isinstance(stars, dict):
        raise ValueError("stars must be a champion-to-star-level mapping.")
    for name, level in stars.items():
        if not isinstance(name, str) or name not in champion_map:
            raise ValueError("stars contains an unknown champion.")
        _integer(level, f"{name} stars", 1)
        if level > 4:
            raise ValueError("Star levels must be between 1 and 4.")

    trait_names = sorted(trait_map)
    trait_index = {name: index for index, name in enumerate(trait_names)}
    breakpoints = [trait_map[name]["breakpoints"] for name in trait_names]
    is_unique = [trait_map[name]["unique"] for name in trait_names]
    dynamic_collab = set(collab) | ({"Sona"} if "Sona" in champion_map else set())
    if dynamic_collab and "Collab" not in trait_map:
        raise ValueError("Collab must be defined when Collab champions are present.")
    excluded_set = set(excluded)
    locked_set = set(locked)
    available = sorted(set(champion_map) - excluded_set - locked_set)
    locked_names = sorted(locked_set)
    groups = sorted({champion["exclusive_group"] for champion in champion_map.values()
                     if champion.get("exclusive_group") is not None})
    group_bits = {name: 1 << index for index, name in enumerate(groups)}

    def unit(name: str) -> tuple[tuple[tuple[int, int], ...], int, int, int, int, int]:
        champion = champion_map[name]
        names = set(champion["traits"]) - {"Collab"}
        if name in dynamic_collab:
            names.add("Collab")
        if name == "Sona" and sona_band:
            names.add(sona_band)
        contributions = tuple(
            (trait_index[trait], stars.get(name, 1)
             if trait_map[trait].get("count_mode", "units") == "stars" else 1)
            for trait in sorted(names)
        )
        return (
            contributions, champion.get("slots", 1),
            int(bool(names & FRONTLINE_TRAITS)), champion["cost"],
            int(name in dynamic_collab),
            group_bits.get(champion.get("exclusive_group"), 0),
        )

    optional = [unit(name) for name in available]
    fixed = [unit(name) for name in locked_names]
    occupied = sum(item[1] for item in fixed)
    if occupied > size:
        raise ValueError("Locked champions exceed the slot budget.")
    fixed_exclusive = 0
    for item in fixed:
        if fixed_exclusive & item[5]:
            raise ValueError("Locked champions include mutually exclusive variants.")
        fixed_exclusive |= item[5]

    # The maximum attainable Collab count for an exact suffix slot total.
    # Carrying exclusivity through the cache makes the feasibility test exact,
    # including mutually exclusive variants and boards containing only 2-slots.
    @cache
    def attainable(start: int, budget: int, blocked: int) -> int:
        if budget == 0:
            return 0
        if start == len(optional):
            return -1
        best = attainable(start + 1, budget, blocked)
        item = optional[start]
        if item[1] <= budget and not blocked & item[5]:
            following = attainable(start + 1, budget - item[1], blocked | item[5])
            if following >= 0:
                best = max(best, following + item[4])
        return best

    fixed_collab = sum(item[4] for item in fixed)
    if attainable(0, size - occupied, fixed_exclusive) < max(0, min_collab - fixed_collab):
        raise ValueError("No team can fill the slots while satisfying the Collab minimum.")

    # Cache contribution scores at each reachable count. Progress encourages
    # reaching a forthcoming breakpoint, but is used only for intermediate beams.
    max_count = max(size * 4, 1)
    values = []
    for points in breakpoints:
        table = []
        for count in range(max_count + 1):
            tier = bisect_right(points, count)
            next_point = points[tier] if tier < len(points) else None
            previous = points[tier - 1] if tier else 0
            progress = (count - previous) / (next_point - previous) if next_point else 0.0
            table.append((tier, progress))
        values.append(table)

    empty = _State((), (0,) * len(trait_names), 0, 0, 0, 0.0, 0, 0, 0, 0, 0)

    def extend(state: _State, item: tuple, index: int | None) -> _State:
        contributions, _, frontline, cost, collab_count, exclusive = item
        counts = list(state.counts)
        active, tiers, unique, progress = state.active, state.tiers, state.unique, state.progress
        for trait, increment in contributions:
            old_tier, old_progress = values[trait][counts[trait]]
            counts[trait] += increment
            new_tier, new_progress = values[trait][counts[trait]]
            if is_unique[trait]:
                unique += int(new_tier > 0) - int(old_tier > 0)
            else:
                active += int(new_tier > 0) - int(old_tier > 0)
                tiers += new_tier - old_tier
                progress += new_progress - old_progress
        return _State(
            state.picks + (index,) if index is not None else state.picks,
            tuple(counts), active, tiers, unique, progress,
            state.frontline + frontline, state.cost + cost,
            state.collab + collab_count,
            state.mask | (1 << index) if index is not None else state.mask,
            state.exclusive | exclusive,
        )

    initial = empty
    for item in fixed:
        initial = extend(initial, item, None)

    def score(state: _State) -> float:
        value = state.active * 100.0 + state.tiers * 10.0 + state.unique
        if mode in ("balanced", "high_value"):
            unit_count = len(locked_names) + len(state.picks)
            value += 30.0 * min(state.frontline, 2)
            value += 20.0 * int(unit_count > state.frontline)
        if mode == "high_value":
            value += 30.0 * state.cost
        return value

    beam_width = max(256, min(1024, limit * 24))
    heaps: list[list[tuple]] = [[] for _ in range(size + 1)]
    serial = 0

    def push(slots: int, state: _State) -> None:
        nonlocal serial
        serial += 1
        rank = score(state) + (6.0 * state.progress if slots < size else 0.0)
        cost_rank = state.cost if mode == "high_value" else -state.cost
        entry = (rank, state.active, state.tiers, cost_rank, -serial, state)
        capacity = max(beam_width, limit) if slots == size else beam_width
        heap = heaps[slots]
        if len(heap) < capacity:
            heapq.heappush(heap, entry)
        elif entry[:5] > heap[0][:5]:
            heapq.heapreplace(heap, entry)

    push(occupied, initial)
    for filled in range(occupied, size):
        states = sorted(heaps[filled], reverse=True)
        heaps[filled] = []
        for entry in states:
            state = entry[-1]
            start = state.picks[-1] + 1 if state.picks else 0
            for index in range(start, len(optional)):
                item = optional[index]
                next_filled = filled + item[1]
                if next_filled > size or state.exclusive & item[5]:
                    continue
                needed = max(0, min_collab - state.collab - item[4])
                if attainable(index + 1, size - next_filled, state.exclusive | item[5]) < needed:
                    continue
                push(next_filled, extend(state, item, index))

    candidates = [entry[-1] for entry in heaps[size]]

    def team_names(state: _State) -> tuple[str, ...]:
        return tuple(sorted(locked_names + [available[index] for index in state.picks]))

    candidates.sort(key=lambda state: (-score(state), -state.active, -state.tiers,
                                       -state.cost if mode == "high_value" else state.cost, team_names(state)))
    # Greedy overlap penalty keeps near-tied alternatives meaningfully different.
    # Locked units are omitted from overlap because they cannot diversify.
    chosen: list[_State] = []
    penalties = [0.0] * len(candidates)
    remaining = list(range(len(candidates)))
    while remaining and len(chosen) < limit:
        best = max(remaining, key=lambda index: (score(candidates[index]) - penalties[index], -index))
        selected = candidates[best]
        chosen.append(selected)
        remaining.remove(best)
        for index in remaining:
            other = candidates[index]
            denominator = max(len(selected.picks), len(other.picks), 1)
            overlap = (selected.mask & other.mask).bit_count() / denominator
            penalties[index] = max(penalties[index], 30.0 * overlap)
    chosen.sort(key=lambda state: (-score(state), -state.active, -state.tiers,
                                   -state.cost if mode == "high_value" else state.cost, team_names(state)))
    return [
        _evaluate_team(list(team_names(state)), trait_map, champion_map,
                       collab, mode, sona_band, stars)
        for state in chosen
    ]
