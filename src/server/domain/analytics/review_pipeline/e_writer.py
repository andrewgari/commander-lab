"""Pass E output checks: prose may only restate verified Pass D evidence."""
from __future__ import annotations

import functools
import json
import re
from typing import Any, Iterable, Mapping

from src.server.domain.analytics.review_pipeline.names import normalize

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
_NUMBER = re.compile(r"(?<![\w])\d+(?:\.\d+)?%?(?![\w])")
_SWAP_ID = re.compile(r"\bsw-[a-z0-9_-]+\b", re.I)
_GRADE_LETTER = r"([A-Fa-f])([+\-\u2212]?)"
# Every way prose can assert a letter grade; each pattern captures (letter, optional +/- modifier).
_GRADE_CLAIMS = (
    re.compile(r"\bgrade\s*(?:is\s+|of\s+|:\s*)?" + _GRADE_LETTER + r"(?![\w])", re.I),
    # "the deck is an A", "earns a B+", "an A-level deck": article + capital letter, no keyword needed.
    re.compile(r"\b(?:[Aa]n?|[Tt]he)\s+([A-F])([+\-\u2212]?)(?![\w])"),
    # "rated A", "scores B+", "earns A-".
    re.compile(r"\b(?:rated|rating|graded|rates?|earns?|earned|scores?|scored|deserves?|merits?|receives?|gets?)\s+(?:at\s+)?([A-F])([+\-\u2212]?)(?![\w])", re.I),
    # "A-level", "B+ tier", "b-level", "A grade".
    re.compile(r"(?<![\w])([A-Fa-f])([+\-\u2212]?)[\s-]+(?:level|tier|grade)\b"),
    # A bare modified letter anywhere: "B+ territory", "a solid b+", "The deck is A-.".
    re.compile(r"(?<![\w])([A-Fa-f])([+\-\u2212])(?![\w])"),
)
_CONFIDENCE_CLAIM = re.compile(r"\bconfidence\s*(?:is\s+|:\s*)?(0(?:\.\d+)?|1(?:\.0+)?)\b", re.I)
_NON_NAME_TITLE_WORDS = {"a", "an", "the", "this", "that", "these", "those", "it", "its", "your", "you", "we", "our", "they", "their", "with", "for", "from", "while", "because", "when", "if", "cards", "deck", "mana", "overall",
                         "add", "cut", "swap", "keep", "drop", "play", "run", "replace", "consider", "prioritize",
                         "start", "trim", "count", "counts", "expect", "since", "so", "but", "and", "or", "also",
                         "white", "blue", "black", "red", "green", "colorless", "multicolor", "snow",
                         # color-identity names: adjectives for a deck ("This Mardu list"), never a card name on their own
                         "azorius", "dimir", "rakdos", "gruul", "selesnya", "orzhov", "izzet", "golgari", "boros", "simic",
                         "bant", "esper", "grixis", "jund", "naya", "abzan", "jeskai", "sultai", "mardu", "temur"}
_CONNECTORS = frozenset({"of", "the", "and", "to", "in", "for", "with", "at", "on", "a", "an"})
_TITLE_PHRASE = re.compile(r"\b(?:[A-Z][a-z]+(?:['’][A-Za-z]+)?)(?:\s+(?:of|the|and|to|in|for|with|at|on|a|an|[A-Z][a-z]+(?:['’][A-Za-z]+)?)){0,5}\b")


def _grade_claims(text: str) -> list[str]:
    """Every letter grade (with +/- modifier) the prose asserts, uppercased."""
    claims: list[str] = []
    for pattern in _GRADE_CLAIMS:
        for match in pattern.finditer(text):
            letter, modifier = match.group(1), match.group(2)
            if letter == "a" and not modifier:        # lowercase "a" after the word grade is an article
                continue
            claims.append(letter.upper() + modifier.replace("\u2212", "-"))
    return claims


def _decoded_strings(output: Any) -> str:
    """String values of parsed JSON output, so \\u2212-style escapes in the raw text cannot hide a grade claim."""
    if isinstance(output, str):
        return output
    if isinstance(output, Mapping):
        return "\n".join(_decoded_strings(v) for v in output.values())
    if isinstance(output, (list, tuple)):
        return "\n".join(_decoded_strings(v) for v in output)
    return ""


