"""Bundle pinned CommunityDragon Set 19 champion portraits and trait glyphs.

Run: python tools/update_artwork.py
Build-only dependencies: requests, pillow. Runtime remains entirely offline.
Downloads, coverage and PNGs are validated in staging before any assets publish.
The manifest retains source URLs, export/catalog hashes and image hashes/sizes.
"""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageOps
import requests

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://explore.communitydragon.org/tft/19.1.0/'
PIN = 'tft-pbe-windows-rls-19.1.0-5986652-B6A307EE125EC1A0'
INDEX_URL = SOURCE + 'cdragon/files.exported.txt'
EXPORT_URL = SOURCE + 'cdragon/export.json'
CATALOG_BASE = 'https://utils.iesdev.com/static/json/tftTest/set19/en_us/'
SET19 = 'tft/plugins/gamefeatures/set_19/content/'
SHARED = 'tft/plugins/gamefeatures/ui_shared/content/textures/icons/set19/'
MASKS = SET19 + 'traits/_traiticons/iconmask/t_traiticon_19_'
COLORS = {1: '#9ca3af', 2: '#4ade80', 3: '#60a5fa', 4: '#c084fc', 5: '#fbbf24', 7: '#fb7185'}

# These are exact indexed filenames, not alternate URL guesses. Both Akalis have
# distinct directories despite sharing their filename. Seraphine F1 is her Indie
# starting form; F2/F3 are Rising Star/Superstar (not separate roster entries).
# Sett's newer 256px square is the same Heartsteel skin as his older 128px crop.
# Sona deliberately uses the ordinary teamplanner portrait, not a genre variant.
CHAMPION_PATHS = {
    'Akali (K/DA)': SET19 + 'champions/akali_kda/ui/t_set19_akali_teamplanner_256x256.png',
    'Akali (True Damage)': SET19 + 'champions/akali_truedamage/ui/t_set19_akali_teamplanner_256x256.png',
    'Seraphine': SET19 + 'champions/seraphine/ui/t_set19_seraphinef1_teamplanner_256x256.png',
    'Sett': SET19 + 'champions/sett/ui/t_set19_sett_teamplanner_256x256.png',
}
CHAMPION_ALIASES = {'Nunu & Willump': 'nunu'}

# Display names and internal trait identities are evidenced by the IES Set 19
# traits catalog (apiKey -> name); assets themselves are exclusively CDragon.
# The mask suffix is explicit because renamed traits and unique-trait owners
# cannot safely be inferred from their display names.
TRAIT_MASKS = {
    'Bodyguard': ('DA_Bodyguard19', 'bodyguard'),
    'Choreographer': ('DA_Choreographer19', 'choreographer'),
    'Classical': ('DA_Virtuosos19', 'virtuoso'),
    'EDM': ('DA_EDM19', 'edm'),
    'Encore': ('DA_Encore19', 'encore'),
    'Frontman': ('DA_Frontman19', 'frontman'),
    'Gu Feng': ('DA_Heavenscale19', 'heavenscale'),
    'Heartsteel': ('DA_Heartsteel19', 'heartsteel'),
    'Hitmaker': ('DA_Hitmaker19', 'hitmaker'),
    'Hyperpop': ('DA_GlitterBomb19', 'hyperpop'),
    'Illbeats': ('DA_Illbeats19', 'illbeats'),
    'Jazz': ('DA_Jazz19', 'jazz'),
    'Juggernaut': ('DA_Juggernaut19', 'juggernaut'),
    'K/DA': ('DA_KDA19', 'kda'),
    'Manager': ('DA_Manager19', 'manager'),
    'Pentakill': ('DA_Pentakill19', 'pentakill'),
    'Prodigy': ('DA_Prodigy19', 'prodigy'),
    'R&B': ('DA_RnB19', 'rnb'),
    'Renegade': ('DA_Renegade19', 'renegade'),
    'Rock': ('DA_DemonVision19', 'rock'),
    'Slayer': ('DA_Mosher19', 'slayer'),
    'Swiftstriker': ('DA_Swiftstriker19', 'swiftstriker'),
    'Trickshot': ('DA_Trickshot19', 'trickshot'),
    'True Damage': ('DA_TrueDamage19', 'truedamage'),
    # Beat Master's speaker glyph is internally called Synchmancer; its shape
    # was compared with the previously bundled Beat Master glyph, not an emblem.
    'Beat Master': ('DA_Rell19_UniqueTrait', 'synchmancer'),
    'Collab': ('DA_Collab19', 'collab'),
    'Composer': ('DA_Malzahar19_UniqueTrait', 'composer'),
    'Country': ('DA_Alistar19_UniqueTrait', 'country'),
    'Disco': ('DA_Nunu19_UniqueTrait', 'disco'),
    'Freestyle': ('DA_Akali_TrueDamage19_UniqueTrait', 'freestyle'),
    'Hype Man': ('DA_Teemo19_UniqueTrait', 'hypeman'),
    'Indie': ('DA_Seraphine19_IndieTrait', 'indie'),
    'Mariachi': ('DA_Nidalee19_UniqueTrait', 'mariachi'),
    'Melodic Heart': ('DA_Gwen19_UniqueTrait', 'melodicheart'),
    'One Monster Band': ('DA_BaronNashor19_UniqueTrait', 'onemonsterband'),
    'Producer': ('DA_Sona19_UniqueTrait', 'producer'),
    'Windblessed': ('DA_Janna19_UniqueTrait', 'windblessed'),
}
TRAIT_PATHS = {
    'Arcanist': ('DA_Arcanist19', SET19 + 'traits/_traiticons/t_arcanist19.png'),
    'Beach Pop': ('DA_OceanSong19', SHARED + 't_traiticon_19_beachpop.png'),
    'Ionic Spark': ('DA_IonicSpark19', SHARED + 't_traiticon_19_ionicspark.png'),
    '8-Bit': ('DA_MissFortune19_UniqueTrait', SET19 + 'champions/missfortune/uniquetrait/t_missfortune19_uniquetrait_icon.png'),
    'Boy Band': ('DA_Rakan19_UniqueTrait', SET19 + 'champions/rakan/uniquetrait/t_rakan19_uniquetrait_icon.png'),
    'Emo': ('DA_Amumu19_UniqueTrait', SET19 + 'champions/amumu/uniquetrait/t_amumu19_uniquetrait_icon.png'),
    'Goth': ('DA_Zeri19_UniqueTrait', SET19 + 'champions/zeri/uniquetrait/t_traiticon_19_goth.png'),
    'Record Mogul': ('DA_DrMundo19_UniqueTrait', SET19 + 'champions/drmundo/uniquetrait/textures/t_traiticon_19_recordmogul.png'),
    'Synthwave': ('DA_Garen19_UniqueTrait', SET19 + 'champions/garen/uniquetrait/t_traiticon_19_synthwave.png'),
    'Tango': ('DA_TwistedFate19_UniqueTrait', SET19 + 'champions/twisted_fate/uniquetrait/t_traiticon_19_tango.png'),
}


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


