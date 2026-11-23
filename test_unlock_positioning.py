"""Quest setup geometry must override ordinary melee/frontline preferences."""
import copy
import unittest

from positioning import annotate_positioning


class UnlockPositioningTests(unittest.TestCase):
    def test_vladimir_setup_has_exactly_one_unit_in_front_two_rows(self):
        champions = {
            str(index): {'name': str(index), 'cost': 1, 'role': 'Magic Tank',
                         'attack_range': 1, 'attack_range_source': 'https://example.org/range'}
            for index in range(11)
        }
        metadata = copy.deepcopy(champions)
        setup = {'champions': list(champions), 'item_holders': {'tank': '4'},
                 'planned_stars': {}, 'positioning_constraint': 'one_front_two_rows'}
        result = {'champions': [], 'buildup': [
            {'preparation_boards': [{'target': 'Vladimir', 'board': setup}]}]}
        annotate_positioning(result, champions)
        self.assertEqual([marker['champion'] for marker in setup['positioning'] if marker['row'] < 2], ['4'])
        self.assertEqual(len({(marker['row'], marker['column']) for marker in setup['positioning']}), 11)
        self.assertEqual({marker['champion'] for marker in setup['positioning']}, set(champions))
        self.assertEqual(champions, metadata)


if __name__ == '__main__':
    unittest.main()