# Ordinary English that merely starts a sentence capitalized; never a card or proper name on its own.
_SENTENCE_STARTERS = frozenset({
    "i", "he", "she", "we", "you", "they", "it", "him", "her", "them", "us", "me",
    "this", "that", "these", "those", "there", "here", "then", "thus", "hence", "still", "yet", "now", "even",
    "most", "many", "much", "some", "any", "all", "no", "none", "every", "each", "both", "either", "neither",
    "few", "several", "more", "less", "fewer", "other", "others", "another", "such", "only", "just", "both",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "what", "which", "who", "whom", "whose", "where", "why", "how", "whether",
    "however", "therefore", "meanwhile", "otherwise", "instead", "likewise", "overall", "finally", "first", "second",
    "next", "last", "later", "once", "twice", "although", "though", "unless", "until", "after", "before", "during",
    "as", "at", "by", "in", "on", "of", "to", "is", "are", "was", "were", "be", "do", "does", "not", "without",
    # discourse adverbs, imperatives and abstract nouns a writer opens a sentence with; none is a real card name
    # (tests/test_review_pipeline_e_checker.py checks this against the card corpus). Add words here, never "any unknown word".
    "critically", "importantly", "notably", "crucially", "essentially", "ideally", "typically", "generally", "mostly",
    "mainly", "currently", "realistically", "practically", "honestly", "specifically", "additionally", "furthermore",
    "moreover", "basically", "effectively", "broadly", "largely", "roughly", "simply", "strictly", "usually", "often",
    "sometimes", "rarely", "ultimately", "initially", "ordinarily", "naturally", "obviously", "clearly", "likely",
    "apply", "reject", "avoid", "accept", "ignore", "treat", "note", "check", "ask", "decide", "confirm", "choose",
    "powerful", "flavor", "theme", "upside", "downside", "tradeoff", "caveat", "summary", "result", "risk",
    "scores", "score", "volume", "grade", "redundant", "overall", "verdict", "headline", "plan", "mana", "power",
    # ordinary review vocabulary (adjectives, abstract nouns, headings) that opens a sentence or a bullet: "Coherent Lara plan",
    # "Proposed fixes:", "Protection for Lara". Live writers open prose with these; none is a card name in the corpus.
    "acceptable", "adding", "additions", "adds", "adequate", "affordable", "alternative", "alternatives", "analysis",
    "anyone", "anything", "assessment", "available", "average", "backup", "balanced", "being", "best", "better",
    "biggest", "board", "bottom", "budget", "caution", "caveats", "central", "certain", "change", "changes", "cheap",
    "choice", "choices", "clean", "clear", "clock", "coherent", "colors", "colour", "colours", "combat", "common",
    "complete", "complex", "confidence", "confirmed", "considering", "consistency", "consistent", "core", "cost",
    "costs", "creature", "creatures", "current", "curve", "cuts", "cutting", "damage", "data", "decent", "difficult",
    "doing", "downgrade", "draw", "dropping", "early", "easily", "easy", "effect", "effects", "efficiency",
    "efficient", "engine", "engines", "equipment", "evaluation", "everyone", "everything", "evidence", "existing",
    "expected", "expects", "expensive", "exposed", "fact", "facts", "fast", "finding", "findings", "fine", "finisher",
    "finishers", "fix", "fixes", "fixing", "flexibility", "flexible", "fragile", "full", "generic", "good", "great",
    "hard", "harder", "hardest", "having", "healthy", "heavy", "high", "impact", "improve", "improvement",
    "improvements", "improves", "improving", "incomplete", "inefficient", "intended", "interaction", "keeping",
    "known", "land", "lands", "large", "late", "line", "lines", "low", "main", "major", "making", "minor", "missing",
    "nor", "notes", "nothing", "number", "numbers", "observation", "observations", "opinion", "opinions", "option",
    "options", "order", "ordering", "owned", "pace", "package", "packages", "partial", "percent", "percentage",
    "pillar", "pillars", "planned", "poor", "possible", "potential", "pressure", "price", "prices", "primary",
    "priorities", "prioritise", "priority", "probable", "problem", "problems", "proposal", "proposals", "proposed",
    "protection", "protective", "quality", "quantity", "question", "questions", "quick", "quickly", "ramp", "ratio",
    "rationale", "reason", "reasonable", "reasoning", "reasons", "recommendation", "recommended", "redundancy",
    "reliable", "reliably", "removal", "removing", "replacing", "resilience", "resilient", "riskier", "risky",
    "robust", "safe", "safer", "safest", "secondary", "sequence", "sequencing", "simple", "slow", "slowly", "small",
    "smooth", "solid", "someone", "something", "source", "sources", "sourcing", "specific", "speed", "splash",
    "splashing", "state", "strength", "strengths", "strong", "stronger", "strongest", "sufficient", "suggested",
    "suggestion", "suite", "suites", "swapping", "swaps", "synergies", "synergy", "taking", "tempo", "themes",
    "threat", "threats", "tight", "top", "total", "totals", "trade", "tradeoffs", "typical", "unavailable",
    "uncertain", "unclear", "unconfirmed", "unknown", "unowned", "unpriced", "unreliable", "unverified", "upgrade",
    "upgrades", "value", "values", "verification", "verified", "versatile", "version", "versions", "vulnerable",
    "warning", "warnings", "weak", "weaker", "weakest", "weakness", "worse", "worst",
})


