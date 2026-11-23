"""Deterministic, approximate trait search for offline Set 19 planning.

Teams fill an exact slot budget (not necessarily that many champions). Beam
search is not exhaustive and does not promise a global optimum. Most-active mode
rewards trait breadth; balanced and high-value modes reward deeper trait tiers.
Unique traits are shown separately and contribute one point each. Balanced mode
adds a proxy for two frontline units and one other unit; high-value also adds
30 points per gold of base cost. Activated Jazz gains an extra trait-diversity
bonus only when fielded Jazz connects to the selected Collab core. These are
ranking heuristics, not combat or Riot buff calculations.
"""

from __future__ import annotations

import heapq
import json
from bisect import bisect_right
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from champion_info import is_tank, star_text
from team_guidance import carry_flags, has_late_carry


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


def _trait_value(tier: int, mode: str) -> float:
    """Value a nonunique ordinal tier without changing breadth-mode semantics."""
    if mode == "synergies":
        return 100.0 * bool(tier) + 10.0 * tier
    return 110.0 * tier + 80.0 * tier * (tier - 1) / 2


def _jazz_bonus(active: int, jazz_tier: int, bridge: bool, mode: str) -> float:
    """Reward activated Jazz only when it connects to a fielded Collab core."""
    if bridge and jazz_tier > 0 and mode in ("balanced", "high_value"):
        return 20.0 * active * jazz_tier
    return 0.0


def _jazz_links(
    trait_map: dict[str, dict], champion_map: dict[str, dict],
    collab: list[str], sona_band: str,
) -> tuple[dict[str, int], dict[str, int], set[str]]:
    """Precompute symmetric native-trait links; masks still require fielding."""
    names = sorted(champion_map)
    bits = {name: 1 << index for index, name in enumerate(names)}
    partners = {name: 0 for name in names}
    if "Jazz" not in trait_map:
        return bits, partners, set()
    collab_names = set(collab) & champion_map.keys()
    if "Sona" in champion_map:
        collab_names.add("Sona")
    regular = {
        name for name, trait in trait_map.items()
        if not trait["unique"] and name not in ("Jazz", "Collab")
    }
    native = {
        name: set(champion["traits"]) & regular
        for name, champion in champion_map.items()
    }
    jazz_names = {
        name for name, champion in champion_map.items() if "Jazz" in champion["traits"]
    }
    if sona_band == "Jazz" and "Sona" in champion_map:
        jazz_names.add("Sona")
    direct = jazz_names & collab_names
    collab_traits: dict[str, int] = {}
    for name in collab_names:
        for trait in native[name]:
            collab_traits[trait] = collab_traits.get(trait, 0) | bits[name]
    for name in jazz_names:
        mask = 0
        for trait in native[name]:
            mask |= collab_traits.get(trait, 0)
        mask &= ~bits[name]
        partners[name] |= mask
        while mask:
            bit = mask & -mask
            partners[names[bit.bit_length() - 1]] |= bits[name]
            mask ^= bit
    return bits, partners, direct


def planned_stars(
    champion_map: dict[str, dict], level: int, requested: dict[str, int] | None = None,
    sona_band: str = "",
) -> dict[str, int]:
    """Plan upgrades, never ownership; explicit ranks override late-game defaults."""
    _integer(level, "level", 1)
    if requested is None:
        requested = {}
    if not isinstance(requested, dict):
        raise ValueError("stars must be a champion-to-star-level mapping.")
    for name, rank in requested.items():
        if not isinstance(name, str) or name not in champion_map:
            raise ValueError("stars contains an unknown champion.")
        _integer(rank, f"{name} stars", 1)
        if rank > 4:
            raise ValueError("Star levels must be between 1 and 4.")
    reroll_frontline = any(
        rank >= 3 and 1 <= champion_map[name]["cost"] <= 3 and is_tank(champion_map[name])
        for name, rank in requested.items()
    )
    return {
        name: requested.get(name, 2 if level >= 10 and (
            "K/DA" in champion.get("traits", ())
            or (name == "Sona" and sona_band == "K/DA")
            or (not reroll_frontline and champion["cost"] == 4 and is_tank(champion))
        ) else 1)
        for name, champion in champion_map.items()
    }


