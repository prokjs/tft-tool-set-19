"""Offline community UI localization; canonical model values never change.

``tr`` returns logical Unicode (also suitable for clipboard/search). ``localize_tree``
shapes Arabic only at the Tk presentation boundary and preserves original values.
"""
from __future__ import annotations

import json
import re
import string
import sys
from functools import lru_cache
import unicodedata
from pathlib import Path

LOCALES = (
    ('ar_AE', 'العربية'), ('en_US', 'English (US)'), ('cs_CZ', 'Čeština'),
    ('de_DE', 'Deutsch'), ('el_GR', 'Ελληνικά'), ('en_AU', 'English (Australia)'),
    ('en_GB', 'English (UK)'), ('en_PH', 'English (Philippines)'),
    ('en_SG', 'English (Singapore)'), ('es_AR', 'Español (Argentina)'),
    ('es_ES', 'Español (España)'), ('es_MX', 'Español (México)'),
    ('fr_FR', 'Français'), ('hu_HU', 'Magyar'), ('id_ID', 'Bahasa Indonesia'),
    ('it_IT', 'Italiano'), ('ja_JP', '日本語'), ('ko_KR', '한국어'),
    ('pl_PL', 'Polski'), ('pt_BR', 'Português (Brasil)'), ('ro_RO', 'Română'),
    ('ru_RU', 'Русский'), ('th_TH', 'ไทย'), ('tr_TR', 'Türkçe'),
    ('vi_VN', 'Tiếng Việt'), ('zh_CN', '简体中文'), ('zh_MY', '简体中文 (马来西亚)'),
    ('zh_TW', '繁體中文'),
)
_DATA = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'data'
_language = 'en_US'
_cache = {}
_game_cache = {}
_missing = set()
_render_sources = {}
_formatter = string.Formatter()
_BRAND = "prokjs's Set 19 TFT tool"


def available_languages():
    return list(LOCALES)


def set_language(locale):
    global _language
    if locale not in dict(LOCALES):
        raise ValueError(f'Unsupported locale: {locale}')
    _language = locale
    _missing.clear()


def get_language():
    return _language


def _load(folder, locale, cache):
    if locale not in cache:
        path = _DATA / folder / f'{locale}.json'
        if path.exists():
            cache[locale] = json.loads(path.read_text(encoding='utf-8'))
        else:
            cache[locale] = {}
    return cache[locale]


def _catalog():
    return _load('locales_ui', _language, _cache)


def _game():
    return _load('locales_game', _language, _game_cache)


def localized_champion(name):
    if _language.startswith('en_'):
        return name
    game = _game()
    value = game.get('champions', {}).get(name, name)
    return tr(name) if f'champions.{name}' in game.get('missing', []) or value == name else value


def localized_trait(name):
    if _language.startswith('en_'):
        return name
    game = _game()
    value = game.get('traits', {}).get(name, {})
    translated = value.get('name', name) if isinstance(value, dict) else name
    return tr(name) if f'traits.{name}.name' in game.get('missing', []) or translated == name else translated


def localized_trait_description(name, english_description):
    game = _game()
    value = game.get('traits', {}).get(name, {})
    if _language.startswith('en_'):
        return english_description
    if f'traits.{name}.description' in game.get('missing', []):
        return tr(english_description)
    return (value.get('description') or tr(english_description)) if isinstance(value, dict) else tr(english_description)


def search_text(text):
    """Case/accent insensitive logical text, without Arabic display shaping."""
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text)).casefold()
                   if not unicodedata.combining(c))


def coverage():
    """Machine/community provenance and observable English fallbacks."""
    result = dict(_catalog().get('metadata', {}))
    result['runtime_fallbacks'] = sorted(_missing)
    result['game_missing'] = _game().get('missing', [])
    return result


def coverage_notice():
    if _language.startswith('en_'):
        return ''
    return tr('Community AI translation · Some text may remain in English.')


