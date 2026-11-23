"""Import/export bundled community AI translations without network services.

Run --export to obtain incremental missing-key work, translate with an authorized
model or community contributors, then --import-dir with JSON message mappings.
No undocumented translation endpoints, runtime network, or embedded credentials.
"""
from __future__ import annotations

import argparse
import ast
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import string
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from localization import LOCALES

LANGUAGE_SOURCE = 'https://ddragon.leagueoflegends.com/cdn/languages.json'
TARGETS = {locale: locale.split('_')[0] for locale, _ in LOCALES}
TARGETS.update(zh_CN='zh-CN', zh_MY='zh-CN', zh_TW='zh-TW')
FORMATTER = string.Formatter()
# These are gaming roles, never reservoirs/containers. Other terms and proper
# names remain protected tokens and are resolved by the runtime's game catalog.
TANK = {'ar': 'تانك', 'cs': 'Tank', 'de': 'Tank', 'el': 'Τανκ', 'es': 'Tanque',
        'fr': 'Tank', 'hu': 'Tank', 'id': 'Tank', 'it': 'Tank', 'ja': 'タンク',
        'ko': '탱커', 'pl': 'Tank', 'pt': 'Tanque', 'ro': 'Tank', 'ru': 'Танк',
        'th': 'แทงค์', 'tr': 'Tank', 'vi': 'Chống chịu', 'zh': '坦克'}
CARRY = {'ar': 'كاري', 'cs': 'Carry', 'de': 'Carry', 'el': 'Carry', 'es': 'Carry',
         'fr': 'Carry', 'hu': 'Carry', 'id': 'Carry', 'it': 'Carry', 'ja': 'キャリー',
         'ko': '캐리', 'pl': 'Carry', 'pt': 'Carry', 'ro': 'Carry', 'ru': 'Керри',
         'th': 'แครี่', 'tr': 'Taşıyıcı', 'vi': 'Chủ lực', 'zh': '主力输出'}
EXTRA = {
    'Language settings', 'Language', 'Community AI translation · Some text may remain in English.',
    'Community translations', 'Translation coverage', 'English fallback', 'Offline team explorer',
    'Tank', 'Carry', 'High value', 'More variety', 'More traits', 'Balanced', 'None',
    'Language changes display text only; champion IDs and team codes stay unchanged.',
}


def useful(text):
    return (len(text) <= 5000 and bool(re.search(r'[A-Za-z]{2}', text))
            and not re.match(r'^https?://', text)
            and not re.fullmatch(r'[\w./\\#:@!*\[\]<>+-]+', text)
            and not re.search(r'\b(?:import |def |SELECT |CREATE TABLE)', text)
            and '\\' not in text)


