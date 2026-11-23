"""Verified base ranges, exact-roster imports and atomic offline refresh coverage."""
import copy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import update_champion_ranges as ranges
from tools import update_data

# Captured 2026-11-20 from SOURCE_URL stats.offense.range; no role-based inference.
# Source key, displayed name, source slug, shop cost, attack range in hexes.
VERIFIED_UNITS = (('DA_Ahri19', 'Ahri', 'ahri', 4, 4),
 ('DA_AkaliKDA19', 'Akali', 'akali-kda', 5, 1),
 ('DA_Akali_TrueDamage19', 'Akali', 'akali-true-damage', 5, 1),
 ('DA_Alistar19', 'Alistar', 'alistar', 4, 1),
 ('DA_Alune19', 'Alune', 'alune', 4, 4),
 ('DA_Amumu19', 'Amumu', 'amumu', 3, 1),
 ('DA_Annie19', 'Annie', 'annie', 2, 1),
 ('DA_Aphelios19', 'Aphelios', 'aphelios', 5, 5),
 ('DA_Ashe19', 'Ashe', 'ashe', 2, 5),
 ('DA_Aurora19', 'Aurora', 'aurora', 4, 4),
 ('DA_Bard19', 'Bard', 'bard', 3, 4),
 ('DA_BaronNashor19', 'Baron Nashor', 'baron-nashor', 7, 2),
 ('DA_Braum19', 'Braum', 'braum', 3, 1),
 ('DA_Briar19', 'Briar', 'briar', 3, 1),
 ('DA_Camille19', 'Camille', 'camille', 2, 1),
 ('DA_DrMundo19', 'Dr. Mundo', 'dr-mundo', 5, 1),
 ('DA_Draven19', 'Draven', 'draven', 3, 4),
 ('DA_Ekko19', 'Ekko', 'ekko', 3, 1),
 ('DA_Elise19', 'Elise', 'elise', 1, 1),
 ('DA_Evelynn19', 'Evelynn', 'evelynn', 3, 1),
 ('DA_Ezreal19', 'Ezreal', 'ezreal', 2, 4),
 ('DA_Galio19', 'Galio', 'galio', 3, 1),
 ('DA_Garen19', 'Garen', 'garen', 4, 1),
 ('DA_Gnar19', 'Gnar', 'gnar', 1, 4),
 ('DA_Gragas19', 'Gragas', 'gragas', 2, 1),
 ('DA_Graves19', 'Graves', 'graves', 4, 2),
 ('DA_Gwen19', 'Gwen', 'gwen', 5, 2),
 ('DA_Illaoi19', 'Illaoi', 'illaoi', 2, 1),
 ('DA_Janna19', 'Janna', 'janna', 5, 4),
 ('DA_Jax19', 'Jax', 'jax', 2, 1),
 ('DA_Jhin19', 'Jhin', 'jhin', 4, 5),
 ('DA_Jinx19', 'Jinx', 'jinx', 4, 4),
 ('DA_KSante19', "K'Sante", 'ksante', 3, 1),
 ('DA_Kaisa19', "Kai'Sa", 'kaisa', 3, 5),
 ('DA_Karthus19', 'Karthus', 'karthus', 2, 4),
 ('DA_Katarina19', 'Katarina', 'katarina', 1, 1),
 ('DA_Kayle19', 'Kayle', 'kayle', 4, 4),
 ('DA_Kayn19', 'Kayn', 'kayn', 1, 1),
 ('DA_Kennen19', 'Kennen', 'kennen', 1, 1),
 ('DA_Kobuko19', 'Kobuko', 'kobuko', 1, 1),
 ('DA_LeeSin19', 'Lee Sin', 'lee-sin', 5, 1),
 ('DA_Leona19', 'Leona', 'leona', 4, 1),
 ('DA_Lillia19', 'Lillia', 'lillia', 1, 1),
 ('DA_Locke19', 'Locke', 'locke', 4, 1),
 ('DA_Lucian19', 'Lucian', 'lucian', 2, 4),
 ('DA_Lulu19', 'Lulu', 'lulu', 2, 4),
 ('DA_Lux19', 'Lux', 'lux', 2, 4),
 ('DA_Malzahar19', 'Malzahar', 'malzahar', 3, 4),
 ('DA_Mel19', 'Mel', 'mel', 4, 4),
 ('DA_MissFortune19', 'Miss Fortune', 'miss-fortune', 5, 4),
 ('DA_Mordekaiser19', 'Mordekaiser', 'mordekaiser', 5, 1),
 ('DA_Nasus19', 'Nasus', 'nasus', 2, 1),
 ('DA_Neeko19', 'Neeko', 'neeko', 1, 4),
 ('DA_Nidalee19', 'Nidalee', 'nidalee', 5, 6),
 ('DA_Nunu19', 'Nunu & Willump', 'nunu-and-willump', 5, 1),
 ('DA_Olaf19', 'Olaf', 'olaf', 1, 1),
 ('DA_Pyke19', 'Pyke', 'pyke', 5, 1),
 ('DA_Qiyana19', 'Qiyana', 'qiyana', 2, 1),
 ('DA_Rakan19', 'Rakan', 'rakan', 7, 2),
 ('DA_Rell19', 'Rell', 'rell', 5, 1),
 ('DA_RenataGlasc19', 'Renata Glasc', 'renata-glasc', 2, 4),
 ('DA_Renekton19', 'Renekton', 'renekton', 2, 1),
 ('DA_Samira19', 'Samira', 'samira', 2, 4),
 ('DA_Senna19', 'Senna', 'senna', 3, 4),
 ('DA_SeraphineIndie19', 'Seraphine', 'seraphine', 2, 4),
 ('DA_Sett19', 'Sett', 'sett', 1, 1),
 ('DA_Shyvana19', 'Shyvana', 'shyvana', 4, 1),
 ('DA_Smeech19', 'Smeech', 'smeech', 7, 1),
 ('DA_Sona19', 'Sona', 'sona', 7, 10),
 ('DA_Swain19', 'Swain', 'swain', 3, 1),
 ('DA_Syndra19', 'Syndra', 'syndra', 3, 4),
 ('DA_Taliyah19', 'Taliyah', 'taliyah', 5, 4),
 ('DA_Taric19', 'Taric', 'taric', 1, 1),
 ('DA_Teemo19', 'Teemo', 'teemo', 5, 4),
 ('DA_Thresh19', 'Thresh', 'thresh', 4, 1),
 ('DA_Tibbers19', 'Tibbers', 'tibbers', 3, 1),
 ('DA_Tristana19', 'Tristana', 'tristana', 2, 4),
 ('DA_TwistedFate19', 'Twisted Fate', 'twisted-fate', 5, 4),
 ('DA_Varus19', 'Varus', 'varus', 1, 4),
 ('DA_Vayne19', 'Vayne', 'vayne', 4, 5),
 ('DA_Vi19', 'Vi', 'vi', 5, 1),
 ('DA_Viego19', 'Viego', 'viego', 3, 1),
 ('DA_Vladimir19', 'Vladimir', 'vladimir', 5, 2),
 ('DA_Wukong19', 'Wukong', 'wukong', 4, 1),
 ('DA_Xayah19', 'Xayah', 'xayah', 4, 4),
 ('DA_Yasuo19', 'Yasuo', 'yasuo', 4, 1),
 ('DA_Yone19', 'Yone', 'yone', 4, 1),
 ('DA_Yorick19', 'Yorick', 'yorick', 4, 1),
 ('DA_Yunara19', 'Yunara', 'yunara', 1, 4),
 ('DA_Yuumi19', 'Yuumi', 'yuumi', 4, 4),
 ('DA_Zac19', 'Zac', 'zac', 4, 1),
 ('DA_Zeri19', 'Zeri', 'zeri', 5, 4),
 ('DA_Ziggs19', 'Ziggs', 'ziggs', 1, 4),
 ('DA_Zoe19', 'Zoe', 'zoe', 1, 4))