def _phrase_tokens(text: str) -> tuple[str, ...]:
    """Word tokens with possessive 's removed, so \"Fauna Shaman's\" and \"Fauna Shaman\" compare equal."""
    return tuple(w[:-2] if w.endswith("'s") else w for w in _tokens(text))


def _payload_phrases(data: Mapping[str, Any]) -> tuple[str, ...]:
    """Everything the verified payload literally says (omitted[] excluded), as one possessive-stripped token stream."""
    stream: list[str] = []
    for key, value in _walk_verified(data):
        if isinstance(value, str):
            stream.extend(_phrase_tokens(value))
            stream.append("\0")                          # string boundary: a phrase never spans two strings
    return tuple(stream)


def _in_payload(phrase: str, payload: tuple[str, ...]) -> bool:
    needle = _phrase_tokens(phrase)
    if not needle or not payload:
        return False
    n = len(needle)
    return any(payload[i:i + n] == needle for i in range(len(payload) - n + 1))


def _unverified_runs(phrase: str, allowed: set[str], sentence_initial: bool,
                     payload: tuple[str, ...] = ()) -> list[str]:
    """Split a capitalized phrase into runs of words not covered by a verified name; connectors never end a run."""
    if _in_payload(phrase, payload):
        return []
    words = phrase.split()
    covered = [False] * len(words)
    for n in range(len(words), 0, -1):                # longest verified span first
        for i in range(len(words) - n + 1):
            if not any(covered[i:i + n]) and normalize(" ".join(words[i:i + n])) in allowed:
                covered[i:i + n] = [True] * n
    runs: list[tuple[list[str], int, bool]] = []
    cur: list[str] = []
    start = 0

    def flush(next_covered: bool) -> None:
        popped = False
        while cur and cur[-1].casefold() in _CONNECTORS:
            cur.pop()
            popped = True
        if cur:
            runs.append((list(cur), start, next_covered and not popped))   # "Zorblax for Sol Ring": the name is not adjacent
        cur.clear()

    for idx, (word, is_covered) in enumerate(zip(words, covered)):
        if is_covered:
            flush(True)
        elif word.casefold() in _CONNECTORS and not cur:
            continue
        else:
            if not cur:
                start = idx
            cur.append(word)
    flush(False)
    out: list[str] = []
    for run, first, before_verified in runs:
        if first == 0 and sentence_initial:           # "This Mardu ...", "Redundant Selesnya ...": drop leading prose words
            while len(run) > 1 and (run[0].casefold() in _SENTENCE_STARTERS or run[0].casefold() in _NON_NAME_TITLE_WORDS):
                run = run[1:]
                before_verified = False                # what is left is no longer a lone verb right before a verified name
        text = " ".join(run)
        if len(run) == 1 and run[0].casefold() in _NON_NAME_TITLE_WORDS:
            continue
        if _in_payload(text, payload):
            continue
        if len(run) == 1 and first == 0 and sentence_initial and run[0].casefold() in _SENTENCE_STARTERS:
            continue
        # Sentence-initial verb before a verified name ("Prioritize Sol Ring"): ordinary prose, not a card name.
        if len(run) == 1 and first == 0 and sentence_initial and before_verified:
            continue
        out.append(text)
    return out


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(normalize(text)))


def _name_tokens(name: str) -> set[tuple[str, ...]]:
    full = name.strip()
    faces = [full] + ([f.strip() for f in full.split("//")] if "//" in full else [])
    return {t for t in map(_tokens, faces) if t}