@lru_cache(maxsize=1)
def _windows_icu():
    """Use the OS ICU service, never redistribute a bidi implementation.

    Microsoft documents these unsuffixed exports in icu.dll (Windows 10 1903+).
    System32-only loading avoids DLL search-path injection.
    """
    import ctypes
    if sys.platform != 'win32':
        raise RuntimeError('Arabic Tk rendering requires Windows 10 version 1903 or later.')
    # Computed name prevents PyInstaller's literal-ctypes scan from bundling an
    # OS DLL. LOAD_LIBRARY_SEARCH_SYSTEM32 still restricts the actual lookup.
    library_name = ''.join(('icu', '.dll'))
    library = ctypes.CDLL(library_name, winmode=0x00000800)
    uchar = ctypes.POINTER(ctypes.c_uint16)
    error = ctypes.POINTER(ctypes.c_int32)
    library.ubidi_open.argtypes = []
    library.ubidi_open.restype = ctypes.c_void_p
    library.ubidi_close.argtypes = [ctypes.c_void_p]
    library.ubidi_close.restype = None
    library.ubidi_setPara.argtypes = [ctypes.c_void_p, uchar, ctypes.c_int32,
                                      ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint8), error]
    library.ubidi_setPara.restype = None
    library.ubidi_writeReordered.argtypes = [ctypes.c_void_p, uchar, ctypes.c_int32,
                                             ctypes.c_uint16, error]
    library.ubidi_writeReordered.restype = ctypes.c_int32
    return library


def _bidi_display(line):
    import ctypes
    if not line:
        return line
    library = _windows_icu()
    encoded = line.encode('utf-16-le')
    length = len(encoded) // 2
    source = (ctypes.c_uint16 * length).from_buffer_copy(encoded)
    output = (ctypes.c_uint16 * (length + 1))()
    error = ctypes.c_int32(0)
    bidi = library.ubidi_open()
    if not bidi:
        raise MemoryError('Windows ICU could not allocate bidi state')
    try:
        # Default-LTR discovers the first strong script, preserving mixed AD/AP,
        # proper names and digits. ICU handles mirroring and combining marks.
        library.ubidi_setPara(bidi, source, length, 0xfe, None, ctypes.byref(error))
        if error.value > 0:
            raise RuntimeError(f'Windows ICU paragraph error: {error.value}')
        written = library.ubidi_writeReordered(bidi, output, length + 1, 1 | 2 | 8,
                                               ctypes.byref(error))
        if error.value > 0 or not 0 <= written <= length:
            raise RuntimeError(f'Windows ICU reordering error: {error.value}')
        return ctypes.string_at(output, written * 2).decode('utf-16-le')
    finally:
        library.ubidi_close(bidi)


def display_text(text):
    """Shape at the Tk boundary, leaving clipboard/search logical Unicode."""
    text = str(text)
    if re.search(r'[\u0600-\u06ff]', text):
        import arabic_reshaper
        rendered = '\n'.join(_bidi_display(arabic_reshaper.reshape(line)) for line in text.split('\n'))
        if _render_sources.get(text):
            _render_sources[rendered] = _render_sources[text]
        return rendered
    return text


def _fields(text):
    try:
        return [(literal, field, spec, conversion)
                for literal, field, spec, conversion in _formatter.parse(text)]
    except ValueError:
        return []


def _templates(catalog):
    if '_compiled_templates' not in catalog:
        compiled = []
        messages = catalog.get('messages', {})
        candidates = {source.strip(): translated.strip() for source, translated in messages.items()}
        candidates.update(messages)
        for source, translated in candidates.items():
            parts = _fields(source)
            if not any(field is not None for _, field, _, _ in parts):
                continue
            if not any(literal.strip() for literal, _, _, _ in parts):
                continue
            pattern, names = [], []
            for literal, field, _, _ in parts:
                pattern.append(re.escape(literal))
                if field is not None:
                    pattern.append('(.*?)')
                    names.append(field)
            try:
                compiled.append((re.compile(''.join(pattern), re.DOTALL), names, translated,
                                 sum(len(p[0]) for p in parts)))
            except re.error:
                continue
        catalog['_compiled_templates'] = sorted(compiled, key=lambda item: item[3], reverse=True)
    return catalog['_compiled_templates']


