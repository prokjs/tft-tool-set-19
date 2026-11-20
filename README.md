# prokjs's Set 19 TFT tool

**Developed by prokjs.**

Offline Windows TFT Set 19 Collab pool picker and trait-synergy team optimizer.

Pick five Collab champions; candidates sharing native traits are filtered automatically. Sona is included separately. Compare suggested teams, active traits, and breakpoints.

The main roster uses a responsive portrait grid. Click a portrait to select, Ctrl-click to toggle multiple selections, or Shift-click for a range. Hover for names, traits, costs, and unlock details. Cost-colored portraits and C/L/X/star badges show state; search, Lock/Exclude, star controls, scrolling, and arrow-key navigation remain available.

Results use TFT-inspired trait bars: hexagonal icons, current counts, and full breakpoint tracks with the active breakpoint highlighted. Active traits sort above dimmed inactive traits; K/DA counts show stars, and unique traits appear once. Bronze/silver/gold badges indicate breakpoint progress, not official patch-specific rarity colors.

Unlockable champions show sourced unlock tasks and player-level requirements in portrait tooltips, selected-champion details, the Collab pool picker, and team results. Click **Unlock details** on a team card for the full requirement and its source. Copied teams and JSON exports include the requirements. These are offline reference conditions—not automatic tracking of in-game progress; pre-release source uncertainties are shown explicitly.

## Level-by-level buildups

The default final level is **10**. Select a recommended comp and choose **Level buildup** to see every level from 1 to the final board: portraits, additions, removals, temporary units, traits, and unlock requirements. Copies and JSON exports include each comp's buildup.

Quest-dependent boards are conditional, not proof that unlocks are complete. The planner also shows non-unlock alternatives where feasible. End-board locks do not force late-game units onto early boards. Early boards assume one-star units; the final board uses your configured stars. Shop access and published unlock levels restrict acquisition, with conservative timing for quest-only 7-costs; earlier guaranteed unlock-copy rewards are not simulated. Level 11 requires a level-cap extension. Plans assume normal one-slot-per-level capacity (Baron Nashor uses two slots), not bonus-slot augments.

Shop timing uses the [TFT Flow Set 19 shop table, patch 19.1](https://tftflow.com/tables/set19/shop-odds-pool-size-xp-table). These are heuristic transitions, not round-by-round economy or shop simulations.

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
