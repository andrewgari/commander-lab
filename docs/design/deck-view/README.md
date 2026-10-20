# DV-1: deck-first reference

**Suggestions only. Do not merge the reference PR.**

Start with [the ranked audit](../../DESIGN_DECK_VIEW.md).

| Open | Purpose |
|---|---|
| [overview.html](overview.html) | Three alternative main-screen directions; B is recommended |
| [workspaces.html](workspaces.html) | Nine focused page proposals; proposed Cards/Settings, not live routes |
| [evidence.html](evidence.html) | Twelve real deck surfaces, each at 1440 and 390 |
| [preview-b-1440.png](preview-b-1440.png) / [preview-b-390.png](preview-b-390.png) | Recommended direction, cropped preview |
| [current/manifest.json](current/manifest.json) | Real-page capture inventory and blocked write requests |
| [verification.json](verification.json) | Offline browser verification results |

## Run the previews

From the repository root:

```sh
python3 -m http.server 18932 --bind localhost
```

Open `http://localhost:18932/docs/design/deck-view/overview.html`.

No build or external service required. Prototype fixture values are observed deck examples, never seeded application vocabulary. Six card illustrations are stored locally with [credits](art-sources.json).

## Recheck

The verification helper requires Python's Playwright package and installed Chromium (checked in the audit environment); `package.py` also uses Pillow. These are audit tooling dependencies, not new app requirements.

```sh
python3 docs/design/deck-view/verify.py
python3 docs/design/deck-view/package.py
node --check docs/design/deck-view/mockup.js
```

`verify.py` writes rendered mockup PNGs under `proposed/`. Production captures are kept separately under `current/`. `capture.py` is the read-only live-capture helper; it aborts all non-GET/HEAD/OPTIONS requests before navigation. Do not remove the guard to make suggestion failures disappear. Read the audit limitations before interpreting those screenshots.

The prototypes intentionally do not implement saves, sync, deletion, new simulations, generation jobs or backend state. Working interactions are navigation, native evidence disclosures, example-card search/view selection and a clearly labelled local-only Tune staging demonstration.
