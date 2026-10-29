# Judge calibration

Hand-scored reviews used to check the LLM judge (`../judge.py`) agrees with Andrew.

## Add a case
1. Take a real review_document and its deck's `rubric.yaml` + `intent.json` (from `../decks/<slug>/`).
2. Score it yourself 1-10 on `rubric_recall`, `specificity`, `intent_alignment`, `actionability`, `generic_filler` (inverted: 10 = no filler).
3. Save `<deck-slug>-<n>.json` here:
   ```json
   {"review": {}, "rubric": {}, "intent": {}, "human": {"rubric_recall": 7, "specificity": 8, "intent_alignment": 6, "actionability": 7, "generic_filler": 9}}
   ```
4. Run the judge on it and add its scores under a `"judge"` key (same five dimensions).
5. `python tests/review_evals/calibration/report.py` prints MAE, within-1 agreement and Pearson r per dimension.

Empty is fine; the report says so.
