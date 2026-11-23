"""Offline, sourced final-holder item suggestions carried through leveling boards."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=1)
def load_item_builds():
    path = Path(__file__).resolve().parent / 'data' / 'item_builds.json'
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)['champions']


def item_archetype(champion, catalog=None):
    """Classify sourced combat roles and the offline recommended item trio."""
    words = (champion.get('role') or '').casefold().split()
    if 'tank' in words:
        return 'tank'
    for role in ('assassin', 'fighter'):
        if role in words:
            return role
    catalog = load_item_builds() if catalog is None else catalog
    items = catalog.get(champion.get('name'), {}).get('items', ())
    if 'marksman' in words or any(
            item in items for item in ("Guinsoo's Rageblade", "Kraken's Fury")):
        return 'attack_speed'
    if ('specialist' in words and 'Bloodthirster' in items
            and any(item in items for item in ("Sterak's Gage", "Titan's Resolve"))):
        return 'fighter'
    return 'caster' if 'caster' in words else 'general'


def holder_match(holder, target, stars, target_stars, archetypes):
    if holder is None:
        return 'unassigned'
    if holder == target and stars >= target_stars:
        return 'final'
    left, right = archetypes.get(holder, 'general'), archetypes.get(target, 'general')
    if left == right and right != 'general':
        return 'same_archetype'
    if {left, right} == {'assassin', 'fighter'}:
        return 'related_melee'
    return 'damage_fallback'


def annotate_item_builds(result, champions, archetypes=None):
    from team_guidance import item_profile
    catalog = load_item_builds()
    archetypes = archetypes if archetypes is not None else {
        name: item_archetype(champion, catalog) for name, champion in champions.items()}
    final_holders = result.get('item_holders', {})
    builds = []
    for kind in ('tank', 'ad', 'ap', 'flex'):
        target = final_holders.get(kind)
        if target is None:
            continue
        source = catalog.get(target)
        if source is None:
            # Synthetic test rosters have no published champion build.
            continue
        builds.append((kind, target, source))

    def annotate(board):
        if board is None:
            return
        holders = board.get('item_holders', {})
        board['planned_item_builds'] = []
        for kind, target, source in builds:
            holder = holders.get(kind)
            if kind in ('ad', 'ap') and holder is None:
                candidate = holders.get('flex')
                if candidate and item_profile(champions.get(candidate, {})) in (kind, 'flex'):
                    holder = candidate
            match = holder_match(holder, target, board.get('planned_stars', {}).get(holder, 1),
                                 result.get('planned_stars', {}).get(target, 1), archetypes)
            board['planned_item_builds'].append({
                'kind': kind, 'target': target, 'holder': holder,
                'items': list(source['items']), 'source': source['source'],
                'label': source['label'], 'source_kind': source['kind'],
                'note': source['selection_note'],
                'target_archetype': archetypes.get(target, 'general'),
                'holder_archetype': archetypes.get(holder, 'general') if holder else None,
                'holder_match': match,
            })
    annotate(result)
    for step in result.get('buildup', []):
        for key in ('board', 'fallback', 'goal_board'):
            annotate(step.get(key))
        for phase in step.get('upgrades', []):
            annotate(phase.get('board'))
        for preparation in step.get('preparation_boards', []):
            annotate(preparation.get('board'))