def source_units():
    return {key: {'apiKey': key, 'name': name, 'slug': slug, 'cost': cost,
                  'stats': {'offense': {'range': attack_range}}}
            for key, name, slug, cost, attack_range in VERIFIED_UNITS}


def snapshot():
    return json.loads((Path(__file__).parent / 'data' / 'set19.json').read_text(encoding='utf-8'))


class ChampionRangeTests(unittest.TestCase):
    def test_all_94_bundled_ranges_and_provenance_match_verified_source(self):
        data = snapshot()
        champions = {champion['name']: champion for champion in data['champions']}
        self.assertEqual(len(champions), 94)
        expected_names = set()
        for key, name, slug, cost, attack_range in VERIFIED_UNITS:
            if name == 'Akali':
                name = ranges.AKALI_NAMES[slug]
            expected_names.add(name)
            with self.subTest(champion=name):
                self.assertEqual(champions[name]['cost'], cost)
                self.assertEqual(champions[name]['attack_range'], attack_range)
                self.assertEqual(champions[name]['attack_range_source'],
                                 f'{ranges.SOURCE_URL}#/{key}/stats/offense/range')
        self.assertEqual(set(champions), expected_names)
        self.assertEqual(sum(champion['unlockable'] for champion in champions.values()), 32)
        self.assertEqual(len(data['traits']), 47)
        self.assertEqual(len(data['collab_groups']), 40)

    def test_complete_refresh_preserves_every_other_field_and_source_input(self):
        data = snapshot()
        original = copy.deepcopy(data)
        units = source_units()
        original_units = copy.deepcopy(units)
        for champion in data['champions']:
            champion.pop('attack_range')
            champion.pop('attack_range_source')
        ranges.apply_ranges(data, units)
        self.assertEqual(data, original)
        self.assertEqual(units, original_units)

    def test_source_provenance_is_added_once_without_replacing_existing_sources(self):
        data = snapshot()
        data['sources'] = [{'name': 'Keep this reference', 'url': 'https://example.org'}]
        ranges.apply_ranges(data, source_units())
        ranges.apply_ranges(data, source_units())
        self.assertEqual(data['sources'], [
            {'name': 'Keep this reference', 'url': 'https://example.org'}, ranges.SOURCE])

    def test_roles_do_not_determine_range_and_hero_overrides_do_not_replace_base(self):
        data = snapshot()
        units = source_units()
        units['DA_Annie19_HeroAugment_Override'] = {
            'apiKey': 'DA_Annie19_HeroAugment_Override', 'name': 'Annie',
            'slug': 'annie-hero-augment', 'cost': 2, 'stats': {'offense': {'range': 8}}}
        units['DA_Olaf19_HeroAugment_Override'] = {
            'apiKey': 'DA_Olaf19_HeroAugment_Override', 'name': 'Olaf',
            'slug': 'olaf-hero-augment', 'cost': 1, 'stats': {'offense': {'range': 4}}}
        for champion in data['champions']:
            champion['role'] = 'Unrelated role'
        ranges.apply_ranges(data, units)
        actual = {champion['name']: champion['attack_range'] for champion in data['champions']}
        self.assertEqual({name: actual[name] for name in
                          ('Annie', 'Olaf', 'Nidalee', 'Sona', 'Graves', 'Gnar')},
                         {'Annie': 1, 'Olaf': 1, 'Nidalee': 6, 'Sona': 10, 'Graves': 2, 'Gnar': 4})

    def assert_rejected_unchanged(self, units, message):
        data = snapshot()
        before = copy.deepcopy(data)
        with self.assertRaisesRegex(ValueError, message):
            ranges.apply_ranges(data, units)
        self.assertEqual(data, before)

    def test_missing_extra_and_changed_cost_rosters_fail_before_mutation(self):
        missing = source_units()
        missing.pop('DA_Ahri19')
        self.assert_rejected_unchanged(missing, 'roster differs')
        extra = source_units()
        extra['DA_New19'] = {'apiKey': 'DA_New19', 'name': 'New', 'slug': 'new',
                             'cost': 1, 'stats': {'offense': {'range': 1}}}
        self.assert_rejected_unchanged(extra, 'roster differs')
        cost = source_units()
        cost['DA_Ahri19']['cost'] = 5
        self.assert_rejected_unchanged(cost, 'cost changed')
        for value in (True, 4.0, '4', None, 0):
            cost['DA_Ahri19']['cost'] = value
            self.assert_rejected_unchanged(cost, 'Invalid source cost')

    def test_ranges_must_be_present_positive_finite_numbers_not_booleans(self):
        for value in (None, '', '4', 0, -1, True, False, float('nan'), float('inf'), float('-inf')):
            with self.subTest(value=value):
                units = source_units()
                units['DA_Zoe19']['stats']['offense']['range'] = value
                self.assert_rejected_unchanged(units, 'invalid attack range')
        for stats in ({}, {'offense': {}}, {'offense': None}, None):
            units = source_units()
            units['DA_Zoe19']['stats'] = stats
            self.assert_rejected_unchanged(units, 'invalid attack range')

    def test_positive_numeric_range_is_not_rounded_or_inferred(self):
        data = snapshot()
        units = source_units()
        units['DA_Ahri19']['stats']['offense']['range'] = 3.5
        ranges.apply_ranges(data, units)
        ahri = next(champion for champion in data['champions'] if champion['name'] == 'Ahri')
        self.assertEqual(ahri['attack_range'], 3.5)

    def test_duplicate_names_slugs_and_invalid_identities_are_rejected(self):
        duplicate_name = source_units()
        duplicate_name['DA_Zoe19']['name'] = 'Ziggs'
        self.assert_rejected_unchanged(duplicate_name, 'Duplicate')
        duplicate_slug = source_units()
        duplicate_slug['DA_Zoe19']['slug'] = 'ziggs'
        self.assert_rejected_unchanged(duplicate_slug, 'Duplicate')
        identity = source_units()
        identity['DA_Zoe19']['apiKey'] = 'wrong'
        self.assert_rejected_unchanged(identity, 'identity')
        variant = source_units()
        variant['DA_AkaliKDA19']['slug'] = 'akali-unknown'
        self.assert_rejected_unchanged(variant, 'Unknown Akali')
        for field in ('name', 'slug'):
            missing = source_units()
            missing['DA_Zoe19'].pop(field)
            self.assert_rejected_unchanged(missing, 'name/slug')

    def test_duplicate_snapshot_names_and_json_keys_are_rejected(self):
        data = snapshot()
        data['champions'].append(copy.deepcopy(data['champions'][0]))
        before = copy.deepcopy(data)
        with self.assertRaisesRegex(ValueError, 'Duplicate champion names'):
            ranges.apply_ranges(data, source_units())
        self.assertEqual(data, before)
        for text in ('{"unit": {}, "unit": {}}', '{"unit": {"range": 1, "range": 4}}'):
            with self.assertRaisesRegex(ValueError, 'Duplicate source JSON key'):
                ranges.load_ranges(text)
        for payload in ([], {}, None):
            self.assert_rejected_unchanged(payload, 'nonempty unit object')


