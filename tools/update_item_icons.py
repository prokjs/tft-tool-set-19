"""Bundle pinned CommunityDragon artwork for all item-build names.

Run: python tools/update_item_icons.py
Build-only dependencies: requests, pillow. Runtime uses local files only.
All downloads validate before atomic publication of the complete manifest.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from PIL import Image
import requests

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://explore.communitydragon.org/tft/19.1.0/'
PIN = 'tft-pbe-windows-rls-19.1.0-5986652-B6A307EE125EC1A0'
INDEX_URL = BASE + 'cdragon/files.exported.txt'
INDEX_SHA256 = 'ba59540ddc2f11a30266d09499c9ca0490178f293b6a70f3a2a6fcd410ecf37b'
# Exact normal-item export stems, not legacy API aliases or radiant/artifact
# variants. In particular Kraken's Fury, Spirit Visage, Striker's Flail and
# Void Staff have their own current artwork; do not substitute their predecessors.
NORMAL_ITEMS = {
    "Archangel's Staff": 'archangelsstaff',
    'Bloodthirster': 'bloodthirster',
    'Blue Buff': 'bluebuff',
    'Crownguard': 'crownguard',
    'Deathblade': 'deathblade',
    'Edge of Night': 'edgeofnight',
    'Gargoyle Stoneplate': 'gargoylestoneplate',
    "Guinsoo's Rageblade": 'guinsoosrageblade',
    'Hand Of Justice': 'handofjustice',
    'Hextech Gunblade': 'hextechgunblade',
    'Infinity Edge': 'infinityedge',
    'Ionic Spark': 'ionicspark',
    'Jeweled Gauntlet': 'jeweledgauntlet',
    "Kraken's Fury": 'krakensfury',
    'Last Whisper': 'lastwhisper',
    'Morellonomicon': 'morellonomicon',
    "Rabadon's Deathcap": 'rabadonsdeathcap',
    'Red Buff': 'redbuff',
    'Spear of Shojin': 'spearofshojin',
    'Spirit Visage': 'spiritvisage',
    'Steadfast Heart': 'steadfastheart',
    "Sterak's Gage": 'steraksgage',
    "Striker's Flail": 'strikersflail',
    'Sunfire Cape': 'sunfirecape',
    "Titan's Resolve": 'titansresolve',
    'Void Staff': 'voidstaff',
    "Warmog's Armor": 'warmogsarmor',
}
ITEM_PATHS = {
    name: f'tft/plugins/gamefeatures/item_shared/content/item/{stem}/t_{stem}.png'
    for name, stem in NORMAL_ITEMS.items()
}
# Bard's champion-specific enabled unique-item icon, not the disabled variant.
ITEM_PATHS["Bard's Horn of the Traveler"] = (
    'tft/plugins/gamefeatures/set_19/content/champions/bard/'
    'bard19_uniqueitem/t_bard19_uniqueitemicon.png'
)


def fetch(url):
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    return response.content




def main():
    builds = json.loads((ROOT / 'data/item_builds.json').read_text(encoding='utf-8'))
    required = sorted({name for build in builds['champions'].values() for name in build['items']})
    if not required or not all(isinstance(name, str) and name for name in required):
        raise ValueError('The item-build inventory must contain nonempty item names.')
    index = fetch(INDEX_URL)
    index_digest = hashlib.sha256(index).hexdigest()
    if index_digest != INDEX_SHA256:
        raise ValueError('Pinned CommunityDragon export index changed; nothing published.')
    exported = set(index.decode('utf-8-sig').splitlines())
    missing = sorted(name for name in required
                     if name not in ITEM_PATHS or ITEM_PATHS[name] not in exported)
    if missing:
        raise ValueError(f'No verified indexed item artwork; nothing published: {missing}')

    def load(name):
        image_url = BASE + ITEM_PATHS[name]
        source = fetch(image_url)
        with Image.open(BytesIO(source)) as image:
            image.verify()
        with Image.open(BytesIO(source)) as image:
            width, height = image.size
            if image.format != 'PNG' or width != height or width < 24:
                raise ValueError(f'Invalid square PNG item artwork for {name}')
            # Preserve the export's square resolution (normally 128px).
            output = BytesIO()
            image.convert('RGBA').save(output, format='WEBP', lossless=True, method=6)
        content = output.getvalue()
        with Image.open(BytesIO(content)) as image:
            image.verify()
        digest = hashlib.sha256(content).hexdigest()
        slug = re.sub(r'[^a-z0-9]+', '-', name.casefold()).strip('-')
        filename = f'{slug}-{digest}.webp'
        entry = {'file': f'assets/items/{filename}', 'source': image_url,
                 'discovered_from': INDEX_URL, 'image_url': image_url,
                 'source_path': ITEM_PATHS[name], 'pin': PIN,
                 'index_sha256': index_digest,
                 'source_sha256': hashlib.sha256(source).hexdigest(),
                 'sha256': digest, 'width': width, 'height': height}
        return name, filename, content, entry

    with ThreadPoolExecutor(max_workers=6) as pool:
        downloaded = list(pool.map(load, required))
    manifest = {'snapshot_date': date.today().isoformat(), 'set': 19,
                'source': BASE, 'pin': PIN, 'index_url': INDEX_URL,
                'index_sha256': index_digest, 'catalog_sha256': index_digest,
                'attribution': 'Item artwork © Riot Games. Sourced from pinned CommunityDragon TFT exports.',
                'coverage': {'required': len(required), 'mapped': len(downloaded), 'missing': []},
                'items': {name: entry for name, _, _, entry in downloaded}}
    directory = ROOT / 'assets/items'
    directory.mkdir(parents=True, exist_ok=True)
    destination = ROOT / 'data/item_icons.json'
    previous_files = set()
    if destination.exists():
        previous = json.loads(destination.read_text(encoding='utf-8'))
        previous_files = {entry['file'] for entry in previous['items'].values()}
    with tempfile.TemporaryDirectory(prefix='.update-', dir=directory) as temporary:
        staging = Path(temporary)
        for _, filename, content, _ in downloaded:
            (staging / filename).write_bytes(content)
        staged_manifest = staging / 'item_icons.json'
        staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        # Content-addressed image names keep the old manifest usable until the
        # final single-file atomic replacement publishes the complete new set.
        for _, filename, _, _ in downloaded:
            os.replace(staging / filename, directory / filename)
        os.replace(staged_manifest, destination)
    current_files = {entry['file'] for entry in manifest['items'].values()}
    for obsolete in previous_files - current_files:
        path = ROOT / obsolete
        if path.parent == directory and path.suffix == '.webp':
            path.unlink(missing_ok=True)
    print(f'Item icon coverage: {len(downloaded)}/{len(required)}; missing: 0')
    for name, _, content, entry in downloaded:
        print(f"{name} | {entry['image_url']} | {entry['width']}x{entry['height']} | {len(content)} bytes")
    print(f'Published {destination.relative_to(ROOT)}; all artwork bundled locally.')


if __name__ == '__main__':
    main()
