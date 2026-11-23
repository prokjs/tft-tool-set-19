"""Offline localization contracts; run with unittest after integration is complete."""
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import unittest

import localization as loc
from tools import update_locales


class CatalogTests(unittest.TestCase):
    def tearDown(self):
        loc.set_language('en_US')

    def test_all_riot_locales_are_bundled_and_complete(self):
        self.assertEqual(len(loc.available_languages()), 28)
        sources = json.loads((loc._DATA / 'locales_ui/sources.json').read_text(encoding='utf-8'))['messages']
        for locale, native in loc.available_languages():
            with self.subTest(locale=locale):
                self.assertTrue(native)
                catalog = json.loads((loc._DATA / 'locales_ui' / f'{locale}.json').read_text(encoding='utf-8'))
                self.assertEqual(catalog['locale'], locale)
                self.assertEqual(set(catalog['messages']), set(sources))
                self.assertEqual(catalog['missing'], [])
                self.assertEqual(catalog['metadata']['missing_count'], 0)
                self.assertFalse(catalog['metadata']['reviewed'])
                update_locales.validate_messages(catalog['messages'])
                if not locale.startswith('en_'):
                    self.assertEqual(catalog['metadata']['kind'], 'community-AI-translation')
                    self.assertNotEqual(catalog['messages']['Find teams'], 'Find teams')

    def test_trait_descriptions_unlocks_and_roles_are_sources(self):
        data = json.loads((loc._DATA / 'set19.json').read_text(encoding='utf-8'))
        catalog = json.loads((loc._DATA / 'locales_ui/en_US.json').read_text(encoding='utf-8'))['messages']
        for trait in data['traits']:
            self.assertIn(trait['description'], catalog)
        for champion in data['champions']:
            if champion.get('unlockable'):
                self.assertIn(champion['unlock_condition'], catalog)
            self.assertIn(champion['role'], catalog)

    def test_selection_validation_and_brand_stability(self):
        with self.assertRaises(ValueError):
            loc.set_language('not_a_locale')
        for locale, _ in loc.available_languages():
            loc.set_language(locale)
            self.assertEqual(loc.get_language(), locale)
            self.assertEqual(loc.tr("prokjs's Set 19 TFT tool"), "prokjs's Set 19 TFT tool")
            self.assertEqual(loc.tr('Developed by prokjs'), 'Developed by prokjs')
            self.assertEqual(loc.tr('https://example.com/game'), 'https://example.com/game')

    def test_placeholder_import_rejects_changes(self):
        with self.assertRaises(ValueError):
            update_locales.validate_messages({'Level {v0}': 'Niveau {v1}'})
        with self.assertRaises(ValueError):
            update_locales.validate_messages({'Level {v0}': 'Niveau'})
        with self.assertRaises(ValueError):
            update_locales.validate_messages({'Find teams': ''})

    def test_search_normalization(self):
        self.assertEqual(loc.search_text('ÉVELYNN'), 'evelynn')
        self.assertEqual(loc.search_text('阿狸'), '阿狸')

    def test_arabic_display_is_shaped_but_translation_is_logical(self):
        loc.set_language('ar_AE')
        text = loc.tr('Find teams')
        shaped = loc.display_text(text)
        self.assertNotEqual(text, shaped)
        self.assertEqual(loc.tr('Find teams'), text)
        self.assertEqual(loc.display_text('AD / AP 123'), 'AD / AP 123')


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.previous_catalog = loc._cache.get('fr_FR')
        self.previous_game = loc._game_cache.get('fr_FR')
        loc._cache['fr_FR'] = {'messages': {
            'Find teams': 'Trouver des équipes', 'High value': 'Haute valeur',
            'More traits': 'Plus de traits', 'Level {v0} · {v1}': 'Niveau {v0} · {v1}',
            '{v0} active': '{v0} actifs', 'Role': 'Rôle', 'Tank': 'Tank',
            'Unlocks ({v0})': 'Déblocages ({v0})',
        }, 'metadata': {'kind': 'community-AI-translation'}}
        loc._game_cache['fr_FR'] = {
            'champions': {'Ahri': 'Ahri française', 'Vi': 'Vi française'},
            'traits': {'Arcanist': {'name': 'Arcaniste', 'description': None}},
        }
        loc.set_language('fr_FR')

    def tearDown(self):
        loc.set_language('en_US')
        if self.previous_catalog is None:
            loc._cache.pop('fr_FR', None)
        else:
            loc._cache['fr_FR'] = self.previous_catalog
        if self.previous_game is None:
            loc._game_cache.pop('fr_FR', None)
        else:
            loc._game_cache['fr_FR'] = self.previous_game

    def test_dynamic_templates_recursively_translate_field_values(self):
        self.assertEqual(loc.tr('Level 7 · 3 active'), 'Niveau 7 · 3 actifs')
        self.assertEqual(loc.tr('Unlocks (12)'), 'Déblocages (12)')
        self.assertEqual(loc.tr('Role: Tank'), 'Rôle: Tank')

    def test_names_replace_on_boundaries_not_substrings(self):
        self.assertEqual(loc.tr('Ahri · Arcanist'), 'Ahri française · Arcaniste')
        self.assertEqual(loc.tr('Violet'), 'Violet')
        self.assertEqual(loc.localized_champion('Ahri'), 'Ahri française')
        self.assertEqual(loc.localized_trait('Arcanist'), 'Arcaniste')
        self.assertEqual(loc.localized_trait_description('Arcanist', 'Find teams'), 'Trouver des équipes')

    def test_fallback_is_observable_and_english_is_reversible(self):
        self.assertEqual(loc.tr('Uncatalogued phrase'), 'Uncatalogued phrase')
        self.assertIn('Uncatalogued phrase', loc.coverage()['runtime_fallbacks'])
        loc.set_language('en_US')
        self.assertEqual(loc.tr('Find teams'), 'Find teams')
        self.assertEqual(loc.localized_champion('Ahri'), 'Ahri')

    def test_structural_whitespace_remains(self):
        self.assertEqual(loc.tr('\n  Find teams\n'), '\n  Trouver des équipes\n')


