"""Consumer-visible trait text, source consistency, and safe refresh regressions."""
import copy
import json
from pathlib import Path
import unittest

from tools.update_trait_descriptions import apply_descriptions, description_fields
from bs4 import BeautifulSoup


def card(name='Arcanist', points=(2, 4), effect='Your team gains Ability Power.'):
    rows = ''.join(f'<li><span class="set-bp-chip">{point}</span><span>{amount} Ability Power</span></li>'
                   for point, amount in zip(points, (15, 35)))
    return f'<section class="set-trait"><h3 class="set-trait-name"><a href="/sets/19/traits/arcanist">{name}</a></h3><div class="set-trait-text"><p>{effect}</p><ul>{rows}</ul></div></section>'


class TraitDescriptionTests(unittest.TestCase):
    def test_effects_and_breakpoint_numbers_survive_markup(self):
        data = {'traits': [{'name': 'Arcanist', 'breakpoints': [2, 4], 'unique': False}],
                'champions': [{'name': 'Sona', 'unlock_condition': 'Become inspired 15 times.'}]}
        champion_data = copy.deepcopy(data['champions'])
        apply_descriptions(data, card())
        description = data['traits'][0]['description']
        self.assertIn('Your team gains Ability Power.', description)
        self.assertIn('2 15 Ability Power', description)
        self.assertIn('4 35 Ability Power', description)
        self.assertTrue(data['traits'][0]['description_source'].endswith('/traits/arcanist'))
        self.assertEqual(data['champions'], champion_data)

    def test_nested_effect_paragraph_is_not_duplicated(self):
        html = '<section><h3 class="set-trait-name"><a href="/sets/19/traits/healer">Healer</a></h3><div class="set-trait-text"><ul><li><p>Heal <b>20%</b> Health.</p></li></ul></div></section>'
        fields = description_fields(BeautifulSoup(html, 'html.parser').section)
        self.assertEqual(fields['description'].count('20%'), 1)
        self.assertIn('Heal 20% Health.', fields['description'])

    def test_empty_effect_body_does_not_replace_snapshot(self):
        data = {'traits': [{'name': 'Arcanist', 'breakpoints': [2, 4], 'description': 'Previous valid effect.'}]}
        before = copy.deepcopy(data)
        empty = '<section class="set-trait"><h3 class="set-trait-name"><a href="/traits/arcanist">Arcanist</a></h3><div class="set-trait-text"></div></section>'
        with self.assertRaisesRegex(ValueError, 'Empty trait effect'):
            apply_descriptions(data, empty)
        self.assertEqual(data, before)

    def test_changed_breakpoints_cannot_mix_with_older_effect_snapshot(self):
        data = {'traits': [{'name': 'Arcanist', 'breakpoints': [2, 4], 'description': 'Previous valid effect.'}]}
        before = copy.deepcopy(data)
        with self.assertRaisesRegex(ValueError, 'breakpoints changed'):
            apply_descriptions(data, card(points=(2, 6)))
        self.assertEqual(data, before)

    def test_every_bundled_trait_has_real_effect_text_and_source(self):
        data = json.loads((Path(__file__).parent / 'data' / 'set19.json').read_text(encoding='utf-8'))
        for trait in data['traits']:
            with self.subTest(trait=trait['name']):
                self.assertRegex(trait['description'], '[A-Za-z]')
                self.assertTrue(trait['description_source'].startswith('https://tft.ninja/sets/19/traits/'))


if __name__ == '__main__':
    unittest.main()
