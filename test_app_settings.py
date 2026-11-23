import json
from pathlib import Path
import tempfile
import unittest

from app_settings import load_language, save_language


class LanguagePreferenceTests(unittest.TestCase):
    def test_saved_language_round_trips_without_game_or_usage_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'settings.json'
            save_language(path, 'ar_AE')
            self.assertEqual(load_language(path, {'en_US', 'ar_AE'}), ('ar_AE', None))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'language': 'ar_AE'})

    def test_corrupt_or_unsupported_setting_recovers_with_an_explanation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            for content in ('{bad json', '{"language":"unsupported"}', '{"language":[]}'):
                path.write_text(content, encoding='utf-8')
                language, error = load_language(path, {'en_US', 'fr_FR'})
                self.assertEqual(language, 'en_US')
                self.assertTrue(error)


if __name__ == '__main__':
    unittest.main()
