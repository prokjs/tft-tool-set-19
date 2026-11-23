import copy
import json
import unittest

from trait_support import annotate_trait_support


class TraitSupportTests(unittest.TestCase):
    def test_percentage_means_average_personal_tier_completion_not_unique_buffs(self):
        champions = {'Carry': {'traits': ['Damage', 'Solo']}}
        traits = {
            'Damage': {'breakpoints': [2, 4, 6], 'unique': False, 'description': 'Sourced effect'},
            'Solo': {'breakpoints': [1], 'unique': True, 'description': 'Unique effect'},
        }
        result = {'champions': ['Carry'], 'traits': [
            {'name': 'Damage', 'count': 4}, {'name': 'Solo', 'count': 1}], 'buildup': []}
        original = copy.deepcopy((champions, traits))
        annotate_trait_support(result, champions, traits)
        support = result['champion_trait_support']['Carry']
        self.assertEqual(support['percent'], 66.7)
        self.assertEqual(support['details'][0]['reached'], 4)
        self.assertIn('not damage efficiency', support['note'])
        self.assertEqual((champions, traits), original)
        json.dumps(result)

    def test_dynamic_collab_and_sona_band_count_but_unassigned_collab_does_not(self):
        champions = {'Picked': {'traits': ['Damage']}, 'Other': {'traits': ['Collab']},
                     'Sona': {'traits': ['Collab', 'Solo']}}
        traits = {
            'Damage': {'breakpoints': [2, 4], 'unique': False},
            'Collab': {'breakpoints': [3, 5], 'unique': False},
            'Solo': {'breakpoints': [1], 'unique': True},
        }
        result = {'champions': list(champions), 'collab_pool': ['Picked'], 'sona_band': 'Damage',
                  'traits': [{'name': 'Damage', 'count': 2}, {'name': 'Collab', 'count': 3},
                             {'name': 'Solo', 'count': 1}]}
        annotate_trait_support(result, champions, traits)
        self.assertEqual(result['champion_trait_support']['Picked']['percent'], 50)
        self.assertEqual(result['champion_trait_support']['Sona']['percent'], 50)
        self.assertIsNone(result['champion_trait_support']['Other']['percent'])

    def test_zero_activation_and_unique_only_are_distinct(self):
        champions = {'Inactive': {'traits': ['Damage']}, 'Unique': {'traits': ['Solo']}}
        traits = {'Damage': {'breakpoints': [2], 'unique': False},
                  'Solo': {'breakpoints': [1], 'unique': True}}
        result = {'champions': list(champions), 'traits': [{'name': 'Damage', 'count': 1},
                                                        {'name': 'Solo', 'count': 1}]}
        annotate_trait_support(result, champions, traits)
        self.assertEqual(result['champion_trait_support']['Inactive']['percent'], 0)
        self.assertIsNone(result['champion_trait_support']['Unique']['percent'])

    def test_all_board_variants_have_detached_explanations(self):
        champions = {'Carry': {'traits': ['Damage']}}
        traits = {'Damage': {'breakpoints': [2, 4], 'unique': False}}
        board = {'champions': ['Carry'], 'traits': [{'name': 'Damage', 'count': 2}]}
        result = {**copy.deepcopy(board), 'buildup': [{
            'board': copy.deepcopy(board), 'fallback': copy.deepcopy(board),
            'goal_board': copy.deepcopy(board), 'upgrades': [{'board': copy.deepcopy(board)}]}]}
        annotate_trait_support(result, champions, traits)
        step = result['buildup'][0]
        step['board']['champion_trait_support']['Carry']['details'][0]['reached'] = 99
        for other in (result, step['fallback'], step['goal_board'], step['upgrades'][0]['board']):
            self.assertEqual(other['champion_trait_support']['Carry']['details'][0]['reached'], 2)


if __name__ == '__main__':
    unittest.main()
