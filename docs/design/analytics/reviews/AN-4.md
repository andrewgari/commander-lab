# AN-4R: Designer review of the live Stats + Identity pages

Card: `t_f5cf86da`. Reviewed against `docs/design/analytics/UX_SPEC.md` on
live prod `http://192.168.50.3:8020` (f0e34aa, v0.4.11), Holy Hellfire, at
1440×900 and 390×844 (mobile, DPR 2), using Chromium over CDP. Every finding
below was measured in the DOM, not eyeballed.

## Verdict: PASS, with 6 small fixes shipped in this PR

The build matches the spec. The graph layout, the engine focus that loads
with the first engine selected, the edge-label grouping (`ATTACKING ×15`),
the phone list view, the composition delta chips, the D8 colour rules, the
D9 tokens (`--text-3 #8a96a2`, `--danger #ee6a61`, rank bars render) and the
keyboard map are all right. Reduced motion leaves 0 running animations.
Nothing here blocks AN-5.

### What I checked and it holds

| Spec | Result |
|---|---|
| §3.0 header + tabs, keys 1–3 | ok. `3` → /identity, and keys are ignored while focus is in an input |
| §3.1 stat strip, 6 tiles, hero first | ok |
| §3.1.1 graph: mana filter, √degree radius, labels at degree ≥ 3, viewBox fit | ok. 50 nodes / 190 edges |
| §3.1.2 engines: first focused, `[` `]` cycle, `Esc` clears to Overview | ok |
| §3.1.3 inspector: Engine / Card / Overview states, `aria-live` | ok |
| §3.1.4 `/` search + Enter selects | ok (Isshin → Card state, Fed by · 10) |
| §3.1.5 ≤ 640: SVG hidden, list view, "Full graph on a wider screen." | ok |
| §3.1.7 composition rows + chips (−3 short / +1 ok / +17 over) | ok. Chips hide ≤ 640 per my own CSS (see N3) |
| §3.1.10 combos ordering, ownership chips, All/Owned | ok |
| §3.2 hero Pillar 1, draft tiles with inset `--data` ring, add tile, savebar | ok |
| §5 390px: no page h-scroll on Stats / Identity | ok (scrollWidth 390) |

## Fixes shipped (this PR)

| # | Where | Bug (measured) | Fix |
|---|---|---|---|
| F1 | `hud.css` `.hud-tabs` | Every tab strip had a 1px vertical scroll (scrollHeight 36 vs client 35). The active indicator sat at `bottom:-1px` inside an `overflow-x:auto` box, so a thin scrollbar with arrows appeared at the right edge of the tabs. | Indicator moved to `bottom:0`; `overflow-y:hidden`. |
| F2 | `deck.html` Overview | At 390 the page scrolled sideways to **531px**. `.deck-hero` stayed 2-column (208px + **70px**), crushing the stat strip and charts. The coder flagged this as follow-up #8. | `@media (max-width:640px) { .deck-hero { grid-template-columns: minmax(0,1fr) } }`. Verified on live: scrollWidth 390. |
| F3 | `deck_stats.html` Least on-plan | Rows sat flush against the panel border (0px body padding) because the panel was `hud-panel--flush`. Its siblings inset by 16px. | Dropped `--flush`. |
| F4 | `deck_stats.html` EDHREC rarest picks | "not listed" (72px) overflowed its 44px value column and spilled ~11px past the panel border, at both widths. | Value shows `—`; the row sub-line reads "not on EDHREC". |
| F5 | `deck_stats.html` stat strip | On-plan foot truncated to "fits a pillar or is gl…" at 1440. | Copy shortened to "pillar or glue". |
| F6 | `deck_analytics.js` curve template | The template line was ~11% short. It used `v × nonLand / 65`, but the template sums to 58 (coder Q1). | Normalised: `v × nonLand / sum(template)`. The test asserts that the line sums to the deck's non-land count. |

## Answers to the coder's questions (t_729a57d9)

1. **Curve template sum.** Normalise. The "65" in §3.1.8 was my typo for a
   shape. The line should always total the deck's non-land count, so it
   reads as "same cards, different distribution". Shipped as F6.
2. **Engine kinds `chain | loop`.** Correct call. AN-2's data wins. The spec
   now reads `chain | loop`, with the same foot copy you used.
3. **Engine pillar = majority fit ≥ 40%.** Approved. Keep it client-side
   until AN-2 carries `pillar_id`.
4. **Draft hero.** Approved: solid signal fill plus the "proposed" chip.
   Don't add the inset data ring to the hero, because it would fight the
   fill. The chip and the "· draft" label are enough.
5. **Identity from JSON.** Approved. I've updated §2.4 to say that Identity
   renders client-side too; only the Journal form gets macros.
6. **Hidden Journal/Tune tabs.** Approved. That's better than linking to a
   404. AN-5 and AN-6 add them back with keys 4 and 5.
7. **"Open card" → Overview `?card=NAME`.** Approved.
8. **Overview overflow at 390.** Fixed here (F2).

## Notes, not fixes (no action unless Andrew wants it)

- N1. **Engine list scroll on desktop.** The list scrolls inside a fixed-height
  graph block, and the 6th row is cut by the foot. That works as the scroll
  cue, so it stays. A fade mask would be decoration.
- N2. **Composition bars share one scale (0–41).** The small rows (Ramp 6/10,
  CA 5/12) use about 15% of the track. This is the intended design: equal
  scale makes Lands vs Plan honest, and the value plus chip carry the
  precise gap. No change.
- N3. **Delta chips hide ≤ 640.** That's from my own spec CSS. The `35/38`
  value still shows, and the bar is `--signal` when short. That's fine on a
  phone.
- N4. **Curve label "3" over the dashed line at CMC 1.** This is a
  data-dependent collision of a 10px label with a 2px dashed line. It's
  still legible, so I'm leaving it. If it bugs anyone, give the value
  labels a `--surface-1` halo in `HUD.barChart`. That's a kit change, so it
  needs its own card.
- N5. **Combo ownership chip truncates long deck names.** The full text is
  in `title`. This is acceptable; the deck names are absurdly long.

## Verify

```
Desktop 1440 + phone 390, /deck/Holy%20Hellfire{,/stats,/identity}:
  document.documentElement.scrollWidth === innerWidth
  .hud-tabs scrollHeight === clientHeight
  #lowfit .hud-rank-item left edge inset 16px from its panel
  #edhrec: no .hud-rank-value wider than its column
  curve aria/ref path: template line totals the deck's non-land count
```
