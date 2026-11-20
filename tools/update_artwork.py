"""Download Set 19 artwork linked by TFT Ninja and generate offline PNGs.

Run: python tools/update_artwork.py
Build-only dependencies: requests, beautifulsoup4, pillow, resvg-py.
Source URLs and attribution are retained in assets/manifest.json. No network is
used by the desktop app. Full roster/trait coverage is required before publishing.
"""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageOps
import requests
import resvg_py

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://tft.ninja/sets/19/traits'
COLORS = {1: '#9ca3af', 2: '#4ade80', 3: '#60a5fa', 4: '#c084fc', 5: '#fbbf24', 7: '#fb7185'}


def fetch(url):
    response = requests.get(url, timeout=40)
    response.raise_for_status()
    return response.content


def create_unlock_badge(directory):
    """Generate the shared small padlock; runtime only loads this cached PNG."""
    scale = 3
    image = Image.new('RGBA', (16 * scale, 18 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, 16 * scale - 1, 18 * scale - 1), radius=3 * scale, fill='#111827')
    draw.arc((4 * scale, 1 * scale, 12 * scale, 11 * scale), 180, 360, fill='#f1d69b', width=2 * scale)
    draw.line((4 * scale, 5 * scale, 4 * scale, 9 * scale), fill='#f1d69b', width=2 * scale)
    draw.line((12 * scale, 5 * scale, 12 * scale, 9 * scale), fill='#f1d69b', width=2 * scale)
    draw.rounded_rectangle((3 * scale, 7 * scale, 13 * scale, 16 * scale), radius=scale,
                           fill='#253047', outline='#f1d69b', width=scale)
    draw.ellipse((7 * scale, 10 * scale, 9 * scale, 12 * scale), fill='#f1d69b')
    draw.line((8 * scale, 12 * scale, 8 * scale, 14 * scale), fill='#f1d69b', width=scale)
    image.resize((16, 18), Image.Resampling.LANCZOS).save(directory / 'unlock-badge.png')


def main():
    data = json.loads((ROOT / 'data/set19.json').read_text(encoding='utf-8'))
    page = BeautifulSoup(fetch(SOURCE), 'html.parser')
    champion_urls, trait_urls = {}, {}
    for section in page.select('section.set-trait'):
        heading = section.select_one('h3.set-trait-name a')
        trait_urls[heading.get_text(strip=True)] = urljoin(SOURCE, heading.select_one('img')['src'])
        for link in section.select('.set-trait-members a'):
            # Remove screen-reader costs/unlock labels, not the champion name.
            for hidden in link.select('.visually-hidden'):
                hidden.decompose()
            champion_urls[link.get_text(strip=True)] = urljoin(SOURCE, link.select_one('img')['src'])
    expected_champions = {c['name'] for c in data['champions']}
    expected_traits = {t['name'] for t in data['traits']}
    if set(champion_urls) != expected_champions or set(trait_urls) != expected_traits:
        raise ValueError('Artwork source roster differs from bundled data; review before publishing.')
    directory = ROOT / 'assets'
    directory.mkdir(exist_ok=True)
    create_unlock_badge(directory)
    manifest = {'source': SOURCE, 'attribution': 'Champion and trait artwork © Riot Games. Images sourced from TFT Ninja Set 19.', 'champions': {}, 'traits': {}}
    costs = {c['name']: c['cost'] for c in data['champions']}

    def convert(job):
        kind, name, url, index = job
        raw = fetch(url)
        if kind == 'traits':
            rendered = resvg_py.svg_to_bytes(svg_string=raw.decode('utf-8'), width=24, height=24)
            image = Image.open(BytesIO(rendered)).convert('RGBA')
            # Source glyphs may be black/currentColor. Preserve shape/alpha on dark UI.
            white = Image.new('RGBA', image.size, '#e5e7eb')
            white.putalpha(image.getchannel('A'))
            filename = f'trait-{index}.png'
            white.save(directory / filename)
            dark = Image.new('RGBA', image.size, '#14202b')
            dark.putalpha(image.getchannel('A'))
            dark.save(directory / f'trait-{index}-dark.png')
            return kind, name, {'file': filename, 'url': url}
        image = Image.open(BytesIO(raw)).convert('RGBA')
        result = {'url': url}
        for key, size in [('small', 32), ('large', 56)]:
            portrait = ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS)
            ImageDraw.Draw(portrait).rectangle((0, 0, size - 1, size - 1), outline=COLORS[costs[name]], width=2)
            filename = f'champion-{index}-{key}.png'
            portrait.save(directory / filename)
            result[key] = filename
        return kind, name, result

    jobs = [(kind, name, url, index) for kind, mapping in [('champions', champion_urls), ('traits', trait_urls)] for index, (name, url) in enumerate(sorted(mapping.items()))]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for kind, name, record in pool.map(convert, jobs):
            manifest[kind][name] = record
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"Bundled {len(manifest['champions'])} champion portraits and {len(manifest['traits'])} trait icons.")


if __name__ == '__main__':
    main()
