"""Extract Set 19 unlock tasks without guessing missing thresholds.

Used by update_data.py. Its archived input directory also needs unlocks.html
(the Mobalytics guide's HTML or content-negotiated Markdown response) and
unlock_roster.html (the Blitz article). HTTPS verification remains enabled.
A blocked or structurally changed source fails rather than erasing known tasks.
The two newer sources must agree on the unlockable roster; their agreement
supersedes older Ninja lock badges without changing champions or native traits.
"""
from __future__ import annotations

from datetime import datetime
import re

from bs4 import BeautifulSoup


GUIDE_URL = "https://mobalytics.gg/tft/guides/set-19-unlockable-champions"
BLITZ_URL = "https://blitz.gg/tft/articles/tft-set-19-champions-traits-unlocks"
VARIANT_NAMES = {
    "Akali K/DA": "Akali (K/DA)",
    "Akali True Damage": "Akali (True Damage)",
}
AKALI_SLUGS = {"akali-kda": "Akali (K/DA)", "akali-true-damage": "Akali (True Damage)"}


def clean(value: str) -> str:
    return re.sub(r"\s+([,.;:!?])", r"\1", " ".join(value.split())).strip()


def guide_text(content: str) -> str:
    """Give HTML and negotiated Markdown the same heading/label representation."""
    if re.search(r"<(?:html|article|h[1-6])\b", content, re.IGNORECASE):
        soup = BeautifulSoup(content, "html.parser")
        for node in soup.select("script, style, nav, footer"):
            node.decompose()
        for heading in soup.find_all(re.compile(r"^h[1-6]$")):
            heading.replace_with("\n" + "#" * int(heading.name[1]) + " "
                                 + heading.get_text(" ", strip=True) + "\n")
        content = soup.get_text("\n", strip=True)
    # The guide currently emits doubled bold markers around its labels.
    return content.replace("*", "")


def parse_guide(content: str) -> tuple[dict[str, dict[str, str]], str]:
    source = guide_text(content)
    updated = re.search(r"Updated on\s+([A-Za-z]+ \d{1,2}, \d{4})", source)
    if updated is None:
        raise ValueError("Missing Mobalytics guide update date")
    updated_date = datetime.strptime(updated.group(1), "%b %d, %Y").date().isoformat()
    entries = {}
    headings = list(re.finditer(r"^#{3,6}\s+(.+)$", source, re.MULTILINE))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(source)
        block = source[heading.end():end]
        condition = re.search(r"Unlock Condition\s*:\s*(.*?)\s*Level Requirement\s*:\s*(Any|Level\s+\d+\+?)\b",
                              block, re.DOTALL | re.IGNORECASE)
        if condition is None:
            if "Unlock Condition" in block:
                raise ValueError(f"Incomplete guide requirement: {heading.group(1)}")
            continue
        title = heading.group(1).strip()
        link = re.fullmatch(r"\[([^\]]+)\]\([^\n]+\)", title)
        name = clean(link.group(1) if link else title)
        name = VARIANT_NAMES.get(name, name)
        if name in entries:
            raise ValueError(f"Duplicate guide champion: {name}")
        task = clean(condition.group(1))
        if not task:
            raise ValueError(f"Empty guide requirement: {name}")
        level = clean(condition.group(2))
        entries[name] = {
            "unlock_condition": task,
            "unlock_level": "Any" if level.lower() == "any" else "Level " + level.split()[1],
            "unlock_source": GUIDE_URL,
        }
    if not entries:
        raise ValueError("No champion unlock requirements in Mobalytics guide")
    return entries, updated_date


