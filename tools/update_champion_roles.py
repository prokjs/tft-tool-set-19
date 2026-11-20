"""Refresh offline champion roles without changing traits, costs, or unlocks.

Run: python tools/update_champion_roles.py
Refresh-only dependencies: requests and beautifulsoup4 (requirements-artwork.txt).
Roles come from the full Set 19 champion roster, not combat traits. HTTPS
certificate verification stays enabled. The source names and costs must match
exactly before any metadata is changed or the snapshot is atomically replaced.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

SOURCE_URL = 'https://tft.ninja/sets/19/units'
ROOT = Path(__file__).resolve().parents[1]


def role_fields(row):
    """Extract the source's complete role label and its champion reference URL."""
    heading = row.select_one('.set-roster-name a[href]')
    role = row.select_one('td[data-label="Role"]')
    if heading is None or role is None:
        raise ValueError('Champion source row is missing its name, URL, or role.')
    label = role.get_text(' ', strip=True)
    if not label:
        raise ValueError('Empty champion role in source roster.')
    source = urljoin(SOURCE_URL, heading['href'])
    parsed = urlsplit(source)
    slug = row.get('id')
    if (not slug or parsed.scheme != 'https' or parsed.netloc != 'tft.ninja'
            or parsed.path != f'/sets/19/units/{slug}' or parsed.query or parsed.fragment):
        raise ValueError('Champion role source must be its HTTPS Set 19 unit page.')
    return {'role': label, 'role_source': source}


def apply_roles(data, html):
    """Validate the complete source roster before updating only role metadata."""
    soup = BeautifulSoup(html, 'html.parser')
    parsed = {}
    costs = {}
    slugs = set()
    for section in soup.select('section.set-roster-section'):
        match = re.fullmatch(r'cost-(\d+)', section.get('aria-labelledby', ''))
        if not match:
            continue
        rows = section.select('tbody tr')
        if not rows:
            raise ValueError('Empty champion cost section in source roster.')
        for row in rows:
            heading = row.select_one('.set-roster-name a')
            if heading is None:
                raise ValueError('Champion source row is missing its name.')
            name = heading.get_text(' ', strip=True).removeprefix('Unlockable:').strip()
            if not name or name in parsed:
                raise ValueError(f'Duplicate or empty champion name: {name}')
            fields = role_fields(row)
            slug = row['id']
            if slug in slugs:
                raise ValueError(f'Duplicate champion source slug: {slug}')
            slugs.add(slug)
            parsed[name] = fields
            costs[name] = int(match.group(1))
    expected = {champion['name']: champion['cost'] for champion in data['champions']}
    if len(expected) != len(data['champions']):
        raise ValueError('Duplicate champion names in bundled snapshot.')
    if set(parsed) != set(expected):
        raise ValueError('Source champion roster differs from the bundled snapshot; review before updating.')
    for name, cost in expected.items():
        if costs[name] != cost:
            raise ValueError(f'Source cost changed for {name}; refresh the full champion snapshot.')
    for champion in data['champions']:
        champion.update(parsed[champion['name']])


def main():
    import requests
    destination = ROOT / 'data' / 'set19.json'
    data = json.loads(destination.read_text(encoding='utf-8'))
    response = requests.get(SOURCE_URL, timeout=45, headers={'User-Agent': 'Set19ChampionRoles/1.0'})
    response.raise_for_status()
    apply_roles(data, response.text)
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(destination)
    print(f"Bundled sourced roles for {len(data['champions'])} champions; other metadata preserved.")
    tanks = [champion['name'] for champion in data['champions']
             if champion['cost'] == 4 and champion['role'].endswith(' Tank')]
    print('4-cost Tank catalog: ' + ', '.join(tanks))


if __name__ == '__main__':
    main()
