# Legal notices and public-launch restrictions

## Status: unapproved prototype

This repository is not evidence of Riot approval, an IP licence, or permission to monetize. No registration, approval or legal clearance is claimed. Experimental manual in-game unlock, inventory, starting-board and transition features are included at the user's request despite the current-game-state recommendation restriction below; they are not policy-cleared. Do not deploy an ad-supported public product before the reviews below.

## Required Riot disclosure

prokjs's Set 19 TFT tool isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games, and all associated properties are trademarks or registered trademarks of Riot Games, Inc.

## Purpose and responsibility

This tool calculates champion combinations, trait relationships and heuristic board transitions, using manually reported unlocks, item inventory and starting-board snapshots when provided. Unlock confirmations do not establish owned units, items, gold or completed purchases. Inventory and rank entries are user assertions, not detected game state. It does not calculate, predict or guarantee match outcomes. Scores, item compatibility, estimated positions and personal trait-tier percentages are not measured combat strength or win probabilities.

Provided as is, without warranties, to the extent permitted by applicable law. Use at your own risk. You are responsible for your game decisions. To the extent permitted by law, the developer is not liable for losses arising from use. Nothing here excludes liability or legal rights that cannot lawfully be excluded. These notices do not create immunity or substitute for legal advice.

## Before a public or AWS-hosted ad-supported release

1. **Register and obtain Riot review.** [Riot's TFT policy](https://developer.riotgames.com/docs/tft) requires registration even without official API use. Monetization requires product status **Approved or Acknowledged** and a free tier; advertisements can be part of that free tier. Features and metadata must stay current.
2. **Resolve the use-case restriction.** The TFT policy permits static pre-game recommendations but prohibits suggestions based on current in-game state, dynamic real-time recommendations and apps that dictate decisions. The custom Collab pool, replacement guide and item/position advice require explicit Riot review, even when inputs are manual. Renaming the tool or adding a disclaimer does not resolve that restriction.
3. **Resolve content permissions.** Riot owns the champion/trait/item IP. Bundled artwork is extracted from Riot's Set 19 client files and served by CommunityDragon; the pinned patch, source URLs and hashes are recorded in `assets/manifest.json` and `data/item_icons.json`. CommunityDragon is community-run, not a Riot approval or licence service. Public URLs and attribution are provenance, not redistribution permission. Review source-specific terms and obtain any necessary permissions. Do not imply that CommunityDragon, TFT Ninja, TFT Flow, IES, MetaTFT or Riot sponsors this tool.
4. **Review branding.** Keep the non-endorsement notice readily visible. Do not use Riot logos, endorsement claims or confusing domains/marketing without appropriate permission. Review the product name before commercial launch.
5. **Preserve dependency notices.** Distribute `THIRD_PARTY_NOTICES.txt` and the included licence material with binary distributions. Community-assisted interface translations are not official Riot translations. Locale catalogs retain official/third-party source provenance and incomplete-PBE warnings.
6. **Review the website separately.** AWS hosting does not grant content rights. Before enabling ads, review advertising-network terms, inappropriate/gambling ads, privacy disclosures, tracking and any applicable cookie/advertising consent obligations. No ads, tracking, API keys or AWS deployment are implemented by this update.

Policies checked against the official pages on November 20, 2026; policies can change:

- [Riot TFT policy](https://developer.riotgames.com/docs/tft)
- [Riot general developer policies](https://developer.riotgames.com/policies/general)
- [Riot Legal Jibber Jabber](https://www.riotgames.com/en/legal)

Riot, source providers and applicable law—not this document—determine permission and compliance. The current build is not cleared for public monetization.

## Rust and desktop/browser runtimes

The desktop uses [Tauri](https://github.com/tauri-apps/tauri), the Rust engine in `crates/tft-core`, and the Windows-provided WebView2 Runtime. The browser uses the same Rust engine compiled to bundled WebAssembly. The application does not include a Python interpreter, Pyodide, or an external calculation service. WebView2 is a Microsoft system runtime, not a grant of rights to game content. `THIRD_PARTY_NOTICES.txt` records Cargo dependency source/license material and standard-library notices; building or bundling does not clear Riot/source-provider rights, branding, public distribution, or monetization.
