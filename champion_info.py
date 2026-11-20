"""Consistent offline display of sourced champion unlock requirements."""


def unlock_text(champion):
    if champion['unlockable']:
        text = f"Unlock: {champion['unlock_condition']}\nPlayer level: {champion['unlock_level']}"
    else:
        text = 'No special unlock requirement.'
    if champion.get('unlock_note'):
        text += '\n' + champion['unlock_note']
    return text


def star_text(level):
    """Champion rank markers; aggregate trait-star counters stay numeric."""
    return '★' * level


def trait_members(data, trait_name, collab=(), sona_band=''):
    """All native members, or the current dynamic Collab pool plus Sona."""
    champions = {champion['name']: champion for champion in data['champions']}
    if trait_name == 'Collab':
        names = set(collab) | {'Sona'}
    else:
        names = {name for name, champion in champions.items() if trait_name in champion['traits']}
        if trait_name == sona_band:
            names.add('Sona')
    return [champions[name] for name in sorted(names, key=lambda name: (champions[name]['cost'], name))]


def is_tank(champion):
    """Use the sourced combat role, not every defensive class as a main tank."""
    return 'tank' in champion.get('role', '').casefold().split()