def _name_mapping():
    game = _game()
    if '_name_mapping' not in game:
        missing = set(game.get('missing', []))
        names = {name: value for name, value in game.get('champions', {}).items()
                 if f'champions.{name}' not in missing}
        names.update({name: value.get('name', name) for name, value in game.get('traits', {}).items()
                      if isinstance(value, dict) and f'traits.{name}.name' not in missing})
        # Community names cover unpublished/English-only game locales. Honor
        # dotted missing paths instead of treating a populated English payload
        # as an official localized name.
        messages = _catalog().get('messages', {})
        for group in ('champions', 'traits'):
            for name in game.get(group, {}):
                if name not in names or names[name] == name:
                    if name in messages:
                        names[name] = messages[name]
        names = {key: value for key, value in names.items() if isinstance(value, str) and value}
        changed = {key: value for key, value in names.items() if key != value}
        pattern = (re.compile(r'(?<!\w)(' + '|'.join(re.escape(name) for name in
                   sorted(set(changed) | set(changed.values()), key=len, reverse=True)) + r')(?!\w)') if changed else None)
        game['_name_mapping'] = (names, changed, pattern)
    return game['_name_mapping']


def _replace_names(text):
    _, names, pattern = _name_mapping()
    return pattern.sub(lambda match: names.get(match[0], match[0]), text) if pattern else text


def _translate(text, depth=0):
    if (not text or _language.startswith('en_') or text == _BRAND
            or text.startswith(_BRAND + ' · v')
            or text.startswith(_BRAND + " isn't endorsed by Riot Games")
            or text == 'Developed by prokjs'):
        return text
    if depth > 8 or re.match(r'^https?://\S+$', text):
        return text
    game_names, _, _ = _name_mapping()
    if text in game_names and game_names[text] != text:
        return game_names[text]
    messages = _catalog().get('messages', {})
    if text in messages:
        return _replace_names(messages[text])
    catalog = _catalog()
    if '_stripped_messages' not in catalog:
        catalog['_stripped_messages'] = {source.strip(): translated.strip()
                                         for source, translated in messages.items()
                                         if source.strip() and source.strip() != source}
    if text in catalog['_stripped_messages']:
        return _replace_names(catalog['_stripped_messages'][text])
    # Whitespace remains structural: rich-text runs and copied paragraphs retain it.
    stripped = text.strip()
    if stripped != text and stripped:
        start = len(text) - len(text.lstrip())
        return text[:start] + _translate(stripped, depth + 1) + text[start + len(stripped):]
    for pattern, names, translated, _ in _templates(_catalog()):
        match = pattern.fullmatch(text)
        if match:
            values = {name: _translate(value, depth + 1) for name, value in zip(names, match.groups())}
            try:
                # Values already carry their source's numeric formatting. Insert
                # by parsed field name, never evaluate expressions or reformat them.
                return ''.join(_replace_names(literal) + (values[field] if field is not None else '')
                               for literal, field, _, _ in _fields(translated))
            except (KeyError, ValueError, IndexError):
                continue
    if '_fragments' not in catalog:
        catalog['_fragments'] = sorted(
            ((source, translated) for source, translated in messages.items()
             if source.strip() and source != source.strip()
             and not any(field is not None for _, field, _, _ in _fields(source))),
            key=lambda pair: len(pair[0]), reverse=True)
    for source, translated in catalog['_fragments']:
        if source[-1].isspace() and text.startswith(source):
            return _replace_names(translated) + _translate(text[len(source):], depth + 1)
        if source[0].isspace() and text.endswith(source):
            return _translate(text[:-len(source)], depth + 1) + _replace_names(translated)
    for separator in ('\n', ' · ', ' → ', ': '):
        if separator in text:
            return separator.join(_translate(part, depth + 1) for part in text.split(separator))
    replaced = _replace_names(text)
    if replaced == text and re.search(r'[A-Za-z]{2}', text):
        _missing.add(text)
    return replaced


def tr(text):
    source = str(text)
    translated = _translate(source)
    if translated != source:
        previous = _render_sources.get(translated, source)
        _render_sources[translated] = source if previous == source else None
    return translated


def _remember(state, key, current):
    previous = state.get(key)
    # A renderer may have replaced the widget's text since our last visit.
    source = (previous[0] if previous and current == previous[1]
              else _render_sources.get(current) or current)
    rendered = display_text(tr(source))
    state[key] = (source, rendered)
    return rendered