def _frontline_anchor_value(champion: dict, stars: int, level: int) -> float:
    """Value only sourced, upgraded late-game tanks as main-anchor candidates."""
    if level < 10 or not is_tank(champion):
        return 0.0
    cost = champion["cost"]
    if 1 <= cost <= 3 and stars >= 3:
        return 450.0 + 150.0 * (stars - 3)
    if cost >= 4 and stars >= 2:
        return 350.0 + 25.0 * (cost - 4) + 150.0 * (stars - 2)
    return 0.0


def _trait_context_adjustment(
    name: str, tier: int, count: int, mode: str, level: int,
    carry_ready: bool = False, hype_ready: bool = False,
) -> float:
    """Discount unsupported splash traits without changing their actual tiers."""
    if mode == "synergies":
        return 0.0
    discounted = (
        (name == "Gu Feng" and count < 4)
        or (name == "True Damage" and count < 5 and not carry_ready)
        or (name == "Hyperpop" and count < 4 and level >= 10 and not hype_ready)
    )
    base = _trait_value(tier, mode)
    return base * 0.35 - base if discounted else 0.0


def _strategy_masks(
    trait_map: dict[str, dict], champion_map: dict[str, dict], sona_band: str,
    stars: dict[str, int], bits: dict[str, int],
) -> tuple[int, int]:
    """Eligible carries are catalogued once, then must be present in fielded bits."""
    td_ready = hype_ready = 0
    for name, champion in champion_map.items():
        rank = stars.get(name, 1)
        traits = champion["traits"]
        td_member = "True Damage" in traits or (name == "Sona" and sona_band == "True Damage")
        hype_member = "Hyperpop" in traits or (name == "Sona" and sona_band == "Hyperpop")
        if "True Damage" in trait_map and td_member and name != "Senna" and (
            (name == "Qiyana" and rank >= 2)
            or (name == "Ekko" and rank >= 3)
            or (champion["cost"] >= 4 and rank >= 2)
        ):
            td_ready |= bits[name]
        if "Hyperpop" in trait_map and hype_member and rank >= 2:
            hype_ready |= bits[name]
    return td_ready, hype_ready


def _late_carry_value(flags: int, level: int, mode: str) -> float:
    return 300.0 if level >= 10 and mode in ("balanced", "high_value") and has_late_carry(flags) else 0.0


def _unit_strategy_value(champion: dict, stars: int, level: int, mode: str) -> float:
    """Current-patch preferences and fast-10 path costs, not gold or winrates."""
    if mode == "synergies":
        return 0.0
    value = -350.0 if champion["name"] == "Senna" else 0.0
    if level >= 10:
        if champion["cost"] <= 3 and stars == 3:
            value -= 100.0
        if champion["name"] == "Qiyana" and stars == 2:
            value -= 60.0
    return value


@dataclass(frozen=True, slots=True)
class _State:
    picks: tuple[int, ...]
    counts: tuple[int, ...]
    active: int
    tiers: int
    unique: int
    trait_value: float
    progress: float
    frontline: int
    cost: int
    collab: int
    mask: int
    exclusive: int
    fielded: int
    bridge: bool
    jazz_tier: int
    anchor: float
    unit_value: float
    carry_ready: bool
    hype_ready: bool
    carry_flags: int