class TkLocalizationTests(TranslationTests):
    def setUp(self):
        super().setUp()
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError as error:
            super().tearDown()
            self.skipTest(f'Tk display unavailable: {error}')

    def tearDown(self):
        self.root.destroy()
        super().tearDown()

    def test_repeated_tree_translation_and_switch_back(self):
        label = ttk.Label(self.root, text='Find teams')
        loc.localize_tree(self.root)
        self.assertEqual(label.cget('text'), 'Trouver des équipes')
        loc.localize_tree(self.root)
        self.assertEqual(label.cget('text'), 'Trouver des équipes')
        label.configure(text='More traits')
        loc.localize_tree(self.root)
        self.assertEqual(label.cget('text'), 'Plus de traits')
        loc.set_language('en_US')
        loc.localize_tree(self.root)
        self.assertEqual(label.cget('text'), 'More traits')

    def test_combobox_preserves_canonical_variable_and_callbacks(self):
        canonical = tk.StringVar(self.root, value='High value')
        combo = ttk.Combobox(self.root, textvariable=canonical, values=['High value', 'More traits'], state='readonly')
        seen = []
        combo.bind('<<ComboboxSelected>>', lambda event: seen.append(canonical.get()))
        loc.localize_tree(self.root)
        self.assertEqual(combo.get(), 'Haute valeur')
        self.assertEqual(canonical.get(), 'High value')
        combo.current(1)
        combo.event_generate('<<ComboboxSelected>>')
        self.assertEqual(canonical.get(), 'More traits')
        self.assertEqual(seen, ['More traits'])
        canonical.set('High value')
        self.assertEqual(combo.get(), 'Haute valeur')
        loc.set_language('en_US')
        loc.localize_tree(self.root)
        self.assertEqual(combo.get(), 'High value')
        self.assertEqual(canonical.get(), 'High value')

    def test_status_shadow_tracks_future_writes_without_mutating_source(self):
        status = tk.StringVar(self.root, value='Find teams')
        label = ttk.Label(self.root, textvariable=status)
        loc.localize_tree(self.root)
        self.assertEqual(status.get(), 'Find teams')
        self.assertEqual(self.root.getvar(label.cget('textvariable')), 'Trouver des équipes')
        status.set('More traits')
        self.assertEqual(status.get(), 'More traits')
        self.assertEqual(self.root.getvar(label.cget('textvariable')), 'Plus de traits')
        label.destroy()
        self.assertEqual(status.get(), 'More traits')
        status.set('Find teams')
        self.assertEqual(status.get(), 'Find teams')

    def test_preserves_rich_tags_user_entry_and_legal_optout(self):
        text = tk.Text(self.root)
        text.insert('end', 'Find teams', 'bold')
        text.tag_configure('bold', font=('Segoe UI', 10, 'bold'))
        entry = ttk.Entry(self.root)
        entry.insert(0, 'Find teams')
        legal = ttk.Label(self.root, text='Find teams')
        legal._preserve_text = True
        loc.localize_tree(self.root)
        self.assertEqual(text.get('1.0', 'end-1c'), 'Find teams')
        self.assertEqual(tuple(map(str, text.tag_ranges('bold'))), ('1.0', '1.10'))
        self.assertEqual(entry.get(), 'Find teams')
        self.assertEqual(legal.cget('text'), 'Find teams')

    def test_canvas_notebook_heading_reversibility(self):
        canvas = tk.Canvas(self.root)
        item = canvas.create_text(10, 10, text='Find teams')
        notebook = ttk.Notebook(self.root)
        tab = ttk.Frame(notebook)
        notebook.add(tab, text='Unlocks (3)')
        tree = ttk.Treeview(self.root, columns=['role'])
        tree.heading('role', text='Role')
        loc.localize_tree(self.root)
        self.assertEqual(canvas.itemcget(item, 'text'), 'Trouver des équipes')
        self.assertEqual(notebook.tab(tab, 'text'), 'Déblocages (3)')
        self.assertEqual(tree.heading('role', 'text'), 'Rôle')
        loc.set_language('en_US')
        loc.localize_tree(self.root)
        self.assertEqual(canvas.itemcget(item, 'text'), 'Find teams')
        self.assertEqual(notebook.tab(tab, 'text'), 'Unlocks (3)')
        self.assertEqual(tree.heading('role', 'text'), 'Role')


if __name__ == '__main__':
    unittest.main()
