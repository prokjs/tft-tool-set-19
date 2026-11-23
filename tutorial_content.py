"""The same four tutorial steps for desktop and browser interfaces."""

TUTORIAL_STEPS = (
    ('Choose a language', (
        ('Language settings', 'Use the button at the top right. Choose your language and press Apply language.'),
        ('Saved preference', 'The chosen language is remembered for the next launch. You can change it later.'),
    )),
    ('Select or reset the Collab pool', (
        ('Select champions', 'While the pool is unlocked, click a portrait to add it. Click a selected portrait again to remove it.'),
        ('Five or six picks', 'Choose 5 or 6 champions. Conflicting native traits are filtered automatically. Sona is included separately.'),
        ('Lock Collab pool', 'Lock the pool when your selection is complete. Unlock it again to edit your picks.'),
        ('Reset', 'The Reset button clears the Collab picks, team locks, exclusions and star overrides so you can start again.'),
        ('Picker stars', 'The champion picker shows one star unless you explicitly override it. The leveling guide separately shows planned upgrades as levels increase.'),
    )),
    ('Find teams', (
        ('Choose settings', 'Set the final level, minimum Collab count and ranking. Sona featured band is optional.'),
        ('Find teams', 'Press Find teams after locking the pool. Wait for the combinations to appear, then select a row to inspect that team.'),
    )),
    ('Look at level buildups', (
        ('Level buildup', 'Selecting a team opens its Final board. Use the level tabs for the actual preparation, upgrade and unlock-check sequence; ⓘ opens extra information.'),
        ('Choose a level', 'Click a level, then choose a phase. Complete its setup and planned upgrades before the unlock check. Click an offered champion in the same slot card to pin it into later paths; clear the pin to restore ordinary planning.'),
        ('Final-level upgrades', 'Same-level phases separate preparation, star upgrades, unlock checks and replacements. Buy incoming units before selling their replacements; targets and quests are not assumed owned or complete.'),
        ('Unlocks', 'Set the match level and check only tasks you have completed. Confirmations change eligible paths but do not mean you own the champion, copies or items. Grey requirement portraits are missing from the displayed board.'),
        ('Cap-out', 'After the Collab cashout, inspect Cap-out for a premium-board alternative and its buy-before-sell steps. This does not confirm a cashout or purchase, and higher cost is not guaranteed combat strength.'),
    )),
)
