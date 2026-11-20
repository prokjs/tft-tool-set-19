"""Refresh offline trait effects without changing champion or unlock data.

Run: python tools/update_trait_descriptions.py
Build-only dependencies: requests and beautifulsoup4 (requirements-artwork.txt).
Uses the same Set 19 trait source as update_data.py. TLS stays verified; missing
or changed source cards abort before the existing snapshot is overwritten.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

SOURCE_URL = 'https://tft.ninja/sets/19/traits'
ROOT = Path(__file__).resolve().parents[1]


def _text(node):
    return re.sub(r'\s+([,.;:!?])', r'\1', ' '.join(node.get_text(' ', strip=True).split()))


def description_fields(card):
    heading = card.select_one('h3.set-trait-name a[href]')
    content = card.select_one('.set-trait-text')
    if heading is None or content is None:
        raise ValueError('Trait source is missing a name, URL, or effect body.')
    parts = []
    for node in content.find_all(['p', 'li']):
        if node.find_parent(['p', 'li']) is None:
            value = _text(node)
            if value:
                parts.append(value)
    if not parts:
        value = _text(content)
        if value:
            parts.append(value)
    if not parts:
        raise ValueError(f'Empty trait effect: {_text(heading)}')
    return {'description': '\n'.join(parts),
            'description_source': urljoin(SOURCE_URL, heading['href'])}


def apply_descriptions(data, html):
    soup = BeautifulSoup(html, 'html.parser')
    parsed = {}
    points = {}
    for card in soup.select('section.set-trait'):
        heading = card.select_one('h3.set-trait-name a')
        if heading is None:
            raise ValueError('Trait source card is missing its name.')
        name = _text(heading)
        if name in parsed:
            raise ValueError(f'Duplicate trait effect: {name}')
        parsed[name] = description_fields(card)
        points[name] = sorted({int(chip.get_text(strip=True)) for chip in card.select('.set-bp-chip')})
    expected = {trait['name'] for trait in data['traits']}
    if set(parsed) != expected:
        raise ValueError('Source trait roster differs from the bundled snapshot; review before updating.')
    for trait in data['traits']:
        if points[trait['name']] != trait['breakpoints']:
            raise ValueError(f"Source breakpoints changed for {trait['name']}; refresh the full trait snapshot.")
    for trait in data['traits']:
        trait.update(parsed[trait['name']])


def main():
    import requests
    destination = ROOT / 'data' / 'set19.json'
    data = json.loads(destination.read_text(encoding='utf-8'))
    response = requests.get(SOURCE_URL, timeout=45, headers={'User-Agent': 'Set19TraitReference/1.0'})
    response.raise_for_status()
    apply_descriptions(data, response.text)
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(destination)
    print(f"Bundled sourced effects for {len(data['traits'])} traits; champion and unlock data preserved.")


if __name__ == '__main__':
    main()
