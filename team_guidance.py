"""Role-based display guidance, not item ownership or combat simulation.

All ranks are planned targets. Holder categories are alternatives for the items
actually available; a Flex holder does not imply separate AD and AP item sets.
"""
from functools import lru_cache
from itertools import combinations

from item_builds import annotate_item_builds, holder_match, item_archetype

_DAMAGE_ROLES = {"assassin", "caster", "fighter", "marksman", "specialist"}
_KINDS = ("tank", "ad", "ap", "flex")


def item_profile(champion):
    """Use combat-role metadata only, with the explicit user policy for Yone."""
    if champion.get("name") == "Yone":
        return "flex"
    words = (champion.get("role") or "").casefold().split()
    if "tank" in words:
        return "tank"
    if len(words) == 2 and words[1] in _DAMAGE_ROLES:
        return {"attack": "ad", "magic": "ap"}.get(words[0], "unknown")
    return "unknown"


def carry_flags(champion, stars=1):
    """Late-carry, cheap-damage and fielded reroll-target bits."""
    damage = item_profile(champion) in ("ad", "ap", "flex")
    cost = champion.get("cost", 1)
    return (int(damage and (cost >= 3 or stars >= 3))
            | (int(damage and cost <= 2) << 1)
            | (int(cost <= 3 and stars >= 3) << 2))


def has_late_carry(flags):
    return bool(flags & 1 or flags & 6 == 6)


def planned_unit_strength(champion, stars=1):
    """Existing rarity/upgrade proxy, not measured combat strength."""
    strength = max(1, champion.get("cost", 1)) * 3 ** (stars - 1)
    if "specialist" in (champion.get("role") or "").casefold().split():
        strength *= 0.6
    return strength


def _compatible(left, right):
    if "unknown" in (left, right):
        return False
    return left == right or (left == "flex" and right in ("ad", "ap")) or (
        right == "flex" and left in ("ad", "ap"))


def _board_level(board, champions):
    return board.get("level", sum(champions.get(name, {}).get("slots", 1)
                                  for name in board.get("champions", [])))


def _board_identity(board):
    stars = board.get("planned_stars", {})
    return {name: stars.get(name, 1) for name in board.get("champions", [])}


def _holders(board, champions, previous=None, level=None, final_board=False,
             targets=None, target_stars=None, archetypes=None, store=True):
    previous = previous or {}
    holders = dict.fromkeys(_KINDS)
    stars = board.get("planned_stars", {})
    flags = {name: carry_flags(champions.get(name, {}), stars.get(name, 1))
             for name in board["champions"] if name in champions}
    board_flags = 0
    for value in flags.values():
        board_flags |= value
    level = _board_level(board, champions) if level is None else level
    prefer_late = level >= 10 and not board_flags & 4 and has_late_carry(board_flags)
    for kind in _KINDS:
        target = targets.get(kind) if targets else None
        candidates = [name for name in board["champions"]
                      if (item_profile(champions.get(name, {})) == kind
                          or (kind == "flex" and target
                              and _compatible(item_profile(champions.get(name, {})), kind)))
                      and not (prefer_late and final_board and kind != "tank"
                               and not flags[name] & 1)]

        def rank(name):
            champion = champions[name]
            # A three-star cheap carry can beat a one-star premium. Specialist
            # rarity alone is not evidence that it is the better item carry.
            strength = planned_unit_strength(champion, stars.get(name, 1))
            eligible = prefer_late and kind != "tank" and bool(flags[name] & 1)
            preference = 0
            if target and kind != "tank":
                match = holder_match(name, target, stars.get(name, 1),
                                     target_stars.get(target, 1), archetypes)
                preference = {'final': 4, 'same_archetype': 3,
                              'related_melee': 2, 'damage_fallback': 1}[match]
            return (not eligible, -preference, -strength, name != previous.get(kind),
                    not (kind == "tank" and name == board.get("frontline_anchor")), name)

        if candidates:
            holders[kind] = min(candidates, key=rank)
    if store:
        board["item_holders"] = holders
    return holders


def replacement_priority(outgoing, incoming, champions):
    """Rank candidate actual exchanges by compatible profile, then exact role."""
    champions = champions if isinstance(champions, dict) else {row["name"]: row for row in champions}
    score, _ = _replacement_plan({"champions": outgoing}, {"champions": incoming}, champions)
    # A roster-wide bound keeps one additional profile match more important
    # than every possible exact-label tie-break, even for weighted units.
    scale = 1 + sum(champion.get("slots", 1) for champion in champions.values())
    return score[0] * scale + score[1]


