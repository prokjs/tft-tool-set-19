"""Personal trait-tier completion, never damage efficiency or total buff strength."""
from bisect import bisect_right


METRIC_NOTE = (
    "Average fraction of this champion's own non-unique trait tiers activated. "
    "This is not damage efficiency, carry strength, total buffs or a win probability. "
    "Teamwide and conditional effects are not converted into percentages."
)


def annotate_trait_support(result, champions, traits):
    collab = set(result.get('collab_pool', ())) | {'Sona'}
    sona_band = result.get('sona_band', '')

    def annotate(board):
        if board is None:
            return
        rows = {row['name']: row for row in board['traits']}
        support = {}
        for name in board['champions']:
            memberships = set(champions[name]['traits']) - {'Collab'}
            if name in collab:
                memberships.add('Collab')
            if name == 'Sona' and sona_band:
                memberships.add(sona_band)
            details = []
            fractions = []
            for trait_name in sorted(memberships):
                definition = traits[trait_name]
                count = rows.get(trait_name, {}).get('count', 0)
                points = definition['breakpoints']
                tier = bisect_right(points, count)
                unique = definition['unique']
                if not unique:
                    fractions.append(tier / len(points))
                details.append({
                    'name': trait_name, 'count': count,
                    'reached': points[tier - 1] if tier else 0,
                    'tier_index': tier, 'max_tiers': len(points), 'unique': unique,
                    'effect': definition.get('description', ''),
                })
            support[name] = {
                'percent': round(100 * sum(fractions) / len(fractions), 1) if fractions else None,
                'metric': 'personal_trait_tier_completion', 'details': details,
                'note': METRIC_NOTE,
            }
        board['champion_trait_support'] = support

    annotate(result)
    for step in result.get('buildup', []):
        for board in (step['board'], step['fallback'], step.get('goal_board')):
            annotate(board)
        for phase in step.get('upgrades', []):
            annotate(phase['board'])
        for preparation in step.get('preparation_boards', []):
            annotate(preparation.get('board'))
