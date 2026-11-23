"""Generate offline Set 19 display catalogs without changing the English snapshot.

Run: python tools/update_localized_game_data.py
Official Data Dragon character names take precedence after exact English-name
and stable character-ID joins; they do not supply TFT stats or trait text.
Archived input: --source-dir DIR, containing versions.json,
<locale>.champion.json, <locale>.units.json and <locale>.traits.json.
Missing captures must be declared in unavailable.json as
{"<locale>.<kind>": "reason"}; absent files are otherwise an error.

Only this maintenance command uses the network, with default verified HTTPS.
HTTP 404 means unpublished; TLS/network errors abort rather than masquerading
as unpublished data. A trusted HTTPS reader's JSON captures may be supplied
when the source's certificate cannot be verified by the local Python runtime.

Catalogs retain canonical English keys, joining champions through the snapshot's
English range-provenance JSON pointers and traits through English apiKeys.
Regional aliases are labeled source_locale, not represented as regional
translations. Unchanged English trait prose is explicitly marked missing even
when served by a non-English endpoint. Unchanged proper names alone do not
prove missing translations. The missing list contains dotted field paths;
coverage explains these conservative, non-linguistic quality checks.

Game strings are republished by IES from Riot's pre-release game data, not
machine-translated here. Publication is not evidence of commercial reuse
permission, official endorsement, or complete translation. Provenance is
bundled in every output; downstream distribution must review applicable terms.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = 'https://utils.iesdev.com/static/json/tftTest/set19'
LANGUAGES_URL = 'https://ddragon.leagueoflegends.com/cdn/languages.json'
LOCALES = (
    'ar_AE', 'en_US', 'cs_CZ', 'de_DE', 'el_GR', 'en_AU', 'en_GB',
    'en_PH', 'en_SG', 'es_AR', 'es_ES', 'es_MX', 'fr_FR', 'hu_HU',
    'id_ID', 'it_IT', 'ja_JP', 'ko_KR', 'pl_PL', 'pt_BR', 'ro_RO',
    'ru_RU', 'th_TH', 'tr_TR', 'vi_VN', 'zh_CN', 'zh_MY', 'zh_TW',
)
REGIONAL_ALIASES = {
    'en_AU': 'en_US', 'en_GB': 'en_US', 'en_PH': 'en_US', 'en_SG': 'en_US',
    'es_AR': 'es_ES', 'es_MX': 'es_ES', 'zh_MY': 'zh_CN',
}
AKALI_VARIANTS = {
    'DA_AkaliKDA19': ('Akali (K/DA)', 'K/DA'),
    'DA_Akali_TrueDamage19': ('Akali (True Damage)', 'True Damage'),
}
HERO_OVERRIDES = frozenset({
    'DA_Annie19_HeroAugment_Override', 'DA_Elise19_HeroAugment_Override',
    'DA_Kennen19_HeroAugment_Override', 'DA_Lillia19_HeroAugmentOverride',
    'DA_Olaf19_HeroAugment_Override', 'DA_Renekton19_HeroAugment_Override',
    'DA_Sett19_HeroAugment_Override',
})


class PlainText(HTMLParser):
    """Discard game formatting and non-display script/style content."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif not self.hidden and tag in ('br', 'p', 'div', 'li'):
            self.parts.append('\n')

    def handle_startendtag(self, tag, attrs):
        if tag not in ('script', 'style'):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in ('p', 'div', 'li'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(text):
    if not isinstance(text, str):
        raise ValueError('Game display text must be a string.')
    parser = PlainText()
    parser.feed(text)
    parser.close()
    text = re.sub(r'%i:[^%]*%', '', ''.join(parser.parts))
    return '\n'.join(line for line in (
        re.sub(r'[^\S\n]+', ' ', line).strip() for line in text.splitlines()
    ) if line)


def decode_json(text):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate source JSON key: {key}')
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=unique_object)
    if not isinstance(value, dict) or not value:
        raise ValueError('Expected a nonempty game-data object.')
    return value


def source_url(locale, kind):
    return f'{BASE_URL}/{locale.lower()}/{kind}'