def _replacement_plan(previous, current, champions):
    """Globally match actual changes; unused incoming units are level growth.

    Elementary exchanges are singles or one-to-many weighted swaps. A weighted
    incoming unit may also use the new level's slot. Matching maximizes role
    compatibility, then exact labels, then individual swaps; names break ties.
    """
    outgoing = sorted(set(previous["champions"]) - set(current["champions"]))
    incoming = sorted(set(current["champions"]) - set(previous["champions"]))
    if not outgoing:
        return (0, 0), []
    slots = lambda name: champions.get(name, {}).get("slots", 1)
    profile = lambda name: item_profile(champions.get(name, {}))
    role = lambda name: (champions.get(name, {}).get("role") or "").casefold().split()
    growth = max(0, sum(map(slots, incoming)) - sum(map(slots, outgoing)))
    candidates = {}

    def candidate(old, new):
        old_names = tuple(outgoing[i] for i in old)
        new_names = tuple(incoming[i] for i in new)
        old_mask = sum(1 << i for i in old)
        new_mask = sum(1 << i for i in new)
        budget = sum(slots(name) for name in old_names)
        # Every elementary group has a singleton side, allowing weighted role
        # comparison without inventing duplicate copies of its two-slot unit.
        if len(old_names) == 1:
            pairs = [(old_names[0], name, slots(name)) for name in new_names]
        else:
            pairs = [(name, new_names[0], slots(name)) for name in old_names]
        compatible = min(budget, sum(weight for a, b, weight in pairs
                                     if _compatible(profile(a), profile(b))))
        exact = min(budget, sum(weight for a, b, weight in pairs
                               if _compatible(profile(a), profile(b)) and role(a) == role(b)))
        same_role = all(_compatible(profile(a), profile(b)) for a, b, _ in pairs)
        if len(old_names) == len(new_names) == 1 and slots(old_names[0]) == slots(new_names[0]):
            left, right = profile(old_names[0]), profile(new_names[0])
            matched = right if left == "flex" else left
            label = matched if same_role else "roster"
        else:
            label = "slot_exchange"
        record = {"outgoing": list(old_names), "incoming": list(new_names),
                  "same_role": same_role, "profile": label}
        excess = sum(slots(name) for name in new_names) - budget
        candidates[old_mask, new_mask] = (
            old_mask, new_mask, (compatible, exact, -excess, int(len(old) == len(new) == 1)),
            (old_names, new_names), record)

    def subsets(names, minimum, maximum):
        # Slots are positive, so no feasible subset can exceed maximum members.
        for count in range(1, min(len(names), maximum) + 1):
            for indices in combinations(range(len(names)), count):
                if minimum <= sum(slots(names[i]) for i in indices) <= maximum:
                    yield indices

    for i, name in enumerate(outgoing):
        for group in subsets(incoming, slots(name), slots(name) + growth):
            candidate((i,), group)
    for i, name in enumerate(incoming):
        for group in subsets(outgoing, max(1, slots(name) - growth), slots(name)):
            candidate(group, (i,))
    by_outgoing = [[] for _ in outgoing]
    for exchange in candidates.values():
        for i in range(len(outgoing)):
            if exchange[0] & (1 << i):
                by_outgoing[i].append(exchange)

    @lru_cache(None)
    def match(old_mask, new_mask):
        if not old_mask:
            return (0, 0, 0, 0), (), ()
        first = (old_mask & -old_mask).bit_length() - 1
        best = None
        for old, new, score, signature, record in by_outgoing[first]:
            if old & old_mask != old or new & new_mask != new:
                continue
            tail = match(old_mask ^ old, new_mask ^ new)
            if tail is None:
                continue
            total = tuple(a + b for a, b in zip(score, tail[0]))
            signatures = tuple(sorted((signature,) + tail[1]))
            choice = total, signatures, (record,) + tail[2]
            if best is None or total > best[0] or (total == best[0] and signatures < best[1]):
                best = choice
        return best

    matched = match((1 << len(outgoing)) - 1, (1 << len(incoming)) - 1)
    if matched is not None:
        return matched[0][:2], sorted(matched[2], key=lambda row: (row["outgoing"], row["incoming"]))
    # Unusual weights can require a larger indivisible exchange. Keep that
    # actual exchange honest rather than inventing a filler or duplicate unit.
    budget = sum(map(slots, outgoing))
    groups = [group for count in range(1, len(incoming) + 1)
              for group in combinations(incoming, count)
              if sum(map(slots, group)) >= budget]
    additions = min(groups, key=lambda group: (sum(map(slots, group)), len(group), group)) if groups else tuple(incoming)
    return (0, 0), [{"outgoing": outgoing, "incoming": list(additions), "same_role": False,
                    "profile": "slot_exchange" if any(slots(name) != 1 for name in outgoing + list(additions)) else "roster"}]