def normalized(name):
    return re.sub('[^a-z0-9]', '', name.lower())


def resolve_paths(data, indexed_paths, trait_catalog, unit_catalog):
    """Resolve only reviewed variants; never choose the first fuzzy match."""
    candidates = defaultdict(list)
    pattern = re.compile(r'/ui/t_19_([a-z0-9]+)_teamplanner_portrait\.png$')
    for path in indexed_paths:
        match = pattern.search(path)
        if path.startswith(SET19 + 'champions/') and match:
            candidates[match[1]].append(path)
    champions, traits, errors = {}, {}, []
    for champion in data['champions']:
        name = champion['name']
        if name in CHAMPION_PATHS:
            choices = [CHAMPION_PATHS[name]]
        else:
            choices = candidates.get(CHAMPION_ALIASES.get(name, normalized(name)), [])
        if len(choices) != 1:
            errors.append(f"champion {name}: expected one portrait, found {sorted(choices)}")
        elif choices[0] not in indexed_paths:
            errors.append(f"champion {name}: missing indexed path {choices[0]}")
        else:
            champions[name] = choices[0]
    mappings = {name: (key, MASKS + suffix + '.png')
                for name, (key, suffix) in TRAIT_MASKS.items()}
    mappings.update(TRAIT_PATHS)
    for trait in data['traits']:
        name = trait['name']
        if name not in mappings:
            errors.append(f'trait {name}: no reviewed glyph mapping')
            continue
        key, path = mappings[name]
        if trait_catalog.get(key, {}).get('name') != name:
            errors.append(f'trait {name}: catalog does not confirm {key}')
        elif path not in indexed_paths:
            errors.append(f'trait {name}: missing indexed glyph {path}')
        else:
            traits[name] = (key, path)
    seraphine = unit_catalog.get('DA_SeraphineIndie19', {})
    if seraphine.get('name') != 'Seraphine' or 'DA_Seraphine19_IndieTrait' not in seraphine.get('origin', []):
        errors.append('champion Seraphine: catalog no longer confirms the starting Indie form')
    if errors:
        raise ValueError('Artwork mappings require review:\n' + '\n'.join(errors))
    return champions, traits


def digest(raw):
    return sha256(raw).hexdigest()


def file_record(path):
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        if image.format != 'PNG' or image.mode != 'RGBA':
            raise ValueError(f'Invalid generated PNG: {path.name}')
        dimensions = list(image.size)
    return {'sha256': digest(path.read_bytes()), 'dimensions': dimensions}


