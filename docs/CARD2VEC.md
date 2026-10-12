# card2vec: "played with" similarity (AN-7)

Spec: `docs/ANALYTICS_SPEC.md` Phase 2. Code: `analytics/card2vec.py` (load +
score), `scripts/fetch_card2vec_data.py` + `scripts/train_card2vec.py`
(offline), artifact `analytics/data/card2vec.npz`. Tests:
`tests/test_card2vec.py`.

## What it is

word2vec over real Commander decklists. Each deck is a sentence and each card
is a word, so two cards end up close when they keep turning up in the same
decks. The model never sees card text. That makes it a different signal from
pillar fit (bge-small on oracle text):

- pillar fit: "this card *reads like* your plan"
- played with: "decks like this one *actually run* this card"

## Where it shows up

- **Weak performers** (Stats → Tune): a 4th equally weighted part,
  `played = 1 − played-with score`, with the reason "rarely played with the
  rest". When the model is loaded for a deck, every card gets this part. A
  card it has never seen (a new set, a very rare card) gets the deck's median
  played score, a neutral "typical card here", because unknown isn't weak.
  That way every card is a mean over the same parts.
- **Tuner score**: the mean of edges / fit / EDHREC gets `played` as a 4th
  part for both the cut and every candidate (owned or buy), with the same
  neutral fill for unknown cards. So the gap is always over the same parts.
  An unknown candidate can't look like an upgrade just because a known cut
  scored low on a part the candidate skipped. Swap "why" bars get a
  **Played** bar (real value only), and the weak table gets a **Played
  with** column (— = not in the model).
- **Stats page**: "Played with · most / least" panels (Identity section) with
  coverage ("63/65 known · 24,784 decks") and the cards the model doesn't know.
- **API**: `GET /api/decks/{id}/played-with?top=8`, and
  `GET /api/card2vec?card=Sol%20Ring&n=10` (model info + nearest neighbours).

## Math

    deck centroid  normalised mean of the deck's non-land, non-basic cards
                   the model knows (commanders included)
    deck card      sim = cos(card, centroid of the OTHER deck cards)
    candidate      sim = cos(card, centroid of the deck)
    score          (sim − sim_floor) / (sim_ceil − sim_floor), clamped 0..1

`sim_floor` and `sim_ceil` come from the artifact. They were measured on
held-out decks the model didn't train on: `sim_floor` is the median sim of a
random vocabulary card to a deck, `sim_ceil` is the 90th percentile sim of a
card to its own deck. So 0 is "a random card" and 1 is "clearly belongs".

Lands are left out of the centroid and aren't scored (every deck plays Command
Tower, so lands only add noise).

## Data source (and why this one)

| source | used? | why |
|---|---|---|
| **EDHTop16 public GraphQL API** (TopDeck.gg tournament decklists) | **yes** | Its [terms](https://edhtop16.com/terms) explicitly offer the API "for developers and data enthusiasts". The conditions are: don't overload it (we fetch sequentially, 10 tournaments per request, 3 s apart, offline only) and don't imply endorsement. robots.txt allows everything. |
| **MTGJSON AllDeckFiles**, `type == "Commander Deck"` | **yes** | MIT licence. Official precons (~200) cover casual cards and older commanders that tournaments never play. |
| EDHREC | no | ToS 2(d): no copying or downloading "in any form or by any means". The app already reads one EDHREC commander page per deck for inclusion %, but bulk-pulling decklists for training is not OK. |
| Archidekt / Moxfield crawl | no | Archidekt ToS forbids copying/downloading and automated queries; Moxfield's ToS page needs JS and the site is known to block scrapers. |
| HF `nsaroiu/moxfield-dump` (122k decks) | no | Gated, and the card itself says "No license is asserted … check Moxfield's terms" (a Moxfield scrape). |
| HF `bingbong-sempai/edh-decks-250810` (2.3 GB) | no | No README or licence, and the records are EDHREC deck pages (urlhash, salt, edhrec_tags), i.e. an EDHREC scrape. |
| Kaggle "EDHREC Top 100 Commanders Decklists" | no | Also an EDHREC scrape. |
| TopDeck.gg API directly | not needed | Free, but needs an API key and visible attribution. EDHTop16 already serves the same TopDeck data with no key. |
| cEDH Decklist Database (MIT) | no | Only ~140 entries, all Moxfield links. |

**Bias to know about**: tournament decks are mostly competitive (cEDH). The
model knows staples and competitive packages very well and casual pet cards
less well. The precons offset that a little. Cards it doesn't know are skipped
rather than punished, and the Stats panel lists them.

## Rebuilding the artifact

```bash
pip install gensim numpy                  # offline only, not in requirements.txt
python scripts/fetch_card2vec_data.py data/edhtop16.ndjson      # resumable, polite
curl -O https://mtgjson.com/api/v5/AllDeckFiles.tar.xz && tar -xJf AllDeckFiles.tar.xz
python scripts/train_card2vec.py --edhtop16 data/edhtop16.ndjson \
    --mtgjson AllDeckFiles --out analytics/data/card2vec.npz
```

Training: skip-gram, negative sampling 10, 64 dimensions, min_count 5, 15
epochs, window = the whole deck (`window=200, shrink_windows=False`, so every
card is context for every other card; decks are reshuffled every epoch), `sample=1e-3` to downsample staples like Sol Ring.
Exact duplicate lists (one player registering the same deck at several
events) are kept once. Basic lands are dropped. Names use the app's key
(lower-cased front face, so `A // B` and `A` match). Vectors are L2-normalised
and stored as float16 in a ~1-2 MB `.npz` that the image ships. No torch, no
network at runtime. `CARD2VEC_PATH` overrides the path.

The artifact's `meta` holds the source counts, date range, parameters and
calibration; `GET /api/card2vec` returns it.
