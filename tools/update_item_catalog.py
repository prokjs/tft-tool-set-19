"""Bundle the complete IES Set 19 English item catalog; runtime stays offline.

No item effects, images, or unpublished statistics are fabricated. Derived slot
and uniqueness metadata is read from the source's tags and explicit rules text.
"""
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import requests

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'https://utils.iesdev.com/static/json/tftTest/set19/en_us/items'


def main():
    response = requests.get(SOURCE, timeout=60)
    response.raise_for_status()
    raw = response.content
    source = json.loads(raw)
    if not isinstance(source, dict) or not source:
        raise ValueError('Expected a nonempty item catalog keyed by API ID')
    items = {}
    for api_id, entry in sorted(source.items()):
        if entry.get('apiKey') != api_id:
            raise ValueError(f'Item API ID mismatch: {api_id}')
        name = entry['name']
        if not isinstance(name, str) or not name.strip() or name in items:
            raise ValueError(f'Empty or duplicate canonical item name: {name!r}')
        tags = entry['tags']
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f'Invalid source tags: {api_id}')
        description = entry['description']
        text = re.sub(r'<[^>]+>', '', description).casefold()
        component = any(tag.startswith('Item.Equippable.Item.Component') for tag in tags)
        consumable = entry['kind'] == 'consumable' or any(tag.startswith('Item.Equippable.Consumable') for tag in tags)
        equippable = not consumable and any(tag.startswith('Item.Equippable.Item.') for tag in tags)
        slots = 3 if 'consumes 3 item slots' in text or 'Item.Equippable.Item.ThiefsGloves' in tags else 1
        holder_rule = re.search(r'Only (.+?) can hold this item', re.sub(r'<[^>]+>', '', description))
        items[name] = {
            'id': api_id, 'name': name, 'kind': entry['kind'], 'tags': tags,
            'description': description, 'builds_from': entry['buildsFrom'],
            'builds_into': entry['buildsInto'], 'trait': entry['trait'],
            'equippable': equippable, 'component': component, 'slot_cost': slots if equippable else 0,
            'unique_per_champion': 'only 1 per champion' in text or 'Item.Equippable.Item.Unique' in tags,
            'allowed_holders': [holder_rule.group(1)] if holder_rule else [],
        }
    catalog = {'set': 19, 'source': SOURCE, 'snapshot_date': date.today().isoformat(),
               'source_sha256': hashlib.sha256(raw).hexdigest(),
               'mechanics_note': 'Source tags and explicit slot/unique rules only. Category fit is a heuristic, not a combat simulation; special effects and random generated items are not simulated.',
               'items': dict(sorted(items.items()))}
    destination = ROOT / 'data/item_catalog.json'
    destination.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Bundled {len(items)} sourced item records from {SOURCE}')
    print(f'Raw source SHA-256: {catalog["source_sha256"]}')


if __name__ == '__main__':
    main()
