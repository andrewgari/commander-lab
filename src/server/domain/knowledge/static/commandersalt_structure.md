## Commander Salt (third-party signal)
Commander Salt (commandersalt.com) is an independent site. When FACTS includes its numbers they are labelled "Commander Salt"; treat them as a second opinion, never as the deciding input.
How its pipeline is shaped (structure only):
1. It takes a public deck URL, looks it up in its cache, or imports it.
2. It tags each card with categories (for example ramp, interaction, combos, stax) and scores each category.
3. It combines category scores into a deck salt rating, a power rating, a bracket rating and detected combo lines.
Commander Lab uses the same shape with its own data and weights: card facts (Scryfall) -> card signals (EDHREC inclusion, bracket lift, synergy, Commander Spellbook combos, Game Changer flag) -> per-card merit -> deck power and bracket estimate -> bands. Commander Lab's weights are fitted on EDHREC and Commander Spellbook data; none come from Commander Salt.
