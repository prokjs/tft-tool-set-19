# prokjs's Set 19 TFT tool

**Developed by prokjs.**

Offline Windows TFT Set 19 Collab pool picker and trait-synergy team optimizer.

Pick five Collab champions; candidates sharing native traits are filtered automatically. Sona is included separately. Compare suggested teams, active traits, and breakpoints.

## Rankings

- **High value** (default): rewards higher-cost units alongside active traits, trait tiers, and frontline balance. Scores add 30 points per gold of base champion cost, 100 per active non-unique trait, and 10 per trait tier. This can trade a little synergy for more expensive units; it does not blindly maximize gold.
- **Balanced**: favors synergies and frontline balance without the cost bonus.
- **Most active traits**: prioritizes trait activations and breakpoints.

Roster gold is the sum of base champion costs, not the gold needed for star upgrades. High value is a rarity/cost heuristic, not measured combat strength. Locks, exclusions, Collab minimums, and team-slot limits apply in every mode.

## Run and build

- Run from source: `python app.py`
- Build the Windows executable: `python -m pip install -r requirements-build.txt`, then `./build.ps1` in PowerShell.
- The executable is written to `dist/prokjs's Set 19 TFT tool.exe`; it includes the data and artwork for offline use.

Rankings use approximate synergy heuristics, not measured win rates. Bundled data is a pre-release snapshot.

Champion and trait artwork © Riot Games. Data/artwork sources are credited in the app and bundled manifests. This is an unofficial tool.
