"""Estimated role/range positioning, not combat simulation or win-rate data.

Rows run from the enemy-facing frontline (0) to the backline (3). Odd rows
are offset half a hex right, so row 1 column c touches row 0 columns c/c+1.
Team-slot costs do not change a champion's single displayed board marker.
"""
from math import isfinite

from team_guidance import item_profile, planned_unit_strength


_FRONT_COLUMNS = (4, 3, 2, 5, 1, 6, 0)
_SIDE_COLUMNS = (5, 6, 0, 4, 1, 3, 2)
_RIGHT_COLUMNS = (6, 5, 4, 3, 2, 1, 0)
_CAPACITY = 28
_ISOLATED_SOLO_TANKS = frozenset({"Amumu"})


def _attack_range(champion):
    value = champion.get("attack_range")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if isfinite(value) and value > 0 else None


def _name_key(name):
    return name.casefold(), name


def _boards(result):
    yield result
    for step in result.get("buildup", []):
        for key in ("board", "fallback", "goal_board"):
            if step.get(key) is not None:
                yield step[key]
        for phase in step.get("upgrades", []):
            if phase.get("board") is not None:
                yield phase["board"]
        for preparation in step.get("preparation_boards", []):
            if preparation.get("board") is not None:
                yield preparation["board"]


def _annotate_board(board, champions):
    names = sorted(set(board.get("champions", [])), key=_name_key)
    metadata = {name: champions.get(name, {"name": name}) for name in names}
    profiles = {name: item_profile(metadata[name]) for name in names}
    main_tank = (board.get("item_holders") or {}).get("tank")
    tanks, close, ranged = [], [], []
    for name in names:
        if name == "Teemo":
            continue
        champion = metadata[name]
        role = (champion.get("role") or "").casefold().split()
        attack_range = _attack_range(champion)
        if profiles[name] == "tank":
            tanks.append(name)
        elif (attack_range is not None and attack_range <= 2) or any(
                word in role for word in ("melee", "fighter", "assassin")):
            close.append(name)
        else:
            ranged.append(name)
    tanks.sort(key=lambda name: (name != main_tank, _name_key(name)))
    stars = board.get("planned_stars", {})
    strength = lambda name: planned_unit_strength(metadata[name], stars.get(name, 1))
    if tanks and main_tank not in tanks:
        main_tank = min(tanks, key=lambda name: (-strength(name), _name_key(name)))
        tanks.sort(key=lambda name: (name != main_tank, _name_key(name)))
    isolated = len(tanks) == 1 and main_tank in _ISOLATED_SOLO_TANKS
    reserved = {(0, 2), (0, 4), (1, 2), (1, 3)} if isolated else set()
    occupied = {}
    markers = []

    def place(name, candidates, explanation):
        cell = next((cell for cell in candidates if cell not in occupied and cell not in reserved), None)
        if cell is None:
            raise ValueError("Not enough free hexes to preserve the isolated solo tank's spacing.")
        row, column = cell
        champion = metadata[name]
        attack_range = _attack_range(champion)
        details = ["Estimated placement", explanation(row, column)]
        role = champion.get("role")
        details.append("Role: " + role if role else "Combat role unavailable")
        if attack_range is None:
            details.append("Attack range unavailable; role-only/conservative fallback, not a sourced range")
        else:
            details.append(f"Attack range: {attack_range:g} hexes")
            source = champion.get("attack_range_source", "")
            if not isinstance(source, str) or not source.startswith("https://"):
                details.append("Attack-range source unavailable")
        if profiles[name] == "flex":
            details.append("Yone AD/AP flex does not change short-range protection")
        slots = champion.get("slots", 1)
        if slots != 1:
            details.append(f"Uses {slots} team slots; shown once, not as extra champions")
        occupied[row, column] = name
        markers.append({"champion": name, "row": row, "column": column,
                        "reason": "; ".join(details) + "."})

    if "Teemo" in metadata:
        place("Teemo", [(3, 3)], lambda row, column:
              "Teemo large-area spell exception: centered on the back row")

    tank_cells = [(row, column) for row in range(4) for column in _FRONT_COLUMNS]

    def tank_reason(name, row, column):
        if row:
            return "Front row full; tank overflow behind the frontline"
        priority = "Main item-holder tank" if name == main_tank else "Tank"
        if name == main_tank and isolated:
            return "Isolated solo tank: front center with adjacent friendly hexes kept empty"
        if len(tanks) == 1:
            return "Sole main tank: one hex off center toward the right-side carries"
        if column in (0, 6):
            return f"{priority}: interior frontline filled, connected extension to an edge"
        return f"{priority}: compact front-middle formation, adjacent to other tanks"

    for name in tanks:
        candidates = [(0, 3)] + tank_cells if isolated and name == main_tank else tank_cells
        place(name, candidates, lambda row, column, name=name: tank_reason(name, row, column))

    front_tanks = {column for (row, column), name in occupied.items()
                   if row == 0 and profiles[name] == "tank"}

    def protected(column):
        return bool(front_tanks.intersection((column, column + 1)))

    protected_columns = sorted(_SIDE_COLUMNS, key=lambda column: (
        not protected(column), _SIDE_COLUMNS.index(column)))
    close_cells = ([(1, column) for column in protected_columns] +
                   [(row, column) for row in (2, 3, 0) for column in _SIDE_COLUMNS])

    def close_reason(row, column):
        if row == 1:
            if protected(column):
                return "Melee/short-range unit: second-row side placement directly behind an adjacent frontline tank"
            return "Melee/short-range unit: second-row side preference; no adjacent frontline tank available"
        if row == 0:
            return "Rear rows full; unavoidable frontline overflow for this crowded board"
        return "Second row full; melee/short-range overflow behind the frontline"

    for name in close:
        place(name, close_cells, close_reason)

    ranged_cells = [(row, column) for row in (3, 2, 1, 0) for column in _RIGHT_COLUMNS]

    def ranged_reason(row, column):
        if row == 3:
            return "Ranged or range-unknown unit: backline clustered from the right"
        if row == 0:
            return "Rear rows full; unavoidable frontline overflow for this crowded board"
        return "Back row full; right-side backline overflow behind the frontline"

    for name in ranged:
        place(name, ranged_cells, ranged_reason)

    if board.get("positioning_constraint") == "one_front_two_rows":
        # A separate Vladimir quest setup, not the ordinary combat formation.
        anchor = main_tank or (names[0] if names else None)
        if anchor:
            rear = [(row, column) for row in (3, 2) for column in _RIGHT_COLUMNS]
            occupied.clear()
            markers.clear()
            place(anchor, [(0, 4)], lambda row, column:
                  "Vladimir unlock setup: the only unit in the front two rows")
            for name in names:
                if name != anchor:
                    place(name, rear, lambda row, column:
                          "Vladimir unlock setup: keep every other unit in the back two rows")
            if sum(marker["row"] < 2 for marker in markers) != 1:
                raise ValueError("Vladimir preparation requires exactly one unit in the front two rows.")
    markers.sort(key=lambda marker: (
        marker["row"], profiles[marker["champion"]] != "tank",
        marker["champion"] != main_tank, -marker["column"],
        _name_key(marker["champion"])))
    board["positioning"] = markers
    board["lineup_order"] = [marker["champion"] for marker in markers]


def annotate_positioning(result, champions):
    """Annotate a result and all detached buildup boards without changing rosters.

    ``champions`` accepts the source roster list or its name-keyed mapping. Only
    computed board dictionaries are changed; metadata and export roster order
    remain untouched. More than 28 distinct fielded units cannot fit on a TFT
    half-board and raises ValueError before any board is annotated.
    """
    champions = champions if isinstance(champions, dict) else {
        champion["name"]: champion for champion in champions}
    boards = list(_boards(result))
    if any(len(set(board.get("champions", []))) > _CAPACITY for board in boards):
        raise ValueError("Positioning supports at most 28 distinct champions on the 4x7 half-board.")
    for board in boards:
        _annotate_board(board, champions)
