# Mechanically similar cards (AN-8)

A third card-similarity signal, next to the other two:

| signal | module | meaning |
| --- | --- | --- |
| pillar fit (AN-3) | `analytics/embeddings.py` | the card's text reads like the deck's plan |
| played-with (AN-7) | `analytics/card2vec.py`, `GET /api/card2vec` | decks run these cards together |
| **mechanically similar (AN-8)** | `analytics/mech_similar.py`, `GET /api/similar` | the rules text does the same thing |

It is **not synergy** and **not "played with"**: Sol Ring's neighbours are other mana rocks.

## Data

Precomputed 768-d `gte-modernbert-base` vectors, unit-normalised, built from mechanics-only JSON
(copy of `minimaxir/mtg-embeddings`: ~33k MTGJSON cards deduped by name, up to Aetherdrift).
Source: private HF bucket `CovaDax/mtg-embeddings-bucket`, file `mtg_embeddings.parquet` (~94 MB).
Nothing is embedded at runtime; newer cards simply return no neighbours.

Parquet schema (checked with pyarrow before writing the converter): name column `name`, vector
column `embedding` (a list of float32, 768 per row), plus `x_2d` / `y_2d` UMAP columns.

## Artifact

`mech_similar.npz` (float16 vectors, ~45 MB; keys = card2vec `name_key`, names = display names,
meta JSON). It is **not in git and not in the image**: the HF token never enters the image.

Resolution order: `MECH_SIMILAR_PATH`, else `/data/mech_similar.npz` (the persistent volume),
else `analytics/data/mech_similar.npz`. Loaded lazily, once per process. A missing or bad file logs
one warning and disables the signal (empty results); the app keeps running.

### Build / refresh

Run off-box (the app image has no pyarrow), then put the file on the `/data` volume:

```
export HF_TOKEN=...   # fine-grained, read access to the bucket; never commit it
uv run --with pyarrow --with numpy --with pydantic python scripts/build_mech_similar_artifact.py /tmp/mech_similar.npz
cp /tmp/mech_similar.npz /mnt/user/appdata/commander-lab/data/mech_similar.npz   # prod bind mount of /data
```

Dev compose uses the named volume `app_data:/data`. Use `--parquet FILE` to convert an
already-downloaded parquet. Restart the app to pick up a new artifact.

## API

`GET /api/similar?card=&n=10` has the same shape as `/api/card2vec`:
`{success, model, card, known, neighbours: [{name, sim}]}`; `n` is clamped to 1..50. An unknown
card gives 200 with `known: false` and `neighbours: []`.

## Oracle feed

`analytics/theory.py` `build_request` adds `mechanically_similar` to the Theory request (the only
place Commander Lab builds oracle context; oracle has no tools and no HF token). Caps, module
constants in `analytics/mech_similar.py`: `FEED_MAX_CARDS` cards (commander, then pets, then the
decklist in order; basics skipped), `FEED_PER_CARD` neighbours each, `FEED_MIN_SIM` floor.
Documented for oracle in `docs/oracle/deck-theory/SKILL.md`.