_COMMON_WORD_HEADS = frozenset({"page", "june", "loot", "prosper", "merry", "vector", "photon", "primo", "kilo", "linden",
                                "inspirit", "redshift", "flubs", "hugs", "firebird", "tombstone", "fain", "lier", "mari",
                                "mica", "slicer", "grist", "rankle", "garland", "hades", "insomnia", "electro", "cyan",
                                "curie", "carmen", "hanna", "june", "caesar", "baron", "bard", "braids", "songbird",
                                "firion", "hancock", "joshua", "kess", "rowan", "vega", "venat", "squall", "kira"})


def _legend_heads(names: Iterable[str]) -> set[tuple[str, ...]]:
    """Single-word short names of comma-titled legends ('Thrasios' for 'Thrasios, Triton Hero')."""
    heads: set[str] = set()
    vocabulary: set[str] = set()
    for name in names:
        for face in str(name).split("//"):
            if "," in face:
                head, rest = face.split(",", 1)
                head = normalize(head.strip())
                if " " not in head and len(head) > 3:
                    heads.add(head)
                vocabulary.update(_WORD.findall(normalize(rest)))
            else:
                vocabulary.update(_WORD.findall(normalize(face)))
    return {(h,) for h in heads - vocabulary - _COMMON_WORD_HEADS}


@functools.lru_cache(maxsize=1)
def _name_universe() -> frozenset[tuple[str, ...]]:
    """Multi-word real card names used to catch unverified names written in any case."""
    names: set[str] = set()
    try:
        import numpy as np
        from src.server.domain.analytics import card2vec
        with np.load(card2vec.DEFAULT_PATH) as z:
            names.update(str(n) for n in z["names"])
    except Exception:                                  # optional corpus: absence only narrows the lowercase check
        pass
    try:
        from src.server.domain.draft.deck_validator import COMMANDER_BANNED
        names.update(COMMANDER_BANNED)
    except Exception:
        pass
    universe = {t for n in names for t in _name_tokens(n) if len(t) > 1}
    universe.update(_legend_heads(names))
    return frozenset(universe)


def _omitted_names(data: Mapping[str, Any]) -> set[str]:
    """Card names that appear only in omitted[] labels: the writer must not echo them in any case."""
    out: set[str] = set()
    for item in data.get("omitted") or []:
        label = str(item.get("item", "")) if isinstance(item, Mapping) else str(item)
        swap = re.search(r"\bcut\s+(.+?)\s+->\s+add\s+(.+?)\s*$", label)
        if swap:
            out.update(g.strip() for g in swap.groups())
        elif ":" in label:
            out.add(label.split(":", 1)[0].strip())
    return {n for n in out if n and not _SWAP_ID.fullmatch(n)}