def validate_records(records):
    for key, record in records.items():
        if not isinstance(record, dict) or record.get('apiKey') != key:
            raise ValueError(f'Invalid source apiKey: {key}')


def canonical_ids(base, english_units, english_traits):
    validate_records(english_units)
    validate_records(english_traits)
    champion_ids = {}
    for champion in base['champions']:
        name = champion['name']
        reference = urlsplit(champion['attack_range_source'])
        pointer = reference.fragment.split('/')
        if (reference._replace(fragment='').geturl() != source_url('en_US', 'units')
                or len(pointer) != 5 or pointer[0] != ''
                or pointer[2:] != ['stats', 'offense', 'range']):
            raise ValueError(f'Invalid English range provenance: {name}')
        key = unquote(pointer[1]).replace('~1', '/').replace('~0', '~')
        record = english_units.get(key)
        if record is None:
            raise ValueError(f'English source champion missing: {name}')
        source_name = AKALI_VARIANTS[key][0] if key in AKALI_VARIANTS else record.get('name')
        if source_name != name or record.get('cost') != champion['cost']:
            raise ValueError(f'English champion identity/cost changed: {name}')
        if name in champion_ids or key in champion_ids.values():
            raise ValueError(f'Duplicate canonical champion: {name}')
        champion_ids[name] = key
    if set(english_units) - HERO_OVERRIDES != set(champion_ids.values()):
        raise ValueError('English source roster differs from the canonical snapshot.')
    by_name = {}
    for key, trait in english_traits.items():
        name = trait.get('name')
        if not isinstance(name, str) or not name.strip() or name in by_name:
            raise ValueError(f'Invalid/duplicate English trait name: {name}')
        by_name[name] = key
    trait_ids = {}
    for trait in base['traits']:
        name = trait['name']
        if name not in by_name or name in trait_ids:
            raise ValueError(f'English source trait missing/duplicated: {name}')
        trait_ids[name] = by_name[name]
    return champion_ids, trait_ids


def description_parts(trait):
    parts = []
    description = plain_text(trait.get('description', ''))
    if description:
        parts.append(('description', description))
    bonuses = trait.get('bonuses', [])
    if not isinstance(bonuses, list):
        raise ValueError('Trait bonuses must be a list.')
    for index, bonus in enumerate(bonuses):
        if not isinstance(bonus, dict) or type(bonus.get('needed')) is not int:
            raise ValueError('Trait bonus must have an integer breakpoint.')
        effect = plain_text(bonus.get('effect', ''))
        if effect:
            parts.append((f'bonuses/{index}/effect', f"{bonus['needed']} {effect}"))
    return parts


def source_hash(records):
    normalized = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def ddragon_url(version, locale):
    return f'https://ddragon.leagueoflegends.com/cdn/{version}/data/{locale}/champion.json'


def official_champion_ids(champion_ids, official):
    english = official['en_US']['data']
    by_name = {}
    for key, record in english.items():
        name = record.get('name')
        if record.get('id') != key or not isinstance(name, str) or name in by_name:
            raise ValueError('Invalid/duplicate official English champion identity.')
        by_name[name] = key
    result = {}
    for name, key in champion_ids.items():
        english_name = 'Akali' if key in AKALI_VARIANTS else name
        if english_name in by_name:
            result[name] = by_name[english_name]
    return result