def main():
    data = json.loads((ROOT / 'data/set19.json').read_text(encoding='utf-8'))
    export_raw = fetch(EXPORT_URL)
    if json.loads(export_raw)['meta']['pin'] != PIN:
        raise ValueError(f'CommunityDragon export pin differs from {PIN}; review before publishing.')
    index_raw = fetch(INDEX_URL)
    indexed_paths = set(index_raw.decode('utf-8').splitlines())
    catalogs, catalog_provenance = {}, {}
    for kind in ('traits', 'units'):
        url = CATALOG_BASE + kind
        raw = fetch(url)
        catalogs[kind] = json.loads(raw)
        catalog_provenance[kind] = {'url': url, 'sha256': digest(raw)}
    champions, traits = resolve_paths(data, indexed_paths, catalogs['traits'], catalogs['units'])
    manifest = {
        'source': SOURCE,
        'pin': PIN,
        'attribution': 'Champion and trait artwork © Riot Games. Images sourced from CommunityDragon Set 19.',
        'export': {'url': EXPORT_URL, 'sha256': digest(export_raw)},
        'index': {'url': INDEX_URL, 'sha256': digest(index_raw)},
        'mapping_catalogs': catalog_provenance,
        'champions': {},
        'traits': {},
    }
    costs = {champion['name']: champion['cost'] for champion in data['champions']}
    jobs = [('champions', name, path, index, None)
            for index, (name, path) in enumerate(sorted(champions.items()))]
    jobs += [('traits', name, path, index, key)
             for index, (name, (key, path)) in enumerate(sorted(traits.items()))]

    # Stage on the destination filesystem. A failed mapping, HTTP request or
    # image decode cannot replace any previously bundled file or its manifest.
    with TemporaryDirectory(prefix='.artwork-', dir=ROOT) as temporary:
        staging = Path(temporary)

        def convert(job):
            kind, name, path, index, catalog_key = job
            url = SOURCE + path
            try:
                raw = fetch(url)
                with Image.open(BytesIO(raw)) as source:
                    if source.format != 'PNG':
                        raise ValueError('source is not PNG')
                    source.verify()
                with Image.open(BytesIO(raw)) as source:
                    image = source.convert('RGBA')
                if image.width != image.height:
                    raise ValueError(f'source is not square: {image.size}')
                record = {'url': url, 'source_path': path, 'source_sha256': digest(raw),
                          'source_dimensions': list(image.size), 'outputs': {}}
                if kind == 'traits':
                    alpha = image.getchannel('A')
                    if alpha.getextrema() != (0, 255):
                        raise ValueError('trait glyph must have transparent background and opaque foreground')
                    alpha = alpha.resize((24, 24), Image.Resampling.LANCZOS)
                    filename = f'trait-{index}.png'
                    dark_filename = f'trait-{index}-dark.png'
                    for output, color in [(filename, '#e5e7eb'), (dark_filename, '#14202b')]:
                        glyph = Image.new('RGBA', (24, 24), color)
                        glyph.putalpha(alpha)
                        glyph.save(staging / output)
                        record['outputs'][output] = file_record(staging / output)
                    record.update(file=filename, dark_file=dark_filename, catalog_key=catalog_key,
                                  mapping_evidence={'url': CATALOG_BASE + 'traits',
                                                    'json_pointer': f'/{catalog_key}/name'})
                    if name == 'Beat Master':
                        record['mapping_note'] = 'Synchmancer speaker mask; visually matched to the previously bundled Beat Master glyph.'
                else:
                    for key, size in [('small', 32), ('large', 56)]:
                        portrait = ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS)
                        ImageDraw.Draw(portrait).rectangle((0, 0, size - 1, size - 1), outline=COLORS[costs[name]], width=2)
                        filename = f'champion-{index}-{key}.png'
                        portrait.save(staging / filename)
                        record[key] = filename
                        record['outputs'][filename] = file_record(staging / filename)
                    if name == 'Seraphine':
                        record['mapping_evidence'] = {
                            'url': CATALOG_BASE + 'units',
                            'json_pointer': '/DA_SeraphineIndie19/origin',
                            'note': 'F1 Indie starting form; F2 Rising Star and F3 Superstar are later forms.',
                        }
                    if name in CHAMPION_ALIASES:
                        record['source_alias'] = CHAMPION_ALIASES[name]
                return kind, name, record
            except Exception as error:
                raise ValueError(f'{kind} {name}: {url}: {error}') from error

        with ThreadPoolExecutor(max_workers=8) as pool:
            for kind, name, record in pool.map(convert, jobs):
                manifest[kind][name] = record
        if set(manifest['champions']) != {c['name'] for c in data['champions']} or set(manifest['traits']) != {t['name'] for t in data['traits']}:
            raise ValueError('Incomplete artwork coverage; nothing published.')
        create_unlock_badge(staging)
        manifest['generated'] = {'unlock-badge.png': file_record(staging / 'unlock-badge.png')}
        manifest_path = staging / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        directory = ROOT / 'assets'
        directory.mkdir(exist_ok=True)
        for path in sorted(staging.glob('*.png')):
            path.replace(directory / path.name)
        manifest_path.replace(directory / 'manifest.json')
    print(f"Bundled {len(manifest['champions'])} champion portraits and {len(manifest['traits'])} trait icons from {PIN}.")


if __name__ == '__main__':
    main()
