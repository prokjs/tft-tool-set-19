"""Sourced role parsing and metadata-preserving champion refresh regressions."""
import copy
import json
from pathlib import Path
import unittest

from bs4 import BeautifulSoup
from champion_info import is_tank

from tools.update_champion_roles import apply_roles, role_fields


def row(name, slug, role, unlockable=False):
    prefix = '<span class="visually-hidden">Unlockable: </span>' if unlockable else ''
    return (f'<tr id="{slug}"><th class="set-roster-name"><a href="/sets/19/units/{slug}">'
            f'<span><img alt=""/>{prefix}</span>{name}</a></th>'
            '<td data-label="Traits"><a>Bodyguard</a></td>'
            '<td data-label="Effects">Healing</td>'
            f'<td data-label="Role">{role}</td><td data-label="Unlock">Level 7</td></tr>')


def section(cost, *rows):
    return (f'<section class="set-roster-section" aria-labelledby="cost-{cost}">'
            '<table><tbody>' + ''.join(rows) + '</tbody></table></section>')


class ChampionRoleParserTests(unittest.TestCase):
    def test_complete_tank_and_non_tank_labels_come_from_role_cell(self):
        cases = (
            ('Ekko', 'ekko', 'Magic Tank', True),
            ('Garen', 'garen', 'Attack Tank', True),
            ('Leona', 'leona', 'Magic Tank', True),
            ('Alistar', 'alistar', 'Magic Tank', True),
            ('Dr. Mundo', 'dr-mundo', 'Magic Tank', True),
            ('Jax', 'jax', 'Magic Fighter', False),
            ('Graves', 'graves', 'Attack Fighter', False),
            ('Akali (K/DA)', 'akali-k-da', 'Attack Assassin', False),
            ('Ahri', 'ahri', 'Magic Caster', False),
            ('Jhin', 'jhin', 'Attack Specialist', False),
        )
        for name, slug, label, tank in cases:
            with self.subTest(champion=name):
                fields = role_fields(BeautifulSoup(row(name, slug, label), 'html.parser').tr)
                self.assertEqual(fields, {'role': label,
                                         'role_source': f'https://tft.ninja/sets/19/units/{slug}'})
                # Role wins even when traits suggest a different frontline role.
                champion = {'name': name, 'traits': ['Bodyguard'] if not tank else []}
                champion.update(fields)
                self.assertEqual(is_tank(champion), tank)

    def test_tank_predicate_requires_a_role_token_not_traits_or_substrings(self):
        for role in ('Magic Tank', 'Attack Tank', 'magic TANK'):
            with self.subTest(role=role):
                self.assertTrue(is_tank({'role': role, 'traits': []}))
        for role in ('Magic Fighter', 'Attack Fighter', 'Attack Specialist', 'Tanker', 'AntiTank', ''):
            with self.subTest(role=role):
                self.assertFalse(is_tank({'role': role, 'traits': ['Bodyguard', 'Juggernaut', 'Frontman']}))
        self.assertFalse(is_tank({'traits': ['Bodyguard']}))

    def test_hidden_unlock_prefix_and_both_canonical_akali_names(self):
        names = ('Akali (K/DA)', 'Akali (True Damage)')
        data = {'champions': [{'name': name, 'cost': 4} for name in names]}
        apply_roles(data, section(4,
                                 row(names[0], 'akali-k-da', 'Attack Assassin', True),
                                 row(names[1], 'akali-true-damage', 'Magic Fighter', True)))
        self.assertEqual([champion['name'] for champion in data['champions']], list(names))
        self.assertEqual(data['champions'][0]['role'], 'Attack Assassin')
        self.assertEqual(data['champions'][1]['role'], 'Magic Fighter')

    def test_only_role_fields_change_even_when_source_unlock_flag_is_older(self):
        data = {'version': 'bundled snapshot', 'sources': [{'url': 'https://example.org'}],
                'traits': [{'name': 'Bodyguard', 'breakpoints': [2, 4, 6],
                            'description': 'Keep this effect.', 'description_source': 'https://example.org/trait'}],
                'collab_groups': [{'name': 'Group 1', 'champions': ['Nasus']}],
                'champions': [{'name': 'Nasus', 'cost': 2, 'traits': ['Bodyguard'], 'unlockable': False,
                               'unlock_note': 'Newer source places him in the starting shop.', 'custom': {'keep': [1]}},
                              {'name': 'Alistar', 'cost': 4, 'traits': ['Country'], 'unlockable': True,
                               'unlock_condition': 'A Chain Vest equipped to a Caster or Marksman.',
                               'unlock_level': '7', 'unlock_source': 'https://example.org/unlock', 'slots': 1}]}
        before = copy.deepcopy(data)
        apply_roles(data, section(2, row('Nasus', 'nasus', 'Magic Tank', True))
                    + section(4, row('Alistar', 'alistar', 'Magic Tank', True)))
        for champion in data['champions']:
            self.assertEqual(champion.pop('role'), 'Magic Tank')
            self.assertTrue(champion.pop('role_source').startswith('https://tft.ninja/sets/19/units/'))
        self.assertEqual(data, before)

    def test_roster_and_cost_mismatches_leave_all_metadata_untouched(self):
        data = {'champions': [{'name': 'Garen', 'cost': 4, 'role': 'Previous role'},
                              {'name': 'Leona', 'cost': 4, 'role': 'Previous role'}]}
        cases = (
            section(4, row('Garen', 'garen', 'Attack Tank')),
            section(4, row('Garen', 'garen', 'Attack Tank'), row('Leona', 'leona', 'Magic Tank'),
                    row('Ahri', 'ahri', 'Magic Caster')),
            section(4, row('Garen', 'garen', 'Attack Tank'))
            + section(5, row('Leona', 'leona', 'Magic Tank')),
        )
        for html in cases:
            with self.subTest(html=html):
                before = copy.deepcopy(data)
                with self.assertRaisesRegex(ValueError, 'roster differs|cost changed'):
                    apply_roles(data, html)
                self.assertEqual(data, before)

    def test_duplicate_names_and_slugs_cannot_overwrite_parsed_roles(self):
        data = {'champions': [{'name': 'Garen', 'cost': 4}, {'name': 'Leona', 'cost': 4}]}
        cases = (
            section(4, row('Garen', 'garen', 'Attack Tank'), row('Garen', 'garen-copy', 'Magic Tank')),
            section(4, row('Garen', 'garen', 'Attack Tank'), row('Leona', 'garen', 'Magic Tank')),
        )
        for html in cases:
            with self.subTest(html=html):
                before = copy.deepcopy(data)
                with self.assertRaisesRegex(ValueError, 'Duplicate'):
                    apply_roles(data, html)
                self.assertEqual(data, before)

    def test_missing_empty_or_non_https_role_sources_fail_before_mutation(self):
        good = row('Garen', 'garen', 'Attack Tank')
        cases = (
            good.replace('<td data-label="Role">Attack Tank</td>', ''),
            good.replace('Attack Tank', '   '),
            good.replace('/sets/19/units/garen', 'http://tft.ninja/sets/19/units/garen'),
            good.replace('/sets/19/units/garen', 'https://other.example/sets/19/units/garen'),
            good.replace('id="garen"', ''),
        )
        for html in cases:
            with self.subTest(html=html):
                data = {'champions': [{'name': 'Garen', 'cost': 4, 'role': 'Previous role'}]}
                before = copy.deepcopy(data)
                with self.assertRaises(ValueError):
                    apply_roles(data, section(4, html))
                self.assertEqual(data, before)

    def test_bundled_roster_has_sourced_roles_without_losing_unlocks_or_effects(self):
        data = json.loads((Path(__file__).parent / 'data' / 'set19.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['champions']), 94)
        self.assertEqual(sum(champion['unlockable'] for champion in data['champions']), 32)
        self.assertEqual(len(data['traits']), 47)
        for champion in data['champions']:
            with self.subTest(champion=champion['name']):
                self.assertIsInstance(champion['role'], str)
                self.assertTrue(champion['role'].strip())
                self.assertTrue(champion['role_source'].startswith('https://tft.ninja/sets/19/units/'))
        for trait in data['traits']:
            self.assertTrue(trait['description'].strip())
            self.assertTrue(trait['description_source'].startswith('https://'))
        tanks = {champion['name'] for champion in data['champions']
                 if champion['cost'] == 4 and champion['role'].endswith(' Tank')}
        self.assertEqual(tanks, {'Alistar', 'Garen', 'Leona', 'Shyvana', 'Thresh', 'Wukong', 'Yorick', 'Zac'})


if __name__ == '__main__':
    unittest.main()