def build_catalog(locale, captures, unavailable, champion_ids, trait_ids, retrieved_at,
                  dd_version=None, official=None, official_ids=None):
    selected = {}
    source_locales = {}
    for kind in ('units', 'traits'):
        source_locale = locale
        if (locale, kind) not in captures:
            source_locale = REGIONAL_ALIASES.get(locale)
        if source_locale is not None and (source_locale, kind) in captures:
            selected[kind] = captures[(source_locale, kind)]
            source_locales[kind] = source_locale
            validate_records(selected[kind])
    common_locale = set(source_locales.values())
    source_locale = next(iter(common_locale)) if len(common_locale) == 1 else None
    catalog = {
        'locale': locale,
        'source_locale': source_locale,
        'champions': {}, 'traits': {}, 'sources': [], 'missing': [],
        'coverage': {
            'source_locale_by_endpoint': source_locales,
            'unpublished_endpoints': {
                kind: unavailable[(locale, kind)] for kind in ('units', 'traits')
                if (locale, kind) in unavailable
            },
            'english_equal_descriptions': [],
            'partly_english_descriptions': [],
            'unchanged_names_are_not_translation_validation': True,
            'entirely_english_trait_payload': (
                'traits' in selected and not source_locales['traits'].startswith('en_')
                and selected['traits'] == captures[('en_US', 'traits')]
            ),
        },
        'provenance': {
            'provider': 'Official Riot Data Dragon character names; third-party IES TFT PBE trait/game data',
            'translation_method': 'Published source strings; no machine translation applied',
            'retrieved_at': retrieved_at,
            'language_list_source': LANGUAGES_URL,
            'requested_sources': [source_url(locale, kind) for kind in ('units', 'traits')],
            'english_join_sources': [source_url('en_US', kind) for kind in ('units', 'traits')],
            'champion_api_keys': champion_ids,
            'trait_api_keys': trait_ids,
            'official_champion_ids': official_ids or {},
            'official_champion_name_source': ddragon_url(dd_version, locale) if dd_version else None,
            'official_data_scope': 'League character display names only; not TFT Set 19 stats or traits',
            'official_versions_source': 'https://ddragon.leagueoflegends.com/api/versions.json',
            'official_english_join_source': ddragon_url(dd_version, 'en_US') if dd_version else None,
            'champion_name_source_by_key': {},
            'third_party_pbe_review_required_before_public_ad_launch': bool(selected),
            'source_sha256_normalized_json': {},
            'dependency_license': 'Python standard library; Python Software Foundation License',
            'content_rights': 'Riot game content. Official publication and third-party IES availability are not blanket commercial reuse permission. IES TFT PBE content requires separate provider/Riot terms review before public or ad-supported launch; not rights-cleared.',
            'quality_notice': 'Pre-release source strings may remain English, be incomplete, or change. No official endorsement or complete translation is claimed.',
        },
    }
    for kind, records in selected.items():
        url = source_url(source_locales[kind], kind)
        catalog['sources'].append(url)
        catalog['provenance']['source_sha256_normalized_json'][url] = source_hash(records)
    if official is not None:
        url = ddragon_url(dd_version, locale)
        catalog['sources'].insert(0, url)
        catalog['champion_name_source_locale'] = locale
        catalog['provenance']['source_sha256_normalized_json'][url] = source_hash(official[locale])
    for name, key in trait_ids.items():
        record = selected.get('traits', {}).get(key)
        if record is None:
            catalog['missing'].extend([f'traits.{name}.name', f'traits.{name}.description'])
            continue
        translated_name = plain_text(record.get('name', ''))
        if not translated_name or catalog['coverage']['entirely_english_trait_payload']:
            catalog['missing'].append(f'traits.{name}.name')
        parts = description_parts(record)
        description = '\n'.join(value for _, value in parts) or None
        path = f'traits.{name}.description'
        if description is None:
            catalog['missing'].append(path)
        elif not source_locales['traits'].startswith('en_'):
            english_parts = dict(description_parts(captures[('en_US', 'traits')][key]))
            unchanged = [part for part, value in parts if english_parts.get(part) == value]
            if unchanged:
                catalog['missing'].append(path)
                field = 'english_equal_descriptions' if len(unchanged) == len(parts) else 'partly_english_descriptions'
                catalog['coverage'][field].append(name)
        catalog['traits'][name] = {'name': translated_name, 'description': description}
    for name, key in champion_ids.items():
        official_key = (official_ids or {}).get(name)
        official_record = (official or {}).get(locale, {}).get('data', {}).get(official_key)
        if official_record is not None:
            if official_record.get('id') != official_key:
                raise ValueError(f'Official localized champion identity changed: {name}')
            display = plain_text(official_record.get('name', ''))
            source = ddragon_url(dd_version, locale) + f'#/data/{official_key}/name'
        else:
            record = selected.get('units', {}).get(key)
            display = plain_text(record.get('name', '')) if record is not None else ''
            source = (source_url(source_locales['units'], 'units') + f'#/{key}/name'
                      if record is not None else None)
        if not display:
            catalog['missing'].append(f'champions.{name}')
            continue
        if key in AKALI_VARIANTS:
            qualifier = AKALI_VARIANTS[key][1]
            qualifier = catalog['traits'].get(qualifier, {}).get('name') or qualifier
            display = f'{display} ({qualifier})'
        catalog['champions'][name] = display
        catalog['provenance']['champion_name_source_by_key'][name] = source
    catalog['missing'] = sorted(set(catalog['missing']))
    return catalog