def _unverified_names_any_case(text: str, allowed: set[str], data: Mapping[str, Any],
                               known_names: Iterable[str], payload: tuple[str, ...] = ()) -> list[str]:
    """Names of real cards (or omitted ones) written in the prose, whatever the letter case, that are not verified."""
    allowed_tokens = {t for n in allowed for t in [_tokens(n)] if t}
    universe = set(_name_universe())
    single: set[tuple[str, ...]] = set()
    for name in _omitted_names(data):
        for t in _name_tokens(name):
            (single if len(t) == 1 else universe).add(t)
    for name in known_names:
        universe.update(t for t in _name_tokens(str(name)) if len(t) > 1)
        universe.update(_legend_heads([str(name)]))
    words = list(_tokens(text))
    covered = [False] * len(words)                     # words that belong to a verified name occurrence
    longest = max([len(t) for t in allowed_tokens | universe | single] or [1])
    for n in range(min(longest, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            if any(covered[i:i + n]):
                continue
            if tuple(words[i:i + n]) in allowed_tokens:
                covered[i:i + n] = [True] * n
    found: list[str] = []
    for n in range(min(longest, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            if any(covered[i:i + n]):
                continue
            gram = tuple(words[i:i + n])
            if gram in allowed_tokens:
                continue
            if gram in universe or gram in single:
                if _in_payload(" ".join(gram), payload):   # the verified payload literally says it
                    covered[i:i + n] = [True] * n
                    continue
                found.append(" ".join(gram))
                covered[i:i + n] = [True] * n
    return found


def _walk_verified(value: Any):
    """Walk only reviewable evidence; verifier diagnostics are not writer input."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "omitted":
                continue
            yield key, item
            yield from _walk_verified(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, (str, int, float)) and not isinstance(item, bool):
                yield None, item                          # a bare list element (win_paths, success_criteria, ...) is evidence too
            yield from _walk_verified(item)


def _short_names(name: str) -> set[str]:
    """'Thrasios, Triton Hero' is also referred to as 'Thrasios' and 'Triton Hero'."""
    out: set[str] = set()
    for face in name.split("//"):
        if "," in face:
            out.update(normalize(part.strip()) for part in face.split(",") if part.strip())
    return out


def _verified_names(data: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for key, value in _walk_verified(data):
        if key == "name" and isinstance(value, str):
            full = value.strip()
            names.add(normalize(full))
            if "//" in full:
                names.update(normalize(face.strip()) for face in full.split("//"))
            names.update(_short_names(full))
    return names


def _verified_numbers(data: Mapping[str, Any]) -> set[str]:
    return {m.group(0).rstrip("%") for _, value in _walk_verified(data) if isinstance(value, (str, int, float))
            for m in _NUMBER.finditer(str(value))}


def check_writer_output(text: str, verified: Mapping[str, Any], known_names: Iterable[str] = ()) -> list[str]:
    """Return policy violations; verified contains only the completed, Pass-D-approved payload."""
    errors: list[str] = []
    allowed_names = _verified_names(verified)
    try:
        output = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        output = None
    source_verdict = verified.get("verdict") if isinstance(verified.get("verdict"), Mapping) else {}
    if not source_verdict or "grade" not in source_verdict or "confidence" not in source_verdict:
        errors.append("verified input is missing grade or confidence")
    if not isinstance(output, Mapping) or not isinstance(output.get("verdict"), Mapping):
        errors.append("writer output must be JSON with a verdict object")
        output_verdict = None
    else:
        output_verdict = output["verdict"]
    if isinstance(output_verdict, Mapping) and source_verdict:
        for field in ("grade", "confidence"):
            if field not in output_verdict or output_verdict.get(field) != source_verdict.get(field):
                errors.append(f"verdict {field} does not exactly match the verified input")
        for match in _CONFIDENCE_CLAIM.finditer(text):
            try:
                matches_source = float(match.group(1)) == float(source_verdict["confidence"])
            except (TypeError, ValueError):
                matches_source = False
            if not matches_source:
                errors.append("verdict confidence claim does not match the verified input")
    if source_verdict.get("grade") is not None:
        expected = str(source_verdict["grade"]).strip().upper().replace("\u2212", "-")
        for claim in _grade_claims(text) + _grade_claims(_decoded_strings(output)):
            if claim != expected:
                errors.append("verdict grade claim does not match the verified input")
                break
    payload = _payload_phrases(verified)
    for match in _TITLE_PHRASE.finditer(text):
        before = text[:match.start()].rstrip(" \t\"'([{*_>-")
        sentence_initial = not before or before[-1] in ".!?:;\n"
        for phrase in _unverified_runs(match.group(0).strip(), allowed_names, sentence_initial, payload):
            errors.append(f"unverified card name or proper-name phrase: {phrase}")
    for name in _unverified_names_any_case(text, allowed_names, verified, known_names, payload):
        message = f"unverified card name or proper-name phrase: {name}"
        if not any(e.casefold() == message.casefold() for e in errors):
            errors.append(message)
    allowed_numbers = _verified_numbers(verified)
    for match in _NUMBER.finditer(text):
        number = match.group(0).rstrip("%")
        if number not in allowed_numbers:
            errors.append(f"unverified number: {match.group(0)}")
    survivors = {str(item.get("id")) for item in verified.get("swaps", []) if isinstance(item, Mapping)}
    if isinstance(verified.get("top_changes"), Mapping):
        survivors &= set(map(str, verified["top_changes"].get("swap_ids", [])))
    for swap_id in _SWAP_ID.findall(text):
        if swap_id not in survivors:
            errors.append(f"unverified swap id: {swap_id}")
    return errors


def generate_checked(writer, verified: Mapping[str, Any], *, max_attempts: int = 2, known_names: Iterable[str] = ()) -> str | None:
    """Call writer with the verified payload, retry policy violations at most twice, then drop."""
    for _ in range(max(0, max_attempts) + 1):
        text = writer(verified)
        if not check_writer_output(text, verified, known_names):
            return text
    return None


def render_user_input(verified: Mapping[str, Any], review_preferences: str = "") -> str:
    """Render only the verified JSON and the explicitly supplied profile voice preference."""
    from src.server.domain.analytics.review_pipeline import prompts

    template = prompts.load("e_writer")
    return template.render("user", verified_json=json.dumps(verified, ensure_ascii=False, sort_keys=True),
                           review_preferences=review_preferences or "direct, specific, no padding, no cheerleading")