class ChampionRangeRefreshTests(unittest.TestCase):
    def test_atomic_write_replaces_only_after_success_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'set19.json'
            destination.write_text('original snapshot', encoding='utf-8')
            with patch.object(Path, 'replace', side_effect=OSError('cannot replace')):
                with self.assertRaisesRegex(OSError, 'cannot replace'):
                    ranges.write_snapshot(destination, {'updated': True})
            self.assertEqual(destination.read_text(encoding='utf-8'), 'original snapshot')
            self.assertEqual(list(Path(directory).iterdir()), [destination])
            ranges.write_snapshot(destination, {'updated': True})
            self.assertEqual(json.loads(destination.read_text(encoding='utf-8')), {'updated': True})
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_serialization_failure_never_touches_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'set19.json'
            destination.write_bytes(b'original snapshot')
            with self.assertRaises(ValueError):
                ranges.write_snapshot(destination, {'invalid': float('nan')})
            self.assertEqual(destination.read_bytes(), b'original snapshot')
            self.assertEqual(list(Path(directory).iterdir()), [destination])

    def test_archived_cli_success_and_validation_failure_are_offline_and_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'set19.json'
            archived = Path(directory) / 'units.json'
            data = snapshot()
            for champion in data['champions']:
                champion.pop('attack_range')
                champion.pop('attack_range_source')
            destination.write_text(json.dumps(data), encoding='utf-8')
            archived.write_text(json.dumps(source_units()), encoding='utf-8')
            arguments = ['update_champion_ranges.py', '--source-json', str(archived),
                         '--output', str(destination)]
            with patch('sys.argv', arguments), redirect_stdout(StringIO()):
                self.assertEqual(ranges.main(), 0)
            self.assertEqual(json.loads(destination.read_text(encoding='utf-8')), snapshot())
            before = destination.read_bytes()
            archived.write_text('{}', encoding='utf-8')
            with patch('sys.argv', arguments), redirect_stderr(StringIO()):
                self.assertEqual(ranges.main(), 1)
            self.assertEqual(destination.read_bytes(), before)

    def test_full_refresh_archive_requires_json_ranges_and_uses_atomic_writer(self):
        self.assertEqual(update_data.SOURCES['champion_ranges'], ranges.SOURCE_URL)
        self.assertIs(update_data.write_snapshot, ranges.write_snapshot)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for key in update_data.SOURCES:
                if key != 'champion_ranges':
                    (root / f'{key}.html').write_text(f'<html>{key}</html>', encoding='utf-8')
            with self.assertRaises(FileNotFoundError):
                update_data.fetch_pages(root)
            source_text = json.dumps(source_units())
            (root / 'champion_ranges.json').write_text(source_text, encoding='utf-8')
            pages = update_data.fetch_pages(root)
            self.assertEqual(ranges.load_ranges(pages['champion_ranges']), source_units())
            self.assertEqual(set(pages), set(update_data.SOURCES))


if __name__ == '__main__':
    unittest.main()