def load_sources(source_dir=None):
    captures, unavailable = {}, {}
    manifest = {}
    if source_dir is not None:
        manifest_path = source_dir / 'unavailable.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for locale in LOCALES:
        for kind in ('units', 'traits'):
            if source_dir is not None:
                path = source_dir / f'{locale}.{kind}.json'
                if not path.exists():
                    reason = manifest.get(f'{locale}.{kind}')
                    if not isinstance(reason, str) or not reason.strip():
                        raise ValueError(f'Missing archive without explicit unavailability: {path}')
                    unavailable[(locale, kind)] = reason
                    continue
                text = path.read_text(encoding='utf-8')
            else:
                request = Request(source_url(locale, kind), headers={'User-Agent': 'TFT-Tool-localized-data-updater/1.0'})
                try:
                    with urlopen(request, timeout=45) as response:
                        text = response.read().decode('utf-8')
                except HTTPError as exc:
                    if exc.code != 404:
                        raise
                    unavailable[(locale, kind)] = 'HTTP 404: locale endpoint unpublished'
                    continue
            captures[(locale, kind)] = decode_json(text)
    for kind in ('units', 'traits'):
        if ('en_US', kind) not in captures:
            raise ValueError(f'English identity source unavailable: {kind}')
    return captures, unavailable


def load_official_sources(source_dir=None):
    versions_url = 'https://ddragon.leagueoflegends.com/api/versions.json'
    if source_dir is not None:
        versions = json.loads((source_dir / 'versions.json').read_text(encoding='utf-8'))
    else:
        with urlopen(versions_url, timeout=45) as response:
            versions = json.load(response)
    if not isinstance(versions, list) or not versions or not re.fullmatch(r'\d+\.\d+\.\d+', versions[0]):
        raise ValueError('Invalid official Data Dragon version manifest.')
    version = versions[0]
    official = {}
    for locale in LOCALES:
        if source_dir is not None:
            value = decode_json((source_dir / f'{locale}.champion.json').read_text(encoding='utf-8'))
        else:
            with urlopen(ddragon_url(version, locale), timeout=45) as response:
                value = decode_json(response.read().decode('utf-8'))
        if value.get('version') != version or not isinstance(value.get('data'), dict):
            raise ValueError(f'Invalid official Data Dragon champion catalog: {locale}')
        official[locale] = value
    return version, official


def write_catalog(path, catalog):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=f'.{path.name}.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(catalog, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path)
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'data' / 'set19.json')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'data' / 'locales_game')
    args = parser.parse_args()
    base = decode_json(args.snapshot.read_text(encoding='utf-8'))
    captures, unavailable = load_sources(args.source_dir)
    dd_version, official = load_official_sources(args.source_dir)
    champion_ids, trait_ids = canonical_ids(base, captures[('en_US', 'units')], captures[('en_US', 'traits')])
    official_ids = official_champion_ids(champion_ids, official)
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    catalogs = [build_catalog(locale, captures, unavailable, champion_ids, trait_ids, retrieved_at,
                              dd_version, official, official_ids)
                for locale in LOCALES]
    for catalog in catalogs:
        write_catalog(args.output_dir / f"{catalog['locale']}.json", catalog)
        print(f"{catalog['locale']}: source={catalog['source_locale'] or 'unavailable/mixed'}; "
              f"{len(catalog['champions'])} names, {len(catalog['traits'])} traits; "
              f"{len(catalog['missing'])} missing/unverified fields")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
