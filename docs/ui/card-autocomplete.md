# Card name autocomplete

The classic Brew page uses the shared `static/js/card_autocomplete.js` picker
with the commander-only `/api/cards/autocomplete` search. Typing a name shows
matching suggestions; choosing one updates the Brew summary and enables
generation. The search can also show an error or a degraded-results notice when
the upstream card service is unavailable.

In Brew, Enter may be pressed while the current search is still loading. An
available first result is selected when the search completes; if search is
degraded or reports an error, the notice and results are shown for confirmation
instead. Results from an earlier query must not be selected after the input
changes, and dismissing the picker cancels a pending selection. Other picker
consumers retain their own Enter behavior.

Run the shared picker behavior tests with `python -m pytest
tests/test_card_autocomplete_js.py` and the Brew browser tests with `python -m
pytest tests/e2e/test_brew_autocomplete_e2e.py` (requires Playwright and its
browser dependencies).
