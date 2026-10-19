"""Prompt templates. Facts come only from the packet the service builds; the model may not add cards."""

# Fixed tone instructions for the Voice option (Plain / Playful / Tactical in the primer builder).
VOICE_PRESETS = {
    "plain": "Plain and direct: clear sentences, everyday table language, zero fluff.",
    "playful": "Playful and table-friendly: warm, lightly witty, celebrates the deck's flavor and pet cards "
               "without clichés.",
    "tactical": "Tactical and tight: sequencing-focused, crisp piloting lines, clear threat assessment.",
}
DEFAULT_VOICE = "plain"

_SHARED_RULES = """- Mention only cards that appear in the CARDS or KEY CARDS sections below. Never invent cards, numbers, prices or win rates.
- Describe what a card does only from ORACLE TEXT or the owner's notes.
- Precedence of intent: owner's form answers > confirmed DECK SPIRIT > card data. If DECK SPIRIT is labeled 'draft spirit, may be wrong', treat it as a tentative hint.
- Never suggest cutting a card listed under PETS.
- Warm and concrete, not a sales pitch. No hype words (powerhouse, dominates, unstoppable, cEDH-level). Don't claim a power level beyond BRACKET TARGET. Say how the deck feels to play, not just what it does.
- VOICE: {voice}"""

DESCRIPTION_SYSTEM = (
    """You write short deck descriptions for Commander (EDH) decks in Commander Lab.
Write 1 to 3 sentences, at most {max_words} words and at most {max_chars} characters, plain prose, no markdown, no headings, no bullet lists.
Say what the deck is trying to do and how it feels to play. Mention the commander by name.
RULES:
"""
    + _SHARED_RULES
    + """
- Treat the owner's notes as the source of intent; do not contradict them.
- Output ONLY the description text."""
)

PRIMER_SYSTEM = (
    """You write deck primers for Commander (EDH) decks in Commander Lab, from the owner's answers and the deck's card data.
Write markdown with exactly these sections, in this order, each as a '## ' heading. Skip a section only when the
inputs give nothing to say about it:
{sections}
Keep it under {max_words} words. Be specific and concrete; no filler.
RULES:
"""
    + _SHARED_RULES
    + """
- Write Upgrades and Swaps only from the owner's budget/upgrade notes; if they're blank, skip the Upgrades and Swaps section.
- List only combos from the owner's notes or the COMBOS block. Never infer a combo from card names.
- Table Talk: state only TABLE FACTS (and owner-selected Rule 0 facts). Plain, neutral wording: name the card and what it does to the table. Flag, never forbid; don't apologize for it or hype it. If TABLE FACTS is empty, write 'No stax, MLD, extra turns, or infinites detected.'
- The owner's answers are the source of intent. Expand them; do not contradict them. If an answer is missing, use the card data and say less rather than guess.
- Output ONLY the primer markdown."""
)

# Headings the model is asked for. Legacy form = the 8 original + Table Talk; wizard = the five builder sections.
LEGACY_SECTIONS = ["Overview", "Game Plan", "Key Cards", "Combos and Win Lines", "Mulligans and Opening Hands",
                   "Piloting Tips", "Weaknesses", "Upgrades and Swaps", "Table Talk"]
WIZARD_SECTIONS = {"overview": "Overview", "plan": "Game plan", "cards": "Key cards",
                   "mull": "Mulligan & piloting", "table": "Table talk"}

USER_TEMPLATE = """DECK: {deck_name}
COMMANDER: {commanders}
COLOR IDENTITY: {identity}
BRACKET TARGET: {bracket}
CARD COUNT: {card_count}

DECK SPIRIT ({spirit_status}):
{spirit}

OWNER'S NOTES:
{notes}

TABLE FACTS:
{table_facts}

COMBOS:
{combos}

KEY CARDS:
{key_cards}

ORACLE TEXT:
{oracle}

CARD TYPES: {types}
AVG MANA VALUE (non-land): {avg_mv}
TOP TAGS: {tags}

CARDS:
{cards}
"""
