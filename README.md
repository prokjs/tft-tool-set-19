# prokjs's Set 19 TFT tool

**Developed by prokjs.**

Offline Windows TFT Set 19 Collab pool picker and trait-synergy team optimizer.

Pick the five or six Collab champions shown in your game directly in the main portrait grid. While the pool is **unlocked**, click a portrait to add/remove it; conflicting candidates disappear, selected picks stay removable, and Sona is automatic. Click **Lock Collab pool** to restore the full roster and enable team-building. **Unlock Collab pool** returns to filtered picking without losing your picks or team settings.

Once the pool is locked, click portraits to select, Ctrl-click for multiple selections, or Shift-click for a range. **Team lock**, Exclude, and **Planned stars** controls then affect the selected team units—not the Collab pool. Stars use repeated markers (★, ★★, ★★★, ★★★★) and describe upgrade targets, not units already owned. At final level 10 or higher, sourced 4-cost Tanks default to ★★ unless explicitly overridden; other units default to ★. Changing the final level refreshes these assumptions. Hover or select for names, traits, costs, and unlock details. The grid wraps and scrolls; there is no separate pool-selection window.

Selected results show compact TFT-inspired active-trait cards beside the full champion lineup, rather than stacking both in a shared scrolling document. Trait cards fit their names and full breakpoint tracks, with hexagonal icons, current counts, and the active breakpoint highlighted. K/DA counts show stars, and active unique traits are included. Inactive traits are available through an optional control; unlock requirements remain accessible through the overview controls and champion cards. The layout adapts to the available pane space; widen the window or adjust the pane divider for more room. Bronze/silver/gold badges indicate breakpoint progress, not official patch-specific rarity colors.

Hover a trait card to read its effect and browse matching champions in an offline tooltip using the bundled trait reference. Collab membership and Sona's featured band use the current comp context.

Unlockable champions show sourced unlock tasks and player-level requirements in portrait tooltips, selected-champion details, and team results. Click **Unlock details** on a team card for the full requirement and its source. Copied teams and JSON exports include the requirements. These are offline reference conditions—not automatic tracking of in-game progress; pre-release source uncertainties are shown explicitly.

**Copy TFT code** copies a native team-planner code for the selected lineup using the public version-02 Set 19 format: ten three-hex-digit ID slots (empty slots use `000`) and the `TFTSet19` suffix. Codes support at most ten distinct champion entries; teams are never truncated. Baron Nashor encodes as one champion entry despite occupying two board slots. Codes carry the lineup order, not stars, items, positions, Collab selection, or unlock state. **Copy plan** separately copies readable planned stars, unlock conditions, strategy notes, and level buildups; **Save JSON** exports the full result data.

The bundled mapping contains 89 supported champion IDs, including the two distinct Akalis. Five champions are deliberately unsupported: Graves, Mordekaiser, and Nasus share one published ID; Kennen and Kobuko have unresolved cross-source ID conflicts. Selecting any of these produces a clear error instead of a fabricated code. Provenance and conflicting source values are retained with the mapping. Published format/source evidence is not a claim of testing every roster in the live game client.

## Level-by-level buildups

The default final level is **10**. Select a recommended comp and choose **Level buildup** to see every level from 1 to the final board: portraits, additions, removals, temporary units, traits, and unlock requirements. Copies and JSON exports include each comp's buildup.

Quest-dependent boards are conditional, not proof that unlocks are complete. The planner also shows non-unlock alternatives where feasible. End-board locks do not force late-game units onto early boards. Intermediate boards below level 10 assume one-star units; the final board and level-10-or-higher stages use their evaluated planned stars. Shop access and published unlock levels restrict acquisition, with conservative timing for quest-only 7-costs; earlier guaranteed unlock-copy rewards are not simulated. Level 11 requires a level-cap extension. Plans assume normal one-slot-per-level capacity (Baron Nashor uses two slots), not bonus-slot augments.

Shop timing uses the [TFT Flow Set 19 shop table, patch 19.1](https://tftflow.com/tables/set19/shop-odds-pool-size-xp-table). These are heuristic transitions, not round-by-round economy or shop simulations.

## Rankings

- **High value** (default): rewards higher-cost units alongside synergy depth, conditional Jazz value, and frontline balance. Base cost contributes 30 points per gold; cost is a heuristic, not measured strength.
- **Balanced**: uses the same deep-trait and lineup-context preferences without the base-cost bonus.
- **Most active traits**: remains breadth-first, favoring activations rather than the late-game strategy preferences below.

Roster gold is the sum of base champion costs, not the gold needed for star upgrades. High value is a rarity/cost heuristic, not measured combat strength. Locks, exclusions, Collab minimums, and team-slot limits apply in every mode.

Balanced and High value favor deeper breakpoints, not just many shallow splashes. Jazz's extra value is conditional on fielded synergies; it does not activate absent traits. At level 10+, only the best qualifying upgraded Tank contributes the frontline-anchor preference: a planned 3-star 3-cost or a planned 2-star-or-better higher-cost Tank. A 3-star 3-cost is never assumed automatically. Manual targets and locks remain allowed.

For these two modes, shallow Gu Feng, True Damage without a qualifying fielded carry, and shallow late-game Hyperpop without an upgraded member receive value discounts, not altered trait counts. Fast-10 plans apply a heuristic penalty to 3-star cheap reroll targets and a smaller penalty to exactly 2-star Qiyana; this is not an economy simulation or proof the upgrade is hit. Senna receives a current-patch preference penalty, not a ban or a measured win-rate claim. Result notes and copied plans record relevant assumptions.

## Run and build

- Run from source: `python app.py`
- Build the Windows executable: `python -m pip install -r requirements-build.txt`, then `./build.ps1` in PowerShell.
- The executable is written to `dist/prokjs's Set 19 TFT tool.exe`; it includes the data and artwork for offline use.

Rankings use approximate synergy heuristics, not measured win rates. Bundled data is a pre-release snapshot.

Champion and trait artwork © Riot Games. Data/artwork sources are credited in the app and bundled manifests. This is an unofficial tool.