def _bind_variable(widget, state, canonical_name, combo=False):
    import tkinter as tk
    if not canonical_name:
        return
    if state.get('canonical_name') != canonical_name:
        if state.get('canonical_name') and canonical_name == str(state.get('shadow')):
            canonical_name = state['canonical_name']
        else:
            old = state.pop('variable_binding', None)
            if old:
                widget.tk.call('trace', 'remove', 'variable', old[0], 'write', old[1])
                widget.deletecommand(old[1])
            # Do not wrap someone else's Tcl variable in StringVar: its destructor
            # would unset the app-owned variable when a popup gets destroyed.
            shadow = tk.StringVar(master=widget)
            state.update(canonical_name=canonical_name, shadow=shadow, busy=False)

            def from_canonical(*_):
                if not state['busy']:
                    state['busy'] = True
                    try:
                        shadow.set(display_text(tr(widget.getvar(canonical_name))))
                    finally:
                        state['busy'] = False

            command = widget.register(from_canonical)
            widget.tk.call('trace', 'add', 'variable', canonical_name, 'write', command)
            state['variable_binding'] = (canonical_name, command)
            if combo:
                def from_display(*_):
                    if state['busy']:
                        return
                    value = shadow.get()
                    choices = state.get('values', [])
                    matches = [source for source in choices if display_text(tr(source)) == value]
                    if len(matches) == 1:
                        state['busy'] = True
                        try:
                            widget.setvar(canonical_name, matches[0])
                        finally:
                            state['busy'] = False
                shadow.trace_add('write', from_display)
            def cleanup(event):
                if event.widget is widget:
                    binding = state.pop('variable_binding', None)
                    if binding:
                        try:
                            widget.tk.call('trace', 'remove', 'variable', binding[0], 'write', binding[1])
                            widget.deletecommand(binding[1])
                        except tk.TclError:
                            pass
            widget.bind('<Destroy>', cleanup, add='+')
    state['busy'] = True
    try:
        state['shadow'].set(display_text(tr(widget.getvar(state['canonical_name']))))
        widget.configure(textvariable=state['shadow'])
    finally:
        state['busy'] = False


def localize_tree(widget):
    """Explicit, reversible Tk localization; no global monkeypatches.

    Text rich runs are deliberately untouched. Entry input is never translated.
    Canonical StringVars stay owned by the app; display-only shadows track them.
    """
    import tkinter as tk
    from tkinter import ttk
    try:
        if not widget.winfo_exists() or getattr(widget, '_preserve_text', False):
            return
        state = getattr(widget, '_localization_state', None)
        if state is None:
            state = widget._localization_state = {}
        if isinstance(widget, ttk.Combobox):
            current = tuple(str(value) for value in widget.cget('values'))
            previous = state.get('rendered_values')
            if current != previous:
                state['values'] = list(current)
            rendered = tuple(display_text(tr(value)) for value in state.get('values', []))
            state['rendered_values'] = rendered
            index = widget.current()
            widget.configure(values=rendered)
            variable = str(widget.cget('textvariable'))
            if variable:
                _bind_variable(widget, state, variable, combo=True)
            elif index >= 0:
                widget.current(index)
        elif not isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, tk.Spinbox, ttk.Spinbox)):
            options = widget.keys()
            variable = str(widget.cget('textvariable')) if 'textvariable' in options else ''
            if variable:
                _bind_variable(widget, state, variable)
            elif 'text' in options:
                current = str(widget.cget('text'))
                widget.configure(text=_remember(state, 'text', current))
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            widget.title(_remember(state, 'title', widget.title()))
        if isinstance(widget, ttk.Notebook):
            for tab in widget.tabs():
                widget.tab(tab, text=_remember(state, ('tab', tab), widget.tab(tab, 'text')))
        if isinstance(widget, ttk.Treeview):
            for column in ('#0', *widget['columns']):
                widget.heading(column, text=_remember(state, ('heading', column), widget.heading(column, 'text')))
        if isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                if widget.type(item) == 'text':
                    widget.itemconfigure(item, text=_remember(state, ('canvas', item), widget.itemcget(item, 'text')))
        for child in widget.winfo_children():
            localize_tree(child)
    except tk.TclError:
        # Renderers can destroy children synchronously during Tk variable callbacks.
        if widget.winfo_exists():
            raise
