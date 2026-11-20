"""Consistent offline display of sourced champion unlock requirements."""


def unlock_text(champion):
    if champion['unlockable']:
        text = f"Unlock: {champion['unlock_condition']}\nPlayer level: {champion['unlock_level']}"
    else:
        text = 'No special unlock requirement.'
    if champion.get('unlock_note'):
        text += '\n' + champion['unlock_note']
    return text
