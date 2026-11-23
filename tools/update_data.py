"""Refresh the bundled Set 19 pre-release snapshot from the actual source HTML.

Run: python tools/update_data.py --snapshot-date 2026-11-20
Refresh-only dependency: python -m pip install beautifulsoup4
The desktop application itself does not need Beautiful Soup or internet access.

For archived/reproducible input, use --html-dir DIRECTORY containing full UTF-8
traits.html, units.html, collab.html, unlocks.html, unlock_roster.html and
champion_ranges.json responses from the URLs below. unlocks.html may contain
content-negotiated Markdown. Champion ranges come from stats.offense.range in
the IES Set 19 JSON; a full refresh never drops their HTTPS provenance.
HTTPS certificate verification is never disabled. A fetch or parsing failure
leaves the existing snapshot unchanged. Review source changes before publishing.

Source-specific modeling: Collab appears in Ninja's one-native-champion section,
but is NOT a unique trait: each chosen group gains it dynamically. Sona alone has
it permanently. K/DA uses total star levels. Baron Nashor occupies two slots.
The two Akali variants have reciprocal unlock exclusions and share one exclusive
group; this is preserved from the roster's unlock text, not inferred from names.
Repeated numeric thresholds (Pentakill and True Damage) are deduplicated rather
than guessing a missing threshold or treating alternate descriptions as tiers.
Unspecified prismatic thresholds are not invented. Unlock tasks and player levels
come from the newer Mobalytics guide, cross-checked against Blitz's current
unlock roster. Conditional trait effects, items and combat simulation
are outside this data format.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

try:
    from bs4 import BeautifulSoup
except ImportError as exc:
    raise SystemExit("Refresh requires Beautiful Soup: python -m pip install beautifulsoup4") from exc

if __package__:
    from .update_unlocks import BLITZ_URL, GUIDE_URL, apply_unlocks
    from .update_trait_descriptions import description_fields
    from .update_champion_roles import role_fields
    from .update_champion_ranges import SOURCE_URL as RANGE_URL, apply_ranges, load_ranges, write_snapshot
else:
    from update_unlocks import BLITZ_URL, GUIDE_URL, apply_unlocks
    from update_trait_descriptions import description_fields
    from update_champion_roles import role_fields
    from update_champion_ranges import SOURCE_URL as RANGE_URL, apply_ranges, load_ranges, write_snapshot


SOURCES = {
    "traits": "https://tft.ninja/sets/19/traits",
    "units": "https://tft.ninja/sets/19/units",
    "collab": "https://tftflow.com/tables/set19/collab",
    "unlocks": GUIDE_URL,
    "unlock_roster": BLITZ_URL,
    "champion_ranges": RANGE_URL,
}
EXPECTED = {"traits": 47, "champions": 94, "collab_groups": 40}
AKALI_EXCLUSION_NOTE = (
    "Akali (K/DA) requires True Damage Akali not unlocked; Akali (True Damage) "
    "requires K/DA Akali not unlocked. These reciprocal unlock exclusions make "
    "the variants mutually exclusive."
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def text(node, description: str) -> str:
    require(node is not None, f"Missing source element: {description}")
    value = node.get_text(" ", strip=True)
    require(bool(value), f"Empty source element: {description}")
    return value


def fetch_pages(html_dir: Path | None) -> dict[str, str]:
    pages = {}
    for key, url in SOURCES.items():
        suffix = "json" if key == "champion_ranges" else "html"
        if html_dir is not None:
            pages[key] = (html_dir / f"{key}.{suffix}").read_text(encoding="utf-8-sig")
        else:
            request = Request(url, headers={
                "User-Agent": "Set19CollabOptimizer/1.0",
                "Accept": ("application/json" if key == "champion_ranges" else
                           "text/markdown" if key == "unlocks" else "text/html"),
            })
            with urlopen(request, timeout=45) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                pages[key] = response.read().decode(charset)
    return pages


def parse_pages(pages: dict[str, str], snapshot_date: str) -> dict:
    soups = {key: BeautifulSoup(page, "html.parser") for key, page in pages.items()
             if key != "champion_ranges"}
    traits = []
    source_members = {}
    for section in soups["traits"].select("section.set-roster-section"):
        section_name = section.get("aria-labelledby")
        require(section_name in {"traits", "unique-traits"}, "Trait section markup changed")
        for card in section.select("section.set-trait"):
            name = text(card.select_one("h3.set-trait-name a"), "trait name")
            breakpoints = sorted({int(chip.get_text(strip=True)) for chip in card.select(".set-bp-chip")})
            require(bool(breakpoints) and breakpoints[0] > 0, f"Missing breakpoints for {name}")
            trait = {
                "name": name,
                "breakpoints": breakpoints,
                "unique": section_name == "unique-traits" and name != "Collab",
            }
            trait.update(description_fields(card))
            if name == "K/DA":
                trait["count_mode"] = "stars"
            traits.append(trait)
            source_members[name] = {
                link["href"].rstrip("/").rsplit("/", 1)[-1]
                for link in card.select(".set-trait-members a[href]")
            }
            require(bool(source_members[name]), f"Missing champion membership for {name}")

    champions = []
    champion_slugs = {}
    for section in soups["units"].select("section.set-roster-section"):
        match = re.fullmatch(r"cost-(\d+)", section.get("aria-labelledby", ""))
        if not match:
            continue
        cost = int(match.group(1))
        rows = section.select("tbody tr")
        require(bool(rows), f"Empty {cost}-cost roster section")
        for row in rows:
            name = text(row.select_one(".set-roster-name a"), "champion name")
            unlockable = name.startswith("Unlockable:")
            if unlockable:
                name = name.removeprefix("Unlockable:").strip()
            champion = {
                "name": name,
                "cost": cost,
                "traits": [text(link, f"{name} trait") for link in row.select(".set-roster-traits a")],
                "unlockable": unlockable,
            }
            champion.update(role_fields(row))
            if name == "Baron Nashor":
                champion["slots"] = 2
            if name in {"Akali (K/DA)", "Akali (True Damage)"}:
                other = "True Damage Akali" if name == "Akali (K/DA)" else "K/DA Akali"
                require(f"without {other} unlocked" in row.get_text(" ", strip=True),
                        f"Reciprocal Akali exclusion changed for {name}")
                champion["exclusive_group"] = "akali"
            champions.append(champion)
            slug = row.get("id")
            require(bool(slug) and slug not in champion_slugs, f"Duplicate/missing champion slug: {slug}")
            champion_slugs[slug] = champion

    group_section = soups["collab"].select_one('[data-table-apiname="DT_Collab19_RandomTraitGroups"]')
    require(group_section is not None, "Missing Collab random group table")
    groups = []
    # The page uses one loot-outcome containing forty separate member cards.
    for card in group_section.select(".loot-item--members"):
        groups.append({
            "name": text(card.select_one(".opener-label"), "Collab group label"),
            "champions": [text(node, "Collab member") for node in card.select(".opener-champion-name")],
        })

    patch_match = re.search(r"Last Updated:\s*(Patch\s+[\w.]+)", soups["collab"].get_text(" ", strip=True))
    require(patch_match is not None, "Missing TFT Flow patch label")
    data = {
        "version": f"Set 19 Prismatic Tour / pre-release snapshot {snapshot_date} / TFT Flow {patch_match.group(1)}",
        "sources": [
            {"name": "TFT Ninja Set 19 traits", "url": SOURCES["traits"]},
            {"name": "TFT Ninja Set 19 champions", "url": SOURCES["units"], "note": AKALI_EXCLUSION_NOTE},
            {"name": "TFT Flow Set 19 Collab tables", "url": SOURCES["collab"]},
        ],
        "traits": traits,
        "champions": champions,
        "collab_groups": groups,
    }
    data["sources"].extend(apply_unlocks(champions, pages["unlocks"], pages["unlock_roster"], snapshot_date))
    apply_ranges(data, load_ranges(pages["champion_ranges"]))
    validate_data(data, source_members, champion_slugs)
    return data


def validate_data(data: dict, source_members: dict[str, set[str]], champion_slugs: dict) -> None:
    for key, expected in EXPECTED.items():
        require(len(data[key]) == expected, f"Expected {expected} {key}; got {len(data[key])}. Review source changes.")
    traits = {trait["name"]: trait for trait in data["traits"]}
    champions = {champion["name"]: champion for champion in data["champions"]}
    require(len(traits) == EXPECTED["traits"], "Duplicate trait names")
    require(len(champions) == EXPECTED["champions"], "Duplicate champion names")
    require(sum(champion["unlockable"] for champion in champions.values()) == 32, "Current unlockable roster changed")
    require(sum(trait["unique"] for trait in traits.values()) == 19, "Unique trait classification changed")
    for trait in traits.values():
        for field in ("description", "description_source"):
            require(isinstance(trait.get(field), str) and bool(trait[field].strip()),
                    f"Missing {field} for {trait['name']}")
    for champion in champions.values():
        require(champion["cost"] in {1, 2, 3, 4, 5, 7}, f"Unexpected cost for {champion['name']}")
        names = champion["traits"]
        require(bool(names) and len(names) == len(set(names)), f"Missing/duplicate traits for {champion['name']}")
        require(set(names) <= traits.keys(), f"Unknown traits for {champion['name']}")
        for field in ("role", "role_source"):
            require(isinstance(champion.get(field), str) and bool(champion[field].strip()),
                    f"Missing {field} for {champion['name']}")
        if champion["unlockable"]:
            for field in ("unlock_condition", "unlock_level", "unlock_source"):
                require(isinstance(champion.get(field), str) and bool(champion[field].strip()),
                        f"Missing {field} for {champion['name']}")
    for name, expected_members in source_members.items():
        actual_members = {slug for slug, champion in champion_slugs.items() if name in champion["traits"]}
        require(actual_members == expected_members, f"Trait/champion source disagreement for {name}")
    require(champions["Sona"]["traits"] == ["Producer", "Collab"], "Sona's native traits changed")
    require([champion["name"] for champion in champions.values() if "Collab" in champion["traits"]] == ["Sona"],
            "Only Sona may have permanent Collab")
    require(not traits["Collab"]["unique"] and traits["Collab"]["breakpoints"] == [3, 5], "Collab thresholds changed")
    expected_groups = [f"Group {number}" for number in range(1, 41)]
    require([group["name"] for group in data["collab_groups"]] == expected_groups, "Collab group ordering/coverage changed")
    for group in data["collab_groups"]:
        members = group["champions"]
        require(len(members) in {5, 6} and len(set(members)) == len(members), f"Invalid members in {group['name']}")
        require(set(members) <= champions.keys(), f"Unknown champion in {group['name']}")
        require("Sona" not in members, f"Unexpected permanent member in {group['name']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html-dir", type=Path, help="Read archived HTML and champion_ranges.json instead of fetching")
    parser.add_argument("--snapshot-date", default=date.today().isoformat(), help="ISO date used in the snapshot version")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "set19.json")
    args = parser.parse_args()
    try:
        date.fromisoformat(args.snapshot_date)
        data = parse_pages(fetch_pages(args.html_dir), args.snapshot_date)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_snapshot(args.output, data)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Snapshot refresh failed: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {args.output}: {len(data['champions'])} champions, {len(data['traits'])} traits, {len(data['collab_groups'])} groups")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
