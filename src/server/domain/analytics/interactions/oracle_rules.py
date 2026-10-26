"""
Deterministic produce/consume rules over Scryfall oracle text.

This is the "oracle data" half of the deterministic pass (the other half is
the Scryfall Tagger otag index, see tagger.ScryfallSource.otag_index). Rules
are intentionally conservative regexes: a miss is fine (the card falls
through to the LLM pass if nothing else matched), a false positive becomes a
wrong edge, so each rule targets wording that reliably means the resource.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .vocab import RESOURCE_KEYS

_REMINDER = re.compile(r"\([^)]*\)")
_NUM = r"(?:a|an|one|two|three|four|five|six|seven|x|that many|\d+|any number of|up to (?:one|two|three|\d+))"


def _normalize_text(card: dict) -> str:
    text = card.get("oracle_text") or ""
    text = _REMINDER.sub("", text)
    name = card.get("name") or ""
    names = {name}
    for face in name.split(" // "):
        names.add(face)
        if "," in face:  # "Kaalia, ..." style short names
            names.add(face.split(",")[0])
    for n in sorted((n for n in names if n), key=len, reverse=True):
        text = text.replace(n, "~")
    return text.lower()


def _type_line(card: dict) -> str:
    return (card.get("type_line") or card.get("type") or "").lower()


def _activated_costs(text: str) -> List[str]:
    """Cost part ('{2}, sacrifice a creature') of every activated ability."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        cost = line.split(":", 1)[0]
        # Activated-ability costs are short and start with a symbol or a verb
        # like "sacrifice"/"pay"/"discard"/"exile"/"remove"/"tap".
        if len(cost) > 80:
            continue
        if re.match(r"^(\{|sacrifice|pay|discard|exile|remove|tap|return|put|collect)", cost):
            out.append(cost)
    return out


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def is_trivial(card: dict) -> bool:
    """True when there is nothing to tag: basic lands, plain mana lands,
    vanilla / keyword-only creatures. These never go to the LLM."""
    tl = _type_line(card)
    if "basic" in tl and "land" in tl:
        return True
    text = _normalize_text(card)
    meaningful = []
    for line in text.split("\n"):
        line = line.strip().rstrip(".")
        if not line:
            continue
        if "land" in tl and re.match(r"^\{t\}(?:, pay \d+ life)?: add ", line):
            continue
        if "land" in tl and re.match(r"^(?:\{[^}]+\}(?:, )?)*\{t\}[^:]*: add ", line):
            continue  # filter-land style mana abilities
        if "land" in tl and re.match(r"^(?:this land|~) enters tapped(?: unless [^.]*)?$", line):
            continue
        if "land" in tl and re.match(r"^as (?:this land|~) enters, you may (?:pay \d+ life|reveal [^.]*)\. if you don't, (?:it|this land|~) enters tapped$", line):
            continue
        if "land" in tl and re.match(r"^(?:this land|~) enters tapped\. as it enters, choose a color$|^\{t\}: add one mana of the chosen color$", line):
            continue
        if re.match(r"^~ enters (?:the battlefield )?tapped", line):
            continue
        # keyword-only line ("flying, vigilance", "ward {2}", "protection from red")
        if re.match(r"^[a-z' -]+(?: \{[^}]+\}| \d+| from [a-z]+)?(?:, [a-z' -]+(?: \{[^}]+\}| \d+| from [a-z]+)?)*$", line) and len(line) < 60:
            continue
        meaningful.append(line)
    return not meaningful


