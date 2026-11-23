"""Refresh verified offline Set 19 base attack ranges without changing other fields.

Run: python tools/update_champion_ranges.py [--source-json archived_units.json]
Source: https://utils.iesdev.com/static/json/tftTest/set19/en_us/units
The source's stats.offense.range is the base attack range in hexes, not ability
reach. Seven explicitly identified hero-augment overrides are not roster units.
Every remaining name/cost must match the snapshot before any mutation. Each
range includes an HTTPS JSON-pointer reference to its exact source field.
HTTPS verification remains enabled; failed fetch/validation/write leaves the
snapshot untouched. Network access is confined to this maintenance tool.
Full refresh: update_data.py also imports these ranges and accepts archived
champion_ranges.json alongside its HTML inputs.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from urllib.request import Request, urlopen

SOURCE_URL = 'https://utils.iesdev.com/static/json/tftTest/set19/en_us/units'
ROOT = Path(__file__).resolve().parents[1]
HERO_AUGMENT_OVERRIDES = frozenset({
    'DA_Annie19_HeroAugment_Override',
    'DA_Elise19_HeroAugment_Override',
    'DA_Kennen19_HeroAugment_Override',
    'DA_Lillia19_HeroAugmentOverride',
    'DA_Olaf19_HeroAugment_Override',
    'DA_Renekton19_HeroAugment_Override',
    'DA_Sett19_HeroAugment_Override',
})
AKALI_NAMES = {
    'akali-kda': 'Akali (K/DA)',
    'akali-true-damage': 'Akali (True Damage)',
}
SOURCE = {
    'name': 'Set 19 base attack ranges (IES champion data)',
    'url': SOURCE_URL,
    'note': 'stats.offense.range in hexes; base roster only, excluding seven hero-augment overrides. Individual fields use HTTPS JSON-pointer provenance.',
}


def load_ranges(text):
    """Decode source JSON without silently accepting duplicate object keys."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate source JSON key: {key}')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique_object)


def apply_ranges(data, units):
    """Validate exact base roster/costs and all ranges, then add only provenance/ranges."""
    if not isinstance(units, dict) or not units:
        raise ValueError('Champion range source must be a nonempty unit object.')
    expected = {champion['name']: champion['cost'] for champion in data['champions']}
    if len(expected) != len(data['champions']):
        raise ValueError('Duplicate champion names in bundled snapshot.')
    parsed = {}
    slugs = set()
    for key, unit in units.items():
        if key in HERO_AUGMENT_OVERRIDES:
            continue
        if not isinstance(unit, dict) or unit.get('apiKey') != key:
            raise ValueError(f'Invalid source unit identity: {key}')
        name, slug = unit.get('name'), unit.get('slug')
        if not isinstance(name, str) or not name.strip() or not isinstance(slug, str) or not slug:
            raise ValueError(f'Missing source unit name/slug: {key}')
        if name == 'Akali':
            if slug not in AKALI_NAMES:
                raise ValueError(f'Unknown Akali variant: {slug}')
            name = AKALI_NAMES[slug]
        if name in parsed or slug in slugs:
            raise ValueError(f'Duplicate source champion name/slug: {name}')
        slugs.add(slug)
        cost = unit.get('cost')
        if type(cost) is not int or cost <= 0:
            raise ValueError(f'Invalid source cost for {name}')
        stats = unit.get('stats')
        offense = stats.get('offense') if isinstance(stats, dict) else None
        attack_range = offense.get('range') if isinstance(offense, dict) else None
        if (type(attack_range) not in (int, float) or not math.isfinite(attack_range)
                or attack_range <= 0):
            raise ValueError(f'Missing or invalid attack range for {name}')
        pointer_key = key.replace('~', '~0').replace('/', '~1')
        parsed[name] = (cost, {
            'attack_range': attack_range,
            'attack_range_source': f'{SOURCE_URL}#/{pointer_key}/stats/offense/range',
        })
    if parsed.keys() != expected.keys():
        raise ValueError('Source champion roster differs from the bundled snapshot; review before updating.')
    for name, cost in expected.items():
        if parsed[name][0] != cost:
            raise ValueError(f'Source cost changed for {name}; refresh the full champion snapshot.')
    for champion in data['champions']:
        champion.update(parsed[champion['name']][1])
    sources = data.setdefault('sources', [])
    if not any(source.get('url') == SOURCE_URL for source in sources):
        sources.append(dict(SOURCE))


def write_snapshot(destination, data):
    """Replace the snapshot atomically using a unique temporary sibling file."""
    destination = Path(destination)
    serialized = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         prefix=destination.name + '.', suffix='.tmp',
                                         dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-json', type=Path, help='Read an archived HTTPS source response instead of fetching')
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'set19.json')
    args = parser.parse_args()
    try:
        data = json.loads(args.output.read_text(encoding='utf-8-sig'))
        if args.source_json is not None:
            payload = args.source_json.read_text(encoding='utf-8-sig')
        else:
            request = Request(SOURCE_URL, headers={'User-Agent': 'Set19ChampionRanges/1.0',
                                                  'Accept': 'application/json'})
            with urlopen(request, timeout=45) as response:
                payload = response.read().decode(response.headers.get_content_charset() or 'utf-8')
        apply_ranges(data, load_ranges(payload))
        write_snapshot(args.output, data)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Champion range refresh failed: {exc}', file=sys.stderr)
        return 1
    print(f"Bundled sourced attack ranges for {len(data['champions'])} champions; other metadata preserved.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
