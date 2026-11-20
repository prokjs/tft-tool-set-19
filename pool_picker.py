"""Rules for the main roster's inline Collab-picking mode.

Each pick removes candidates sharing native traits or an exclusive form.
After the pool is locked, the roster no longer applies these pool filters.
"""

POOL_SIZES = (5, 6)


def eligible_champions(champions, selected):
    """Return unselected, nonconflicting names in cost/name order; cap at six."""
    if len(selected) >= max(POOL_SIZES):
        return []
    traits = {trait for name in selected for trait in champions[name]['traits'] if trait != 'Collab'}
    exclusive = {champions[name]['exclusive_group'] for name in selected if champions[name].get('exclusive_group')}
    return [c['name'] for c in sorted(champions.values(), key=lambda c: (c['cost'], c['name']))
            if c['name'] != 'Sona' and c['name'] not in selected
            and not traits.intersection(c['traits'])
            and (not c.get('exclusive_group') or c['exclusive_group'] not in exclusive)]


def visible_champions(champions, selected, pool_locked):
    """Retain removable picks while culling; locking restores the whole roster."""
    if pool_locked:
        return set(champions)
    return set(eligible_champions(champions, selected)) | set(selected) | {'Sona'}
