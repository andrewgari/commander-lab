# Decklist Paste/Import Syntax Spec — 5 Export Formats

Research deliverable for task t_26c77fdf (parent: t_3ba5516a "Copy decklist to clipboard").
Source: official TCGplayer Mass Entry help article (360055768913), Forge `.dck` format (Card-Forge repo/wiki, issue #5261), Archidekt/Moxfield observed export conventions corroborated by multiple importer docs. Verified 2026-10-26.

Example deck throughout: commander `Atraxa, Praetors' Voice` (2X2 #190), regular card `Sol Ring` (CMM #400), sideboard card `Rest in Peace` (RVR #24? — use RVR only if known; otherwise omit printing).

---

## 1. Plain (agreed Commander Lab default)

- **Line format:** `<qty> <Name>` — space-separated, no `x`.
- **Commander:** listed as the **first line** of the output (no marker).
- **Printing (toggle on):** append ` (SET) CN` — Moxfield/Archidekt style, uppercase set code: `1 Sol Ring (CMM) 400`.
- **Sideboard/Maybeboard:** when included, separate with a `Sideboard` / `Maybeboard` header line and a preceding blank line.

```
1 Atraxa, Praetors' Voice
1 Sol Ring
1 Arcane Signet

Sideboard
1 Rest in Peace
```

With printing:

```
1 Atraxa, Praetors' Voice (2X2) 190
1 Sol Ring (CMM) 400
```

---

## 2. Moxfield

- **Line format:** `<qty> <Name> (SET) CN` — space qty, no `x`. Foil/etched/alter markers `*F*` / `*E*` / `*A*` and `#tags` trail the line.
- **Commander:** `*CMDR*` suffix on the commander's line (same marker convention as `*F*`; widely documented and recognized by Moxfield and third-party importers).
- **Printing:** ` (SET) CN` — **uppercase** set code, collector number as bare token after the closing paren: `1 Sol Ring (CMM) 400`.
- **Sections:** Moxfield's flat text export has no header lines; sideboard included means the cards are appended in the same flat list (third-party parsers also accept `SB: ` line prefixes and `Sideboard` headers, but Moxfield's own paste box takes the flat list and you move cards in the UI).

Without printing:

```
1 Atraxa, Praetors' Voice *CMDR*
1 Sol Ring
```

With printing:

```
1 Atraxa, Praetors' Voice (2X2) 190 *CMDR*
1 Sol Ring (CMM) 400
```

Sideboard (when toggled on): append `SB: 1 Rest in Peace (RVR) 24` style lines (Moxfield accepts `SB:` prefixes on paste), or omit board suffix entirely and note the limitation — see Oracle questions.

---

## 3. Archidekt

- **Line format:** `<qty>x <Name> (set) CN` — qty with **`x` suffix**, **lowercase** set code: `1x Sol Ring (cmm) 400`. Foil `*F*` trails.
- **Commander:** inline category tag `[Commander{top}]` after the printing.
- **Printing:** ` (set) CN` lowercase set code: `1x Sol Ring (cmm) 400`.
- **Sections:** inline bracketed categories with flags, e.g. `[Sideboard{noDeck}{noPrice}]`, `[Maybeboard{noDeck}{noPrice}]` appended to each line. (Archidekt also accepts its export without any category tags; the importer maps untagged lines into the main deck.)

Without printing:

```
1x Atraxa, Praetors' Voice [Commander{top}]
1x Sol Ring
```

With printing:

```
1x Atraxa, Praetors' Voice (2x2) 190 [Commander{top}]
1x Sol Ring (cmm) 400
```

With boards included:

```
1x Rest in Peace (rvr) 24 [Sideboard{noDeck}{noPrice}]
1x Ponder [Maybeboard{noDeck}{noPrice}]
```

Note: Archidekt custom tags (`^Have,#37d67a^`) are NOT emitted — they're user annotations, not deck structure.

---

## 4. Forge (.dck)

INI-style file format (Forge also accepts pasted plain lists, but the canonical Forge format is `.dck`).

- **Sections:** `[metadata]` (`Name=<deck name>`), `[commander]`, `[main]`, `[sideboard]`. Maybeboard has no native section — omit or fold into sideboard (see Oracle questions).
- **Line format:** `<qty> <Name>` or with printing `<qty> <Name>|<SET>` (pipe-delimited set code, **uppercase**; a third pipe field is the art-variant index, e.g. `|1`, not the collector number).

```
[metadata]
Name=Example Deck
[commander]
1 Atraxa, Praetors' Voice
[main]
1 Sol Ring
1 Arcane Signet
[sideboard]
1 Rest in Peace
```

With printing (set code only — Forge does not use collector numbers):

```
[commander]
1 Atraxa, Praetors' Voice|2X2
[main]
1 Sol Ring|CMM
```

---

## 5. TCGplayer (Mass Entry)

Official format from TCGplayer help article 360055768913: each line is **Quantity → Card Name → [Set Code] → Card Number**, set code in **square brackets**, uppercase.

- **Line format:** `<qty> <Name>`; with printing `<qty> <Name> [SET] CN` — e.g. `1 Lightning Bolt [SLD] 84`.
- **Commander:** no commander concept in Mass Entry — it's a shopping cart tool. The commander is just another line; per the export feature spec it should be the **first line** for readability.
- **Printing:** ` [SET] CN`. Number may be dropped (any art from that set) or both dropped (any printing).
- **Sections:** none. Headers/blank lines are not meaningful; emit a flat list only. Sideboard/maybeboard, when included, are appended flat (recommend NOT emitting them for TCGplayer since the target is a buy list — flag in UI copy).

Without printing:

```
1 Atraxa, Praetors' Voice
1 Sol Ring
```

With printing:

```
1 Atraxa, Praetors' Voice [2X2] 190
1 Sol Ring [CMM] 400
```

⚠️ Caveat: TCGplayer set codes are its own product-set naming, not always Scryfall's — e.g. Secret Lair Drop is `SLD` (matches), but some commander/UB sets differ. Wrong/mismatched codes can zero out a search. See Oracle questions.

---

## Format comparison table

| Format    | Qty      | Printing           | Commander marker         | Boards                          |
|-----------|----------|--------------------|--------------------------|---------------------------------|
| Plain     | `1 `     | `(SET) CN` upper   | first line               | `Sideboard`/`Maybeboard` header |
| Moxfield  | `1 `     | `(SET) CN` upper   | `*CMDR*` suffix          | flat / `SB:` prefix             |
| Archidekt | `1x `    | `(set) CN` lower   | `[Commander{top}]`       | `[Sideboard{noDeck}{noPrice}]`  |
| Forge     | `1 `     | `\|SET` (pipe, no CN) | `[commander]` section | `[sideboard]` section           |
| TCGplayer | `1 `     | `[SET] CN` bracket | none (first line)        | none (flat)                     |

## Open questions for Oracle (card/printing-name edge cases)

1. **Moxfield sideboard delimiter:** Moxfield's own paste box is a flat list; is `SB: 1 Rest in Peace` honored on paste for putting the card directly in the sideboard, or must the user drag cards? (Third-party importers accept it; Moxfield official docs are silent.)
2. **Forge maybeboard:** no `[maybeboard]` section exists in the `.dck` spec — confirm folding maybeboard into `[sideboard]` (or dropping with a UI note) is the agreed behavior.
3. **TCGplayer set-code mapping:** Scryfall `CMM` vs TCGplayer product set naming diverges for some sets (Commander Masters variants, Universes Beyond, Secret Lair bonus cards). Which source of truth should the printing toggle use for TCGplayer set codes — Scryfall code as-is, or a mapping table?
4. **Archidekt lowercase set codes:** observed exports use lowercase (`(cmm)`); does the Archidekt importer accept uppercase too (irrelevant for export, but confirms we should always emit lowercase)?
5. **Collector numbers with suffixes** (`62★`, `256p`, `†`): emit verbatim in all four `(SET) CN` / `[SET] CN` formats, or strip non-numeric suffixes for TCGplayer?
