"""TFT's version-02 roster codes: ten 12-bit IDs, not JSON or board state.

The bundled Set 19 IDs are sourced from Blitz's actual in-game-code builder.
Its colliding or conflicting IDs are deliberately unsupported, not guessed. Source URLs,
limitations, and conflicting secondary-source observations live in the mapping.
Codes preserve champion identity/order only: no stars, items, positions, Collab
selection, or unlock progress. Baron consumes one encoded entry even though he
uses two in-game team slots.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys


VERSION = "02"
NATIVE_SLOTS = 10
ID_HEX_DIGITS = 3


class TeamCodeError(ValueError):
    """A roster, native code, or mapping cannot be represented safely."""


def _validate_mapping(mapping: dict) -> dict:
    if not isinstance(mapping, dict):
        raise TeamCodeError("Team Planner mapping must be a mapping document.")
    if mapping.get("format_version") != VERSION:
        raise TeamCodeError("Only native Team Planner format version 02 is supported.")
    if mapping.get("slots") != NATIVE_SLOTS or mapping.get("id_hex_digits") != ID_HEX_DIGITS:
        raise TeamCodeError("Version 02 requires ten slots of three hexadecimal digits.")
    set_id = mapping.get("set_id")
    if not isinstance(set_id, str) or not re.fullmatch(r"TFTSet[1-9]\d*(?:_[A-Za-z0-9]+)*", set_id):
        raise TeamCodeError("Missing or invalid native TFT set identifier.")
    champions = mapping.get("champions")
    if not isinstance(champions, dict) or not champions:
        raise TeamCodeError("Team Planner mapping has no champion IDs.")
    seen = {}
    for name, code in champions.items():
        if not isinstance(name, str) or not name:
            raise TeamCodeError("Champion names in the mapping must be nonempty strings.")
        if type(code) is not int or not 1 <= code <= 0xFFF:
            raise TeamCodeError(f"Invalid native ID for {name}: expected 1..4095 (000 is padding).")
        if code in seen:
            raise TeamCodeError(f"Ambiguous native ID {code:03x}: {seen[code]} and {name}.")
        seen[code] = name
    unsupported = mapping.get("unsupported", {})
    if not isinstance(unsupported, dict):
        raise TeamCodeError("Unsupported champion metadata must be a dictionary.")
    for name, detail in unsupported.items():
        if not isinstance(name, str) or not isinstance(detail, dict) or not detail.get("reason"):
            raise TeamCodeError("Unsupported champion metadata needs a name and reason.")
        if name in champions:
            raise TeamCodeError(f"{name} cannot have both a supported ID and an unsupported marker.")
    return mapping


def load_mapping(path: str | Path | None = None) -> dict:
    """Load the bundled mapping (or an explicit offline mapping document).

    PyInstaller extracts data beneath sys._MEIPASS; source runs use this module's
    directory. No network access or fallback to another set is performed.
    """
    if path is None:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        path = root / "data" / "team_planner_ids.json"
    try:
        with Path(path).open(encoding="utf-8") as stream:
            mapping = json.load(stream)
    except (OSError, ValueError) as exc:
        raise TeamCodeError(f"Cannot load native Team Planner IDs: {exc}") from exc
    return _validate_mapping(mapping)


def encode_team(champion_names: list[str], mapping: dict | None = None) -> str:
    """Encode a unit roster without dropping, truncating, or guessing units.

    An explicit mapping uses the same document schema as load_mapping(). Empty
    rosters are represented by ten 000 slots. At most ten distinct champions can
    be encoded; this limit is independent of their in-game board-slot costs.
    """
    if not isinstance(champion_names, list) or any(not isinstance(name, str) for name in champion_names):
        raise TeamCodeError("A native roster must be a list of champion names, not stars/items/board records.")
    if len(champion_names) > NATIVE_SLOTS:
        raise TeamCodeError("Native TFT Team Planner codes support at most 10 champion entries; nothing was truncated.")
    if len(set(champion_names)) != len(champion_names):
        raise TeamCodeError("Native TFT Team Planner rosters cannot contain duplicate champions.")
    mapping = load_mapping() if mapping is None else _validate_mapping(mapping)
    ids = []
    for name in champion_names:
        if name in mapping.get("unsupported", {}):
            reason = mapping["unsupported"][name]["reason"]
            raise TeamCodeError(f"Cannot export {name}: {reason}")
        if name not in mapping["champions"]:
            raise TeamCodeError(f"No verified native Team Planner ID for {name!r} in {mapping['set_id']}.")
        ids.append(mapping["champions"][name])
    payload = "".join(f"{code:03x}" for code in ids) + "000" * (NATIVE_SLOTS - len(ids))
    return VERSION + payload + mapping["set_id"]


def decode_team(code: str, mapping: dict | None = None) -> list[str]:
    """Decode a version-02 roster strictly; blank slots may occur anywhere.

    Unknown IDs, duplicates, different sets/versions, and malformed slot counts
    are errors. Decoding never silently filters units as some web builders do.
    """
    mapping = load_mapping() if mapping is None else _validate_mapping(mapping)
    if not isinstance(code, str):
        raise TeamCodeError("A native Team Planner code must be a string.")
    code = code.strip()
    if not code.startswith(VERSION):
        raise TeamCodeError("Only native Team Planner format version 02 is supported.")
    set_id = mapping["set_id"]
    if not code.endswith(set_id):
        raise TeamCodeError(f"Native code is not for the supported roster {set_id}.")
    payload = code[len(VERSION):-len(set_id)]
    if len(payload) != NATIVE_SLOTS * ID_HEX_DIGITS or not re.fullmatch(r"[0-9a-fA-F]{30}", payload):
        raise TeamCodeError("Native code must contain exactly ten three-digit hexadecimal slots.")
    reverse = {value: name for name, value in mapping["champions"].items()}
    names = []
    seen = set()
    for start in range(0, len(payload), ID_HEX_DIGITS):
        native_id = int(payload[start:start + ID_HEX_DIGITS], 16)
        if native_id == 0:
            continue
        if native_id in seen:
            raise TeamCodeError(f"Duplicate champion ID {native_id:03x} in native roster.")
        if native_id not in reverse:
            ambiguous = [name for name, detail in mapping.get("unsupported", {}).items()
                         if detail.get("source_id") == native_id]
            if ambiguous:
                raise TeamCodeError(f"Ambiguous native ID {native_id:03x}: {', '.join(ambiguous)}.")
            raise TeamCodeError(f"No verified champion for native ID {native_id:03x} in {set_id}.")
        seen.add(native_id)
        names.append(reverse[native_id])
    return names