def parse_blitz(content: str) -> tuple[dict[str, str], str]:
    """Read the complete Champion/Quest tables, not ordinary roster tables."""
    entries = {}
    soup = BeautifulSoup(content, "html.parser")
    tables = soup.find_all("table")
    if tables:
        for table in tables:
            headers = [clean(node.get_text(" ", strip=True)) for node in table.select("th")]
            if headers != ["Champion", "Quest"]:
                continue
            for row in table.select("tr"):
                cells = row.find_all("td", recursive=False)
                if len(cells) != 2:
                    continue
                link = cells[0].select_one('a[href*="/tft/set19/champions/"]')
                if link is None:
                    raise ValueError("Missing champion link in Blitz quest table")
                slug = link["href"].rstrip("/").rsplit("/", 1)[-1]
                name = AKALI_SLUGS.get(slug, clean(link.get_text(" ", strip=True)))
                if name in entries:
                    raise ValueError(f"Duplicate Blitz unlock champion: {name}")
                entries[name] = clean(cells[1].get_text(" ", strip=True))
        modified = soup.select_one('meta[property="article:modified_time"]')
        date_text = modified.get("content", "") if modified else ""
    else:
        in_quests = False
        for line in content.splitlines():
            if not line.startswith("|"):
                in_quests = False
                continue
            cells = line.split("|")[1:-1]
            if len(cells) != 2:
                in_quests = False
                continue
            if clean(cells[0]) == "Champion" and clean(cells[1]) == "Quest":
                in_quests = True
                continue
            if not in_quests:
                continue
            link = re.search(r'/tft/set19/champions/([^\s")]+)\s+"([^"]+)"', cells[0])
            if link is None:
                continue
            name = AKALI_SLUGS.get(link.group(1), link.group(2))
            if name in entries:
                raise ValueError(f"Duplicate Blitz unlock champion: {name}")
            # Remove linked icon images before keeping the visible linked text.
            quest = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", cells[1])
            quest = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", quest)
            entries[name] = clean(quest)
        modified = re.search(r'meta-article:modified_time:\s*"?([^"\s]+)', content)
        date_text = modified.group(1) if modified else ""
    if not re.match(r"\d{4}-\d{2}-\d{2}", date_text) or not entries:
        raise ValueError("Missing Blitz unlock tables or modification date")
    return entries, date_text[:10]


def single_threshold(task: str) -> list[str]:
    without_level = re.sub(r"\bLevel\s+\d+\+?", "", task, flags=re.IGNORECASE)
    return [number.replace(",", "") for number in re.findall(r"\b\d[\d,]*\b", without_level)]


def apply_unlocks(champions: list[dict], guide_content: str, blitz_content: str,
                  snapshot_date: str) -> list[dict]:
    guide, updated_date = parse_guide(guide_content)
    blitz, blitz_date = parse_blitz(blitz_content)
    names = {champion["name"] for champion in champions}
    if len(guide) != 32 or set(guide) != set(blitz) or not set(guide) <= names:
        raise ValueError("Current sources disagree on the 32-champion unlock roster; review source changes")
    for champion in champions:
        name = champion["name"]
        champion["unlockable"] = name in guide
        # A refresh never retains obsolete requirements or old conflict notes.
        for field in ("unlock_condition", "unlock_level", "unlock_source", "unlock_note"):
            champion.pop(field, None)
        if name not in guide:
            if name in {"Nasus", "Renata Glasc", "Galio", "Syndra"}:
                champion["unlock_source"] = BLITZ_URL
                champion["unlock_note"] = (
                    f"No special unlock: the complete current unlock tables (Mobalytics {updated_date}; "
                    f"Blitz {blitz_date}) supersede this champion's older TFT Ninja lock badge.")
            continue
        champion.update(guide[name])
        primary_numbers = single_threshold(guide[name]["unlock_condition"])
        other_numbers = single_threshold(blitz[name])
        if len(primary_numbers) == len(other_numbers) == 1 and primary_numbers != other_numbers:
            champion["unlock_note"] = (
                f"PBE sources disagree. Blitz ({blitz_date}) lists: {blitz[name]}. "
                f"The displayed requirement follows Mobalytics ({updated_date}). {BLITZ_URL}")
    return [
        {
            "name": "Mobalytics Set 19 unlock guide",
            "url": GUIDE_URL,
            "updated": updated_date,
            "retrieved": snapshot_date,
            "note": "Tasks and player levels for all 32 currently unlockable champions; preferred when PBE numeric thresholds disagree.",
        },
        {
            "name": "Blitz Set 19 unlock roster cross-check",
            "url": BLITZ_URL,
            "updated": blitz_date,
            "retrieved": snapshot_date,
            "note": "Both newer sources list 32 unlocks. The older Ninja roster marks Nasus, Renata Glasc, Galio and Syndra locked; "
                    "the current complete eight-champion 1–3-cost quest table excludes all four. "
                    "Blitz also explicitly places Nasus/Renata in the starting shop and Syndra in the normal 3-cost Beach Pop progression. "
                    "These four are therefore stored without special unlock requirements.",
        },
    ]
