"""Refresh published Set 19 trios and explicit role-based item suggestions.

Build-only dependencies: requests and beautifulsoup4. No runtime network access.
Missing published trios are labeled heuristics, never represented as sourced BiS.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import requests

ROOT = Path(__file__).resolve().parents[1]
INDEX = 'https://blitz.gg/tft/set19/champions'


def normalize(name):
    return re.sub(r'[^a-z0-9]', '', name.casefold().replace('&', 'and'))


def fetch(url):
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    response.encoding = 'utf-8'
    return response.text


def parse_build(html, source):
    soup = BeautifulSoup(html, 'html.parser')
    row = soup.select_one('table.item-trios-table tbody tr')
    if row is None:
        if soup.select_one('h1') is None:
            raise ValueError(f'Missing champion page content: {source}')
        return None
    images = row.select('td[data-sort-key="items"] a[href^="/tft/set19/items/"] img') if row else []
    items = [image.get('alt', '').removesuffix(' Item').strip() for image in images]
    if len(items) != 3 or not all(items):
        raise ValueError(f'Expected a published three-item build: {source}')
    return {'items': items, 'source': source, 'kind': 'published_trio',
            'label': 'Published item trio',
            'selection_note': 'Published champion item-trio suggestion; not guaranteed best items.',
            'source_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest()}


def role_build(unit):
    role = unit['role'].casefold().split()
    if 'tank' in role:
        items = ['Gargoyle Stoneplate', "Warmog's Armor", 'Spirit Visage']
    elif role[0] == 'magic':
        items = (['Spear of Shojin', 'Jeweled Gauntlet', "Rabadon's Deathcap"]
                 if role[-1] in ('caster', 'marksman', 'specialist') else
                 ['Bloodthirster', 'Jeweled Gauntlet', "Titan's Resolve"])
    elif role[-1] in ('fighter', 'assassin', 'specialist'):
        items = ['Bloodthirster', "Sterak's Gage", "Titan's Resolve"]
    else:
        items = (["Guinsoo's Rageblade", 'Infinity Edge', 'Last Whisper']
                 if role[-1] == 'marksman' else
                 ['Spear of Shojin', 'Infinity Edge', 'Last Whisper'])
    return {'items': items, 'source': unit['role_source'], 'kind': 'role_based',
            'label': 'Role-based item suggestion',
            'items_source': 'https://blitz.gg/tft/set19/items',
            'selection_note': 'Role-based suggestion using the sourced combat role and Set 19 item catalog. '
                              'No champion-specific published trio is available; this is not verified best-in-slot.'}


def main():
    data = json.loads((ROOT / 'data/set19.json').read_text(encoding='utf-8'))
    roster = {normalize(unit['name']): unit['name'] for unit in data['champions']}
    units = {unit['name']: unit for unit in data['champions']}
    soup = BeautifulSoup(fetch(INDEX), 'html.parser')
    links = {}
    variants = {'akali-kda': 'Akali (K/DA)', 'akali-true-damage': 'Akali (True Damage)'}
    for anchor in soup.select('a[href][aria-label]'):
        match = re.fullmatch(r'View (.+) details', anchor['aria-label'])
        if match and anchor['href'].startswith('/tft/set19/champions/'):
            slug = anchor['href'].rsplit('/', 1)[-1]
            name = variants.get(slug) or roster.get(normalize(match.group(1)))
            if name:
                links[name] = urljoin(INDEX, anchor['href'])
    missing = set(roster.values()) - links.keys()
    if missing:
        raise ValueError(f'Missing published champion links: {sorted(missing)}')
    def load(pair):
        name, url = pair
        build = parse_build(fetch(url), url)
        if build is None and name == 'Vladimir':
            source = 'https://tftflow.com/tables/set19/vladimir'
            html = fetch(source)
            images = BeautifulSoup(html, 'html.parser').select('img[src*="/items/icon/"]')
            items = [image.get('alt', '').strip() for image in images][:3]
            if len(items) != 3 or not all(items):
                raise ValueError('Vladimir recommended items are missing.')
            build = {'items': items, 'source': source, 'kind': 'published_recommendations',
                     'label': 'Published recommended items',
                     'selection_note': 'First three items from the published recommended list, not a measured item-trio ranking.',
                     'source_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest()}
        if build is None:
            build = role_build(units[name])
            build['availability_source'] = url
        return name, build
    with ThreadPoolExecutor(max_workers=6) as pool:
        builds = dict(pool.map(load, sorted(links.items())))
    snapshot = {'snapshot_date': date.today().isoformat(), 'set': 19, 'patch': '19.1',
                'source': INDEX, 'selection': 'Published trios where available; explicit role-based suggestions otherwise.',
                'champions': builds}
    destination = ROOT / 'data/item_builds.json'
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(destination)
    published = sum(build['kind'] != 'role_based' for build in builds.values())
    print(f'Bundled {len(builds)} item plans: {published} published builds/lists; {len(builds) - published} labeled role-based suggestions.')


if __name__ == '__main__':
    main()
