# prokjs's Set 19 TFT tool

**Developed by prokjs.**

Offline Windows TFT Set 19 Collab pool picker and trait-synergy team optimizer.

Pick the five or six Collab champions shown in your game directly in the main portrait grid. While the pool is **unlocked**, click a portrait to add/remove it; conflicting candidates disappear, selected picks stay removable, and Sona is automatic. Click **Lock Collab pool** to restore the full roster and enable team-building. **Unlock Collab pool** returns to filtered picking without losing your picks or team settings.

Once the pool is locked, click portraits to select, Ctrl-click for multiple selections, or Shift-click for a range. **Team lock**, Exclude, and **Planned stars** controls then affect the selected team units—not the Collab pool. Stars use repeated markers (★, ★★, ★★★, ★★★★) and describe upgrade targets, not units already owned. At final level 10 or higher, sourced 4-cost Tanks default to ★★ unless explicitly overridden; other units default to ★. Changing the final level refreshes these assumptions. Hover or select for names, traits, costs, and unlock details. The grid wraps and scrolls; there is no separate pool-selection window.

Selected results show compact TFT-inspired active-trait cards beside the full champion lineup, rather than stacking both in a shared scrolling document. Trait cards fit their names and full breakpoint tracks, with hexagonal icons, current counts, and the active breakpoint highlighted. K/DA counts show stars, and active unique traits are included. Inactive traits are available through an optional control; unlock requirements remain accessible through the overview controls and champion cards. The layout adapts to the available pane space; widen the window or adjust the pane divider for more room. Bronze/silver/gold badges indicate breakpoint progress, not official patch-specific rarity colors.

Hover a trait card to read its effect and browse matching champions in an offline tooltip using the bundled trait reference. Collab membership and Sona's featured band use the current comp context.

Unlockable champions have a corner padlock on their portraits throughout the roster, pool strip, comp cards, leveling guide, and trait tooltips. The badge marks a required quest, not live tracking of whether you have completed it. Sourced unlock tasks and player-level requirements appear in portrait tooltips, selected-champion details, and team results. Click **Unlock details** on a team card for the full requirement and its source. Copied teams and JSON exports include the requirements. Pre-release source uncertainties are shown explicitly.

**Copy TFT code** copies a native team-planner code for the selected lineup using the public version-02 Set 19 format: ten three-hex-digit ID slots (empty slots use `000`) and the `TFTSet19` suffix. Codes support at most ten distinct champion entries; teams are never truncated. Baron Nashor encodes as one champion entry despite occupying two board slots. Codes carry the lineup order, not stars, items, positions, Collab selection, or unlock state. **Copy plan** separately copies readable planned stars, unlock conditions, strategy notes, and level buildups; **Save JSON** exports the full result data.

The bundled mapping contains 89 supported champion IDs, including the two distinct Akalis. Five champions are deliberately unsupported: Graves, Mordekaiser, and Nasus share one published ID; Kennen and Kobuko have unresolved cross-source ID conflicts. Selecting any of these produces a clear error instead of a fabricated code. Provenance and conflicting source values are retained with the mapping. Published format/source evidence is not a claim of testing every roster in the live game client.

## Level-by-level buildups

The default final level is **10**. Select a recommended comp and choose **Level buildup** to see every level from 1 to the final level: portraits, additions, removals, temporary units, traits, upgrade targets, and unlock requirements. Copies and JSON exports include the complete buildup, ideal goal, and final pivot phases.

Each level opens on its **Arrival** board, preserving most of the previous lineup rather than recommending a fresh team every level. Primary steps normally remove at most one champion below level 8 and two at level 8+. At the final level, Arrival can still contain temporary units: the selected comp is the **ideal end goal**, not an instant full-team replacement. A compact phase selector shows the actual gradual same-level upgrades, ending at that exact goal. Hold Arrival until the needed champions are found; buy incoming units before selling their replacements, and verify unlocks before fielding them. Phases do not claim owned units, available gold, or completed quests. Weighted two-slot Baron transitions can require a larger exchange, which the plan explains explicitly.

Quest-dependent boards are conditional, not proof that unlocks are complete. The planner also shows independent no-unlock alternatives where feasible, or an explicit reason when no exact-slot board exists—never invented fillers. Temporary Annie, Ziggs, Xayah, or other relevant helpers can intentionally prepare a later target's unlock. **Unlock preparation** names that future target and its complete task; fielding a one-star helper does not satisfy a two-star, item, streak, or progress requirement. For example, preparing Evelynn with three K/DA champions means **three different champions**, not three K/DA star levels; follow the full sourced task and confirm the unlock after the required combat before making the handoff. Keep helpers until the required unlock is actually earned, then follow the conditional handoff.

End-board locks do not force late-game units onto early boards. Intermediate boards below level 10 assume one-star units; final-level and level-10-or-higher stages use evaluated planned stars. Default acquisition planning starts 1-costs at level 1, 2-costs at level 3, 3-costs at level 4, **4-costs at level 7**, and **5-costs at level 9**. Published unlock minimums still apply, with conservative timing for quest-only 7-costs. These are planning minima, not claims that earlier rare shop hits are impossible; earlier guaranteed unlock-copy rewards are not simulated. Level 11 requires a level-cap extension. Plans assume normal one-slot-per-level capacity (Baron Nashor uses two slots), not bonus-slot augments.

Default recommendations avoid unrelated long unlock chains. Unlockable **Hyperpop** units require an actual native-Hyperpop Collab pick or Hyperpop as Sona's featured band; ordinary Ziggs remains allowed. Other long-chain targets require relevant Collab support: Gu Feng for unlockable Gu Feng units, Heartsteel for Aphelios, K/DA for Akali K/DA, True Damage for Akali True Damage, Ionic Spark for Gwen, Pentakill for Mordekaiser, and Beach Pop for Taliyah. Baron requires Baron or Malzahar Collab support; Rakan's streak path requires Rakan Collab support. Jazz's single Smeech unlock can still be prepared through a real Jazz bridge, and simple conditional unlocks such as Annie → Tibbers are not blanket-banned. **Team lock** is an explicit manual opt-in to otherwise restricted end-goal chains and retains a planning warning; necessary dependency-band helpers may appear in that goal's guide without licensing unrelated chain targets in the final search. Every allowed quest remains conditional.

Shop timing references the [TFT Flow Set 19 shop table, patch 19.1](https://tftflow.com/tables/set19/shop-odds-pool-size-xp-table), with the conservative planning minima above. These are heuristic transitions, not round-by-round economy or shop simulations.

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