def _evaluate_team(
    names: list[str], trait_map: dict[str, dict], champion_map: dict[str, dict],
    collab: list[str], mode: str, sona_band: str, stars: dict[str, int],
    level: int | None = None,
) -> dict:
    """Evaluate a legal board with prevalidated inputs; shared with buildup."""
    if level is None:
        level = sum(champion_map[name].get("slots", 1) for name in names)
    requested = stars if stars is not None else {}
    stars = planned_stars(champion_map, level, requested, sona_band)
    board_map = {name: champion_map[name] for name in names}
    bits, partners, direct = _jazz_links(trait_map, board_map, collab, sona_band)
    td_mask, hype_mask = _strategy_masks(trait_map, board_map, sona_band, stars, bits)
    fielded = 0
    bridge = False
    for name in names:
        bridge = bridge or name in direct or bool(partners[name] & fielded)
        fielded |= bits[name]
    carry_ready, hype_ready = bool(fielded & td_mask), bool(fielded & hype_mask)
    notes = []
    anchor_value = unit_value = 0.0
    anchor_name = None
    counts: dict[str, int] = {}
    dynamic_collab = set(collab) | {"Sona"}
    frontline = 0
    cost = 0
    board_carry_flags = 0
    for name in names:
        champion = champion_map[name]
        traits = set(champion["traits"]) - {"Collab"}
        if name in dynamic_collab:
            traits.add("Collab")
        if name == "Sona" and sona_band:
            traits.add(sona_band)
        frontline += int(is_tank(champion) or bool(traits & FRONTLINE_TRAITS))
        cost += champion["cost"]
        rank = stars[name]
        board_carry_flags |= carry_flags(champion, rank)
        if level >= 10 and rank == 2 and champion["cost"] == 4 and is_tank(champion) and name not in requested:
            notes.append(f"Automatic tank upgrade target: {name} {star_text(rank)} (planned, not owned).")
        if mode in ("balanced", "high_value"):
            merit = _frontline_anchor_value(champion, rank, level)
            if merit > anchor_value or (merit > 0 and merit == anchor_value and name < anchor_name):
                anchor_value, anchor_name = merit, name
            unit_value += _unit_strategy_value(champion, rank, level, mode)
            if name == "Senna":
                notes.append("Senna receives a current-patch preference penalty, not a measured winrate estimate.")
        if champion["cost"] <= 3 and rank == 3:
            tax = "; fast-10 reroll-path penalty applies" if level >= 10 and mode != "synergies" else ""
            notes.append(f"Planned reroll target: {name} {star_text(rank)}{tax}; not assumed owned.")
        if name == "Qiyana" and rank == 2 and level >= 10 and mode != "synergies":
            notes.append("Qiyana ★★ is a planned target with a fast-10 path penalty, not an owned upgrade.")
        for trait in traits:
            increment = stars.get(name, 1) if trait_map[trait].get("count_mode") == "stars" else 1
            counts[trait] = counts.get(trait, 0) + increment
    rows = []
    active = tiers = unique = jazz_tier = 0
    trait_value = 0.0
    for name, count in sorted(counts.items()):
        trait = trait_map[name]
        points = trait["breakpoints"]
        tier = bisect_right(points, count)
        if trait["unique"]:
            unique += int(tier > 0)
        else:
            active += int(tier > 0)
            tiers += tier
            trait_value += _trait_value(tier, mode)
            adjustment = _trait_context_adjustment(
                name, tier, count, mode, level, carry_ready, hype_ready,
            )
            trait_value += adjustment
            if adjustment < 0:
                requirement = {
                    "Gu Feng": "four members",
                    "True Damage": "five members or an upgraded non-Senna carry",
                    "Hyperpop": "four members or an upgraded Hyperpop member",
                }[name]
                notes.append(f"{name} splash value is discounted without {requirement}.")
        if name == "Jazz":
            jazz_tier = tier
        rows.append({
            "name": name, "count": count,
            "tier": points[tier - 1] if tier else 0,
            "next": points[tier] if tier < len(points) else None,
            "unique": trait["unique"],
        })
    score = trait_value + unique + unit_value + anchor_value
    score += _jazz_bonus(active, jazz_tier, bridge, mode)
    score += _late_carry_value(board_carry_flags, level, mode)
    if mode in ("balanced", "high_value"):
        score += 30.0 * min(frontline, 2)
        score += 20.0 * int(len(names) > frontline)
    if mode == "high_value":
        score += 30.0 * cost
    return {
        "champions": sorted(names), "traits": rows, "active": active, "tiers": tiers,
        "frontline": frontline, "cost": cost, "score": score,
        "planned_stars": {name: stars[name] for name in sorted(names)},
        "frontline_anchor": anchor_name, "strategy_notes": notes,
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
    Star-counting traits use planned ranks: explicit 1..4 overrides, otherwise one,
    except sourced four-cost Tanks target two stars at level ten and above.
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
    requested_stars = stars if stars is not None else {}
    stars = planned_stars(champion_map, size, requested_stars, sona_band)

    trait_names = sorted(trait_map)
    trait_index = {name: index for index, name in enumerate(trait_names)}
    breakpoints = [trait_map[name]["breakpoints"] for name in trait_names]
    is_unique = [trait_map[name]["unique"] for name in trait_names]
    jazz_index = trait_index.get("Jazz", -1)
    jazz_bits, jazz_partners, jazz_direct = _jazz_links(
        trait_map, champion_map, collab, sona_band,
    )
    td_mask, hype_mask = _strategy_masks(trait_map, champion_map, sona_band, stars, jazz_bits)
    td_index = trait_index.get("True Damage", -1)
    hype_index = trait_index.get("Hyperpop", -1)
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

    def unit(name: str) -> tuple[
        tuple[tuple[int, int], ...], int, int, int, int, int, int, int, bool, float, float, int,
    ]:
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
            int(is_tank(champion) or bool(names & FRONTLINE_TRAITS)), champion["cost"],
            int(name in dynamic_collab),
            group_bits.get(champion.get("exclusive_group"), 0),
            jazz_bits[name], jazz_partners[name], name in jazz_direct,
            _frontline_anchor_value(champion, stars[name], size),
            _unit_strategy_value(champion, stars[name], size, mode),
            carry_flags(champion, stars[name]),
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
    ready_lifts = {}
    for trait_name, points in zip(trait_names, breakpoints):
        table = []
        for count in range(max_count + 1):
            tier = bisect_right(points, count)
            next_point = points[tier] if tier < len(points) else None
            previous = points[tier - 1] if tier else 0
            progress = (count - previous) / (next_point - previous) if next_point else 0.0
            value = _trait_value(tier, mode)
            value += _trait_context_adjustment(trait_name, tier, count, mode, size)
            table.append((tier, progress, value))
        values.append(table)
        if trait_name in ("True Damage", "Hyperpop"):
            ready_lifts[trait_index[trait_name]] = [
                0.0 if is_unique[trait_index[trait_name]] else _trait_value(tier, mode) - value
                for tier, _, value in table
            ]

    empty = _State(
        picks=(), counts=(0,) * len(trait_names), active=0, tiers=0, unique=0,
        trait_value=0.0, progress=0.0, frontline=0, cost=0, collab=0, mask=0,
        exclusive=0, fielded=0, bridge=False, jazz_tier=0,
        anchor=0.0, unit_value=0.0, carry_ready=False, hype_ready=False, carry_flags=0,
    )

    def extend(state: _State, item: tuple, index: int | None) -> _State:
        (contributions, _, frontline, cost, collab_count, exclusive, bit, partners, direct,
         anchor, unit_value, flags) = item
        counts = list(state.counts)
        active, tiers, unique, progress = state.active, state.tiers, state.unique, state.progress
        trait_value, jazz_tier = state.trait_value, state.jazz_tier
        fielded = state.fielded | bit
        carry_ready, hype_ready = bool(fielded & td_mask), bool(fielded & hype_mask)
        for trait, increment in contributions:
            old_tier, old_progress, old_value = values[trait][counts[trait]]
            old_count = counts[trait]
            counts[trait] += increment
            new_tier, new_progress, new_value = values[trait][counts[trait]]
            if (trait == td_index and state.carry_ready) or (trait == hype_index and state.hype_ready):
                old_value += ready_lifts[trait][old_count]
                new_value += ready_lifts[trait][counts[trait]]
            if is_unique[trait]:
                unique += int(new_tier > 0) - int(old_tier > 0)
            else:
                active += int(new_tier > 0) - int(old_tier > 0)
                tiers += new_tier - old_tier
                progress += new_progress - old_progress
                trait_value += new_value - old_value
            if trait == jazz_index:
                jazz_tier = new_tier
        if carry_ready and not state.carry_ready and td_index >= 0:
            trait_value += ready_lifts[td_index][counts[td_index]]
        if hype_ready and not state.hype_ready and hype_index >= 0:
            trait_value += ready_lifts[hype_index][counts[hype_index]]
        return _State(
            state.picks + (index,) if index is not None else state.picks,
            tuple(counts), active, tiers, unique, trait_value, progress,
            state.frontline + frontline, state.cost + cost,
            state.collab + collab_count,
            state.mask | (1 << index) if index is not None else state.mask,
            state.exclusive | exclusive,
            fielded,
            state.bridge or direct or bool(state.fielded & partners), jazz_tier,
            max(state.anchor, anchor), state.unit_value + unit_value, carry_ready, hype_ready,
            state.carry_flags | flags,
        )

    initial = empty
    for item in fixed:
        initial = extend(initial, item, None)

    def score(state: _State) -> float:
        value = state.trait_value + state.unique + state.unit_value
        value += _jazz_bonus(state.active, state.jazz_tier, state.bridge, mode)
        value += _late_carry_value(state.carry_flags, size, mode)
        if mode in ("balanced", "high_value"):
            value += state.anchor
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
                       collab, mode, sona_band, requested_stars, size)
        for state in chosen
    ]