def source_messages():
    result = set(EXTRA)
    for path in ROOT.glob('*.py'):
        if path.name.startswith(('test_', 'smoke_')) or path.name in ('localization.py', 'serve_web.py'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                parts, index = [], 0
                for value in node.values:
                    if isinstance(value, ast.Constant):
                        parts.append(str(value.value).replace('{', '{{').replace('}', '}}'))
                    elif isinstance(value, ast.FormattedValue):
                        parts.append('{v' + str(index) + '}')
                        index += 1
                template = ''.join(parts)
                if useful(template):
                    result.add(template)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if isinstance(parents.get(node), ast.JoinedStr):
                    continue
                # Module/function/class docstrings are not visible UI.
                parent = parents.get(node)
                if isinstance(parent, ast.Expr):
                    continue
                text = node.value
                if useful(text) or (text.isalpha() and (text.istitle() or text.isupper())):
                    result.add(text)
    web_strings = ROOT / 'web' / 'strings.json'
    if web_strings.exists():
        labels = json.loads(web_strings.read_text(encoding='utf-8'))
        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            raise ValueError('Web display strings must be a JSON array of text.')
        result.update(labels)
    data = json.loads((ROOT / 'data/set19.json').read_text(encoding='utf-8'))
    for group in ('traits', 'champions'):
        for item in data[group]:
            result.add(item['name'])
            for key in ('description', 'unlock', 'unlock_task', 'unlock_note', 'unlock_condition', 'role', 'unlock_level'):
                value = item.get(key)
                if isinstance(value, str) and useful(value):
                    result.add(value)
    builds = json.loads((ROOT / 'data/item_builds.json').read_text(encoding='utf-8'))
    for build in builds['champions'].values():
        result.update(build['items'])
        result.add(build['label'])
        result.add(build['label'] + ' · not owned.')
        result.add(build['selection_note'])
    # Paragraph and rich-text line fallbacks use the same cache as whole strings.
    for text in list(result):
        for line in text.splitlines():
            if useful(line):
                result.add(line)
    return sorted(result)


def fields(text):
    try:
        return sorted(field for _, field, _, _ in FORMATTER.parse(text) if field is not None)
    except ValueError:
        return None


def protected_terms():
    data = json.loads((ROOT / 'data/set19.json').read_text(encoding='utf-8'))
    return sorted({item['name'] for key in ('champions', 'traits') for item in data[key]}
                  | {'AD', 'AP', 'TFT', 'Collab', 'K/DA', 'True Damage', 'prokjs',
                     "prokjs's Set 19 TFT tool", 'Developed by prokjs'}, key=len, reverse=True)


def validate_messages(messages):
    for source, translated in messages.items():
        if not isinstance(source, str) or not isinstance(translated, str) or not translated.strip():
            raise ValueError('Catalog entries must be nonempty strings')
        if fields(source) != fields(translated):
            raise ValueError(f'Translation placeholders changed: {source!r}')



def atomic_json(path, value):
    # Resource generation is the writer; replace only after complete JSON exists.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--locales', nargs='*', choices=list(TARGETS))
    parser.add_argument('--import-dir', type=Path)
    parser.add_argument('--export', type=Path, help='Write missing sources per language for authorized translation')
    args = parser.parse_args()
    destination = ROOT / 'data/locales_ui'
    sources = source_messages()
    selected = args.locales or list(TARGETS)
    digest = hashlib.sha256('\n'.join(sources).encode()).hexdigest()
    pending = {}
    for locale in selected:
        english = locale.startswith('en_')
        path = destination / f'{locale}.json'
        existing = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        cache = existing.get('messages', {})
        if args.import_dir and not english:
            imports = args.import_dir / f'{TARGETS[locale]}.json'
            if not imports.exists():
                imports = args.import_dir / f'{locale}.json'
            if imports.exists():
                incoming = json.loads(imports.read_text(encoding='utf-8'))
                incoming = incoming.get('messages', incoming)
                validate_messages(incoming)
                cache.update(incoming)
        if english:
            cache = {source: source for source in sources}
        validate_messages(cache)
        messages = {source: cache[source] for source in sources if source in cache}
        missing = [source for source in sources if source not in messages]
        pending[TARGETS[locale]] = missing
        atomic_json(path, {
            'locale': locale,
            'metadata': {
                'kind': 'source-English' if english else 'community-AI-translation',
                'provider': None if english else 'Authorized internal model completion; community-generated text',
                'locale_list_source': LANGUAGE_SOURCE,
                'translation_target': 'en' if english else TARGETS[locale],
                'generated': date.today().isoformat(), 'source_sha256': digest,
                'source_count': len(sources), 'translated_count': len(messages),
                'missing_count': len(missing), 'coverage': len(messages) / max(1, len(sources)),
                'reviewed': False,
                'notice': 'Community AI translations, not official Riot translations. No endorsement or translation-quality guarantee. Missing text falls back visibly to English.',
                'regional_note': 'Regional variants share their language catalog; regional accuracy is not asserted.',
                'gaming_terms': 'Protected canonical proper names, AD/AP, curated tank/carry glossary; game names resolved offline at runtime.',
                'commercial_use_note': 'Generated UI wording is community material, not a grant of rights to Riot game data, artwork, trademarks, or third-party source material.',
            },
            'messages': messages, 'missing': missing,
        })
    atomic_json(destination / 'sources.json', {'messages': sources, 'sha256': digest})
    if args.export:
        atomic_json(args.export, {'sources': sources, 'missing': pending,
                                 'protected_terms': protected_terms()})


if __name__ == '__main__':
    main()
