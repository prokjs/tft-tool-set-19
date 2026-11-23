"""Offline catalog contracts and safe source joining; no network requests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools import update_localized_game_data as localized

ROOT = Path(__file__).resolve().parent


class BundledGameCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = json.loads((ROOT / 'data' / 'set19.json').read_text(encoding='utf-8'))
        cls.catalogs = {
            locale: json.loads((ROOT / 'data' / 'locales_game' / f'{locale}.json').read_text(encoding='utf-8'))
            for locale in localized.LOCALES
        }
        cls.champions = {champion['name'] for champion in cls.base['champions']}
        cls.traits = {trait['name'] for trait in cls.base['traits']}

    def test_all_28_supported_locales_have_offline_catalogs(self):
        self.assertEqual(len(localized.LOCALES), 28)
        self.assertEqual(len(set(localized.LOCALES)), 28)
        for locale, catalog in self.catalogs.items():
            self.assertEqual(catalog['locale'], locale)
            self.assertIsInstance(catalog['missing'], list)
            self.assertEqual(catalog['missing'], sorted(set(catalog['missing'])))

    def test_canonical_keys_and_missing_fields_are_complete(self):
        for catalog in self.catalogs.values():
            self.assertLessEqual(set(catalog['champions']), self.champions)
            self.assertLessEqual(set(catalog['traits']), self.traits)
            for name in self.champions - catalog['champions'].keys():
                self.assertIn(f'champions.{name}', catalog['missing'])
            for name in self.traits - catalog['traits'].keys():
                self.assertIn(f'traits.{name}.name', catalog['missing'])
                self.assertIn(f'traits.{name}.description', catalog['missing'])
            for trait in catalog['traits'].values():
                self.assertEqual(set(trait), {'name', 'description'})
            self.assertNotIn('stats', catalog)
            self.assertNotIn('cost', catalog)
            self.assertNotIn('attack_range', catalog)

    def test_english_names_and_variant_disambiguation(self):
        english = self.catalogs['en_US']
        self.assertEqual(english['champions'], {name: name for name in self.champions})
        self.assertEqual(set(english['traits']), self.traits)
        for catalog in self.catalogs.values():
            first = catalog['champions']['Akali (K/DA)']
            second = catalog['champions']['Akali (True Damage)']
            self.assertNotEqual(first, second)
            self.assertIn('(', first)
            self.assertIn('(', second)

    def test_exact_range_provenance_ids_are_retained(self):
        for catalog in self.catalogs.values():
            ids = catalog['provenance']['champion_api_keys']
            for champion in self.base['champions']:
                self.assertIn(f"#/{ids[champion['name']]}/stats/offense/range",
                              champion['attack_range_source'])
            self.assertEqual(set(catalog['provenance']['trait_api_keys']), self.traits)

    def test_official_names_are_id_joined_for_all_28_languages(self):
        for locale, catalog in self.catalogs.items():
            provenance = catalog['provenance']
            self.assertEqual(catalog['champion_name_source_locale'], locale)
            self.assertEqual(len(provenance['official_champion_ids']), 89)
            for name, key in provenance['official_champion_ids'].items():
                source = provenance['champion_name_source_by_key'][name]
                self.assertTrue(source.startswith('https://ddragon.leagueoflegends.com/cdn/'))
                self.assertTrue(source.endswith(f'/data/{locale}/champion.json#/data/{key}/name'))

    def test_regional_aliases_do_not_claim_regional_ies_sources(self):
        for locale, alias in localized.REGIONAL_ALIASES.items():
            catalog = self.catalogs[locale]
            self.assertEqual(catalog['source_locale'], alias)
            self.assertEqual(catalog['coverage']['source_locale_by_endpoint']['traits'], alias)
            self.assertIn(localized.source_url(alias, 'traits'), catalog['sources'])
            self.assertIn('traits', catalog['coverage']['unpublished_endpoints'])

    def test_unpublished_traits_are_not_fabricated(self):
        for locale in ('ar_AE', 'hu_HU', 'id_ID', 'ro_RO', 'th_TH'):
            catalog = self.catalogs[locale]
            self.assertEqual(catalog['traits'], {})
            self.assertEqual(len(catalog['champions']), 89)
            for name in ('Kobuko', 'Tibbers', 'Alune', 'Baron Nashor', 'Smeech'):
                self.assertIn(f'champions.{name}', catalog['missing'])

    def test_english_pbe_payload_is_explicitly_unverified(self):
        greek = self.catalogs['el_GR']
        self.assertTrue(greek['coverage']['entirely_english_trait_payload'])
        for name in self.traits:
            self.assertIn(f'traits.{name}.name', greek['missing'])
            self.assertIn(f'traits.{name}.description', greek['missing'])
        french = self.catalogs['fr_FR']
        self.assertIn('traits.Country.description', french['missing'])
        self.assertNotEqual(french['traits']['Arcanist']['description'],
                            self.catalogs['en_US']['traits']['Arcanist']['description'])

    def test_plain_text_and_honest_legal_provenance(self):
        for catalog in self.catalogs.values():
            for trait in catalog['traits'].values():
                self.assertNotRegex(trait['description'] or '', r'<[^>]+>|%i:')
            for source in catalog['sources']:
                self.assertTrue(source.startswith('https://'))
                self.assertRegex(catalog['provenance']['source_sha256_normalized_json'][source], r'^[0-9a-f]{64}$')
            provenance = catalog['provenance']
            self.assertIn('not rights-cleared', provenance['content_rights'])
            self.assertIn('No official endorsement', provenance['quality_notice'])
            if catalog['traits']:
                self.assertTrue(provenance['third_party_pbe_review_required_before_public_ad_launch'])


class LocalizedSourceImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = json.loads((ROOT / 'data' / 'set19.json').read_text(encoding='utf-8'))
        english = json.loads((ROOT / 'data' / 'locales_game' / 'en_US.json').read_text(encoding='utf-8'))
        cls.units = {}
        for champion in cls.base['champions']:
            key = english['provenance']['champion_api_keys'][champion['name']]
            cls.units[key] = {
                'apiKey': key, 'cost': champion['cost'],
                'name': 'Akali' if key in localized.AKALI_VARIANTS else champion['name'],
            }
        cls.traits = {
            key: {'apiKey': key, 'name': name, 'description': '', 'bonuses': []}
            for name, key in english['provenance']['trait_api_keys'].items()
        }

    def test_full_roster_join_does_not_mutate_snapshot_or_sources(self):
        base, units, traits = copy.deepcopy((self.base, self.units, self.traits))
        before = copy.deepcopy((base, units, traits))
        champions, trait_ids = localized.canonical_ids(base, units, traits)
        self.assertEqual(len(champions), 94)
        self.assertEqual(len(trait_ids), 47)
        self.assertEqual((base, units, traits), before)

    def test_rejects_missing_extra_or_misidentified_english_records(self):
        for mutation in ('missing', 'extra', 'identity', 'cost'):
            units = copy.deepcopy(self.units)
            key = next(iter(units))
            if mutation == 'missing':
                del units[key]
            elif mutation == 'extra':
                units['unknown'] = {'apiKey': 'unknown', 'name': 'unknown', 'cost': 1}
            elif mutation == 'identity':
                units[key]['apiKey'] = 'wrong'
            else:
                units[key]['cost'] += 1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                localized.canonical_ids(self.base, units, self.traits)

    def test_rejects_duplicate_json_and_bad_range_provenance(self):
        with self.assertRaises(ValueError):
            localized.decode_json('{"same": 1, "same": 2}')
        base = copy.deepcopy(self.base)
        base['champions'][0]['attack_range_source'] = 'https://untrusted.example/#/key/stats/offense/range'
        with self.assertRaises(ValueError):
            localized.canonical_ids(base, self.units, self.traits)

    def test_strips_game_markup_without_inventing_numbers(self):
        text = '<bright>12%</bright><br>%i:scaleAP% &amp; <magicdamage>25</magicdamage><script>hidden</script>'
        self.assertEqual(localized.plain_text(text), '12%\n& 25')
        parts = localized.description_parts({
            'description': '<rules>Localized description.</rules>',
            'bonuses': [{'needed': 2, 'effect': '<bright>18%</bright> &amp; ?'}],
        })
        self.assertEqual(parts, [('description', 'Localized description.'),
                                 ('bonuses/0/effect', '2 18% & ?')])

    def test_archive_missing_input_is_not_implicitly_unpublished(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Missing archive'):
                localized.load_sources(Path(directory))

    def test_official_name_join_keeps_akali_variants_and_tft_only_missing(self):
        ids = {'Akali (K/DA)': 'DA_AkaliKDA19',
               'Akali (True Damage)': 'DA_Akali_TrueDamage19',
               'Tibbers': 'DA_Tibbers19'}
        official = {'en_US': {'data': {'Akali': {'id': 'Akali', 'name': 'Akali'}}}}
        self.assertEqual(localized.official_champion_ids(ids, official),
                         {'Akali (K/DA)': 'Akali', 'Akali (True Damage)': 'Akali'})


if __name__ == '__main__':
    unittest.main()
