"""Regression coverage for player-visible Collab candidate filtering."""
import unittest
from pool_picker import eligible_champions


def champions():
    rows = [('A', ['Tank']), ('B', ['Mage']), ('Both', ['Tank', 'Mage']),
            ('OtherTank', ['Tank']), ('C', ['Slayer']), ('D', ['Support']),
            ('E', ['Jazz']), ('F', ['Rock']), ('Sona', ['Producer', 'Collab'])]
    return {name: {'name': name, 'traits': traits, 'cost': 1} for name, traits in rows}


class PoolFilterTests(unittest.TestCase):
    def test_first_pick_hides_itself_and_every_shared_trait(self):
        data = champions()
        initial = eligible_champions(data, [])
        self.assertNotIn('Sona', initial)
        available = eligible_champions(data, ['A'])
        self.assertNotIn('A', available)
        self.assertNotIn('OtherTank', available)
        self.assertNotIn('Both', available)
        self.assertIn('B', available)
        self.assertIn('C', available)

    def test_remove_restores_only_newly_eligible_champions(self):
        data = champions()
        self.assertNotIn('Both', eligible_champions(data, ['A', 'B']))
        remaining = eligible_champions(data, ['B'])
        self.assertIn('A', remaining)
        self.assertIn('OtherTank', remaining)
        self.assertNotIn('Both', remaining)
        self.assertIn('Both', eligible_champions(data, []))

    def test_fifth_pick_stops_additions_but_removal_reopens(self):
        data = champions()
        picks = ['A', 'B', 'C', 'D', 'E']
        self.assertEqual(eligible_champions(data, picks), [])
        self.assertIn('F', eligible_champions(data, picks[:-1]))
        self.assertNotIn('Sona', eligible_champions(data, picks[:-1]))

    def test_mutually_exclusive_forms_remain_unavailable(self):
        data = champions()
        data['A']['exclusive_group'] = 'forms'
        data['F']['exclusive_group'] = 'forms'
        self.assertNotIn('F', eligible_champions(data, ['A']))
        self.assertIn('F', eligible_champions(data, []))


if __name__ == '__main__':
    unittest.main()