def _transfers(previous, current, champions):
    old = previous["item_holders"]
    new = current["item_holders"]
    fielded = set(current["champions"])
    transfers = []
    for kind in ("tank", "ad", "ap"):
        destination = new[kind] or (new["flex"] if kind != "tank" else None)
        sources = [old[kind]] if old[kind] else []
        if kind != "tank" and old["flex"] and (
                old["flex"] != new["flex"] or old[kind] != new[kind]):
            sources.append(old["flex"])
        for source in dict.fromkeys(sources):
            if (not destination or source == destination
                    or not _compatible(item_profile(champions[source]), kind)
                    or not _compatible(item_profile(champions[destination]), kind)):
                continue
            retained = source in fielded
            method = "remover_or_later_sale" if retained else "sell_outgoing"
            condition = f"Only if {source} actually holds {kind.upper()} items and {destination} is your chosen holder. "
            if source == old["flex"] or destination == new["flex"]:
                condition += "Yone/Flex AD and AP paths are alternatives depending on the actual build, not two assumed item sets. "
            if retained:
                condition += f"{source} remains fielded: use an item remover or wait for a later sale; items do not move freely."
            else:
                condition += f"Secure {destination} first, then sell outgoing {source} to release those items."
            transfers.append({"kind": kind, "outgoing": source, "incoming": destination,
                              "method": method, "condition": condition})
    return transfers


def annotate_result(result, champions):
    """Annotate newly computed result dictionaries without changing source data.

    Primary and fallback continuity are separate. Each upgrade starts from the
    previous phase, never from the distant end goal or another branch.
    """
    champions = champions if isinstance(champions, dict) else {row["name"]: row for row in champions}
    steps = result.get("buildup", [])
    root_level = result.get("level")
    if root_level is None:
        root_level = steps[-1].get("level") if steps else None
    if root_level is None:
        root_level = _board_level(result, champions)
    root_target = _board_identity(result)
    # Read-only base-ranking chronology freezes final targets before routing.
    base_previous = None
    for step in steps:
        level = step.get("level", _board_level(step.get("board") or {}, champions))
        goal = step.get("goal_board")
        local_target = _board_identity(goal) if goal is not None else root_target
        board = step.get("board")
        base_previous = (_holders(board, champions, base_previous, level,
                                 _board_identity(board) == local_target, store=False)
                         if board is not None else None)
        phase_holders = base_previous
        for phase in step.get("upgrades", []):
            board = phase.get("board")
            phase_holders = (_holders(board, champions, phase_holders, level,
                                      _board_identity(board) == local_target, store=False)
                             if board is not None else None)
        base_previous = phase_holders
    targets = _holders(result, champions, base_previous, root_level, True, store=False)
    archetypes = {name: item_archetype(champion) for name, champion in champions.items()}

    def route(board, previous=None, level=None, final_board=False):
        if _board_identity(board) == root_target:
            board["item_holders"] = dict(targets)
            return board["item_holders"]
        return _holders(board, champions, previous, level, False,
                        targets, root_target, archetypes)

    previous = fallback_previous = None
    for step in result.get("buildup", []):
        board = step.get("board")
        level = step.get("level")
        if level is None:
            level = _board_level(board or {}, champions)
        goal = step.get("goal_board")
        target = _board_identity(goal) if goal is not None else root_target
        step["replacements"] = []
        step["item_transfers"] = []
        if board is not None:
            route(board, previous["item_holders"] if previous else None,
                  level, _board_identity(board) == target)
            if previous is not None:
                step["replacements"] = _replacement_plan(previous, board, champions)[1]
                step["item_transfers"] = _transfers(previous, board, champions)
        fallback = step.get("fallback")
        if fallback is not None:
            route(fallback, fallback_previous["item_holders"] if fallback_previous else None,
                  level, _board_identity(fallback) == target)
        for preparation in step.get("preparation_boards", []):
            if preparation.get("board") is not None:
                route(preparation["board"], level=level)
        phase_previous = board
        for phase in step.get("upgrades", []):
            phase_board = phase.get("board")
            phase["replacements"] = []
            phase["item_transfers"] = []
            if phase_board is not None:
                route(phase_board, phase_previous["item_holders"] if phase_previous else None,
                      level, _board_identity(phase_board) == target)
                if phase_previous is not None:
                    phase["replacements"] = _replacement_plan(phase_previous, phase_board, champions)[1]
                    phase["item_transfers"] = _transfers(phase_previous, phase_board, champions)
            phase_previous = phase_board
        if goal is not None:
            route(goal, phase_previous["item_holders"] if phase_previous else None,
                  level, True)
        previous, fallback_previous = phase_previous, fallback
    result["item_holders"] = dict(targets)
    annotate_item_builds(result, champions, archetypes)
