"""Small per-user desktop preferences; no game data or usage tracking."""
import json
import os
from pathlib import Path
import tempfile


def settings_path():
    return Path(os.environ.get('APPDATA', str(Path.home()))) / 'prokjs-set19-tool' / 'settings.json'


def load_language(path, supported):
    if not path.exists():
        return 'en_US', None
    try:
        code = json.loads(path.read_text(encoding='utf-8'))['language']
        if code not in supported:
            raise ValueError('Unsupported language setting')
        return code, None
    except (OSError, ValueError, KeyError, TypeError) as error:
        return 'en_US', str(error)


def save_language(path, code):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='language-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'language': code}, stream)
            stream.write('\n')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
