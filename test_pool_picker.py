"""Regression coverage for player-visible Collab candidate filtering."""
import unittest
from pool_picker import eligible_champions, visible_champions


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

    def test_fifth_pick_allows_sixth_and_sixth_stops_additions(self):
        data = champions()
        five = ['A', 'B', 'C', 'D', 'E']
        self.assertIn('F', eligible_champions(data, five))
        self.assertNotIn('Both', eligible_champions(data, five))
        self.assertNotIn('OtherTank', eligible_champions(data, five))
        six = five + ['F']
        self.assertEqual(eligible_champions(data, six), [])
        self.assertIn('F', eligible_champions(data, six[:-1]))
        self.assertNotIn('Sona', eligible_champions(data, five))

    def test_inline_pool_picks_remain_removable_and_lock_restores_conflicts(self):
        data = champions()
        editing = visible_champions(data, {'A'}, pool_locked=False)
        self.assertIn('A', editing)
        self.assertIn('Sona', editing)
        self.assertNotIn('OtherTank', editing)
        self.assertNotIn('Both', editing)
        five = {'A', 'B', 'C', 'D', 'E'}
        self.assertEqual(visible_champions(data, five, pool_locked=True), set(data))
        self.assertNotIn('OtherTank', visible_champions(data, five, pool_locked=False))

    def test_full_inline_pool_only_shows_picks_and_sona_until_unlocked_for_teams(self):
        data = champions()
        data['Extra'] = {'name': 'Extra', 'traits': ['Unique'], 'cost': 1}
        six = {'A', 'B', 'C', 'D', 'E', 'F'}
        self.assertEqual(visible_champions(data, six, pool_locked=False), six | {'Sona'})
        self.assertIn('Extra', visible_champions(data, six, pool_locked=True))
        self.assertIn('Extra', visible_champions(data, six - {'F'}, pool_locked=False))

    def test_mutually_exclusive_forms_remain_unavailable(self):
        data = champions()
        data['A']['exclusive_group'] = 'forms'
        data['F']['exclusive_group'] = 'forms'
        self.assertNotIn('F', eligible_champions(data, ['A']))
        self.assertIn('F', eligible_champions(data, []))


if __name__ == '__main__':
    unittest.main()
