"""Prompt templates. Facts come only from the packet the service builds; the model may not add cards."""

DESCRIPTION_SYSTEM = """You write short deck descriptions for Commander (EDH) decks in Commander Lab.
Write 2 to 4 sentences, at most {max_words} words, plain prose, no markdown, no headings, no bullet lists.
Say what the deck is trying to do and how it feels to play. Mention the commander by name.
RULES:
- Mention only cards that appear in the CARDS or KEY CARDS sections below. Never invent cards, numbers, prices or win rates.
- Treat the owner's notes as the source of intent; do not contradict them.
- Output ONLY the description text."""

PRIMER_SYSTEM = """You write deck primers for Commander (EDH) decks in Commander Lab, from the owner's answers and the deck's card data.
Write markdown with exactly these sections, in this order, each as a '## ' heading. Skip a section only when the
inputs give nothing to say about it:
## Overview
## Game Plan
## Key Cards
## Combos and Win Lines
## Mulligans and Opening Hands
## Piloting Tips
## Weaknesses
## Upgrades and Swaps
Keep it under {max_words} words. Be specific and concrete; no filler.
RULES:
- Mention only cards that appear in the CARDS or KEY CARDS sections below. Never invent cards, numbers, prices or win rates.
- The owner's answers are the source of intent. Expand them; do not contradict them. If an answer is missing, use the card data and say less rather than guess.
- Output ONLY the primer markdown."""

USER_TEMPLATE = """DECK: {deck_name}
COMMANDER: {commanders}
COLOR IDENTITY: {identity}
BRACKET TARGET: {bracket}
CARD COUNT: {card_count}

OWNER'S NOTES:
{notes}

CARD TYPES: {types}
TOP TAGS: {tags}
KEY CARDS:
{key_cards}

CARDS:
{cards}
"""