def tag_from_oracle(card: dict) -> Tuple[List[str], List[str]]:
    """(produces, consumes) resource keys for one card, in vocabulary order."""
    text = _normalize_text(card)
    tl = _type_line(card)
    mana_cost = (card.get("mana_cost") or "").lower()
    is_land = "land" in tl.split("//")[0]
    costs = _activated_costs(text)
    p, c = set(), set()

    # ---- tokens -------------------------------------------------------
    creates_token = _search(r"\bcreates? [^.]*\btokens?\b", text) or _search(r"\bpopulate\b|\bamass\b|\bincubate\b", text)
    if creates_token:
        p.add("tokens")
    if _search(r"\b(?:treasure|clue|food|blood|map|powerstone|junk|gold|incubator) tokens?\b", text) and creates_token:
        p.add("artifacts")
    if _search(r"\bartifact [^.]*\btoken", text) and creates_token:
        p.add("artifacts")
    if _search(r"\benchantment [^.]*\btoken|\brole token", text) and creates_token:
        p.add("enchantments")
    creature_token = creates_token and (
        _search(r"\d+/\d+ [^.]*creature tokens?|creature tokens?|\bamass\b|\bpopulate\b", text)
    )
    if creature_token:
        p.add("etb")
    if _search(r"\btokens? you control\b|\bfor each token\b|\bwhenever [^.]*\btokens? (?:you control )?(?:enters|is created|are created)\b|\bsacrifice (?:a|an|another) token\b|\bif (?:one or more )?tokens? would be created\b|\bcreate (?:that many|twice that many) (?:of those )?tokens? instead\b", text):
        c.add("tokens")

    # ---- sacrifice / death -------------------------------------------
    outlet = False
    outlet_creature = False
    for cost in costs:
        m = re.search(r"sacrifice (?:a|an|another|one or more|two|three|x|any number of) ([a-z ,/-]+)", cost)
        if m:
            outlet = True
            target = m.group(1)
            if re.search(r"creature|permanent|nonland|token", target):
                outlet_creature = True
    if outlet:
        p.add("sacrifice")
    if outlet_creature:
        p.add("death")
    if _search(r"\b(?:each|target) (?:player|opponent)s? sacrifices? (?:a|an|two|three|x|that many|\w+) [^.]*creature", text):
        p.add("death")
    if _search(r"\bdestroy all (?:other )?(?:nonland permanents|creatures|nontoken creatures)\b|\beach creature deals damage to itself|\ball creatures get -\d+/-\d+", text):
        p.add("death")
    if _search(r"\bwhenever you sacrifice\b|\bif you sacrificed\b|\bsacrificed this turn\b|\bwhenever (?:a|an|another) [^.]*is sacrificed\b|\bwhenever a player sacrifices\b", text):
        c.add("sacrifice")
    if _search(r"\bwhenever [^.]*\bdies\b|\bwhenever [^.]*put into (?:a|your|an opponent's) graveyard from the battlefield\b|\b(?:creatures?|permanents?) (?:that )?died this turn\b|\bif a creature died this turn\b", text):
        # "When ~ dies" (self only) is also a death payoff for sac outlets.
        c.add("death")

    # ---- etb / blink ---------------------------------------------------
    if _search(r"\bonto the battlefield\b", text) and not _search(r"^(?:[^.\n]*)~ enters (?:the battlefield )?tapped", text):
        if not _search(r"search your library for [^.]*\blands? cards?[^.]*onto the battlefield", text) or _search(r"creature card[^.]*onto the battlefield", text):
            p.add("etb")
    if _search(r"\bexile [^.]*(?:then )?return (?:it|them|that card|those cards) to the battlefield\b|\bexile [^.]*\. return (?:it|them|that card|those cards) to the battlefield\b", text):
        p.add("etb")
    if _search(r"\bwhen(?:ever)? ~ enters\b|\bwhen(?:ever)? ~ enters the battlefield\b", text):
        c.add("etb")
    if _search(r"\bwhenever (?:a|an|another|one or more) (?:nontoken )?(?:other )?(?:[a-z]+ )?(?:creature|permanent|artifact|enchantment)s? [^.]*\benters?\b", text) and not _search(r"\bland [^.]*enters\b", text):
        c.add("etb")

    # ---- counters --------------------------------------------------------
    if _search(rf"\bput {_NUM} [^.]*\+1/\+1 counters?\b|\benters (?:the battlefield )?with {_NUM} [^.]*\+1/\+1 counters?\b|\bproliferate\b|\bdouble the number of [^.]*counters\b|\badapt \d|\bbolster \d|\bsupport \d|\bmonstrosity \d|\bmodular\b|\bevolve\b|\bgraft\b|\boutlast\b|\btraining\b|\breinforce\b", text):
        p.add("counters")
    if _search(r"\bwith (?:a|one or more|\w+) \+1/\+1 counters? on (?:it|them)\b|\bfor each \+1/\+1 counter\b|\bwhenever (?:one or more )?\+1/\+1 counters? (?:are|is) put\b|\bremove (?:a|an|one|two|x|\w+) \+1/\+1 counters?\b|\bcounters? on (?:it|them|~|each)\b[^.]*\b(?:equal|for each)\b|\bproliferate\b|\bhad counters\b|\bif one or more [^.]*counters would be put\b", text):
        c.add("counters")

    # ---- graveyard -------------------------------------------------------
    if _search(r"\bmill (?:a|one|two|three|four|five|x|that many|\d+) cards?\b|\bsurveil \d|\bthen discard\b|\bdiscard (?:a|one|two|x|\d+|your hand|any number of) cards?\b|\binto your graveyard\b|\bdredge \d", text):
        if not _search(r"\b(?:target|each) opponent (?:mills|discards)\b", text) or _search(r"\byou (?:mill|discard)|\bmill (?:a|one|two|three|x|\d+)", text):
            p.add("graveyard")
    if _search(r"\bfrom your graveyard\b|\bcards? in your graveyard\b|\bflashback\b|\bescape\b|\bunearth\b|\bembalm\b|\beternalize\b|\bdelve\b|\bthreshold\b|\bjump-start\b|\bretrace\b|\bscavenge\b|\bdisturb\b|\bencore\b|\bgraveyard (?:has|contains)\b", text):
        c.add("graveyard")

    # ---- mana --------------------------------------------------------------
    if not is_land:
        if _search(r"\badd \{|\badd (?:one|two|three|x|that much|an amount of) (?:mana|\{)|\badd [^.]*mana of any\b|\btreasure tokens?\b", text):
            p.add("mana")
        if _search(r"search your library for [^.]*\blands? cards?\b[^.]*onto the battlefield", text):
            p.add("mana")
        if _search(r"\bcost(?:s)? \{\d+\} less\b|\bcosts? \{\d+\} less to cast\b", text):
            p.add("mana")
    if "{x}" in mana_cost or any("{x}" in cost for cost in costs):
        c.add("mana")
    for cost in costs:
        if "{t}" in cost or "{q}" in cost:
            continue
        if re.search(r"\{(?:\d+|[wubrgc])\}", cost) and not re.match(r"^\{(?:\d+|[wubrgc])\}$", cost.strip()):
            c.add("mana")
            break
        if re.match(r"^\{(?:\d+)\}", cost.strip()):
            c.add("mana")
            break

    # ---- cards ------------------------------------------------------------
    if _search(rf"(?<!opponent )(?<!player )(?<!opponents )\bdraws? {_NUM} (?:additional )?cards?\b|\bdraw cards equal\b|\binvestigate\b|\bconnive|\bclue tokens?\b", text):
        if not _search(r"\b(?:target|each) opponent draws\b", text) or _search(r"\byou draw|\bdraw (?:a|one|two|three|x|\d+) cards?", text):
            p.add("cards")
    if _search(r"\bwhenever you draw\b|\bdraw your (?:second|third) card\b|\bcards? in your hand\b|\bno cards in hand\b|\bfor each card in your hand\b|\bmaximum hand size\b", text):
        c.add("cards")

    # ---- life gain ------------------------------------------------------
    if _search(r"\byou gain (?:\d+|x|that much|life|an amount)|\bgains? (?:\d+|x|that much) life\b|\blifelink\b|\bgain life equal\b", text):
        p.add("life_gain")
    if _search(r"\bwhenever you gain life\b|\bif you (?:would )?gain(?:ed)? life\b|\blife you(?:'ve)? gained\b|\bif you would gain life\b|\bwhenever you gain \d", text):
        c.add("life_gain")

    # ---- opponent life loss -----------------------------------------------
    if _search(r"\b(?:each opponent|target opponent|that player|target player|each player|defending player|opponents?) (?:each )?loses? (?:\d+|x|that much|half|life equal)|\bdeals? (?:\d+|x) damage to each opponent\b|\bdeals? (?:\d+|x) damage to (?:target|any target|that player|each player)", text):
        p.add("life_loss")
    if _search(r"\bwhenever an opponent loses life\b|\bopponents? (?:who )?lost life\b|\blife (?:an opponent|opponents) lost\b|\blost life this turn\b", text):
        c.add("life_loss")

    # ---- attacks ------------------------------------------------------
    if _search(r"\b(?:have|has|gains?|gain) haste\b|\badditional combat phase\b|\bextra combat\b|\battack[s]? each combat\b|\bcreatures you control have haste\b", text):
        p.add("attacks")
    if _search(r"\bwhenever [^.]*\battacks?\b|\bwhenever you attack\b|\battacking creatures?\b|\bdeals combat damage to (?:a player|an opponent)\b|\bmyriad\b|\bmelee\b|\bbattle cry\b|\bexalted\b|\bannihilator\b", text):
        c.add("attacks")

    # ---- spells ------------------------------------------------------------
    if "instant" in tl or "sorcery" in tl:
        p.add("spells")
    if _search(r"\bcopy target instant or sorcery\b|\bcopy (?:that|the next) (?:instant|sorcery|spell)\b", text):
        p.add("spells")
    if _search(r"\bwhenever you cast (?:an?|your first|your second)? ?(?:instant|sorcery|noncreature)\b|\bmagecraft\b|\bprowess\b|\binstant (?:and|or) sorcery spells you cast\b|\binstant and sorcery cards? in your graveyard\b", text):
        c.add("spells")

    # ---- artifacts -----------------------------------------------------------
    if "artifact" in tl:
        p.add("artifacts")
    if _search(r"\bartifacts? you control\b|\bwhenever (?:an?|another|one or more) (?:nontoken )?artifacts? (?:you control )?enters?\b|\bfor each artifact\b|\baffinity for artifacts\b|\bmetalcraft\b|\bimprovise\b|\bwhenever you cast an artifact\b|\bartifact spells? you cast\b", text):
        c.add("artifacts")

    # ---- enchantments -------------------------------------------------------
    if "enchantment" in tl:
        p.add("enchantments")
    if _search(r"\benchantments? you control\b|\bconstellation\b|\bwhenever (?:an?|another) (?:nontoken )?enchantment (?:you control )?enters\b|\bwhenever you cast an enchantment\b|\bfor each enchantment\b|\benchantment spells? you cast\b", text):
        c.add("enchantments")

    # ---- lands ---------------------------------------------------------------
    if _search(r"\bplay (?:an|two) additional lands?\b|\bput (?:a|up to \w+) land cards? [^.]*onto the battlefield\b|\bsearch your library for [^.]*\blands? cards?\b[^.]*onto the battlefield\b|\breturn (?:a|target) land card [^.]*to the battlefield\b", text):
        p.add("lands")
    if _search(r"\blandfall\b|\bwhenever (?:a|one or more) lands? (?:you control )?enters?\b|\bfor each land you control\b|\blands you control\b", text):
        c.add("lands")

    return [k for k in RESOURCE_KEYS if k in p], [k for k in RESOURCE_KEYS if k in c]


def explain(card: dict) -> Dict[str, List[str]]:
    p, c = tag_from_oracle(card)
    return {"produces": p, "consumes": c}
