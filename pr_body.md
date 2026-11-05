## Summary

Implement the deck composition database models, schema definitions, and Redis-backed store for composition-v1.

## Changes

### `analytics/composition_models.py` (pure models, no I/O)
- **Preset model**: structure (deck size, commanders, lands) + slots (category ref + target/min/max)
- **Ref grammar**: kind:id with kinds: structure, type, core, group, facet, theme, cat, flag
- **Built-in presets**: 9 presets (tcz_2025, standard_36, big_mana, go_wide, spellslinger, control, voltron, graveyard, landfall)
- **Validation**: E1-E9 errors, W1-W3 warnings, I1 info issues
- **Auto-selection**: spec section 6, resolves preset from journal archetypes, deck tags, commander MV
- **Legacy migration helpers**: `legacy_override_to_record`, `default_deck_composition`

### `analytics/composition_store.py` (Redis persistence)
- Preset CRUD: `list_presets`, `get_preset`, `create_preset`, `update_preset`, `delete_preset`
- Deck composition CRUD: `get_deck_composition`, `set_deck_composition`
- Legacy migration: `migrate_legacy_composition`, `migrate_all_legacy_compositions`

### `scripts/migrate_composition.py`
- CLI migration script with `--dry-run` and `--url` arguments

### `tests/test_composition_models.py`
- **68 tests** covering: constants, refs, helpers, built-ins, validation, auto-selection, deck composition validation, legacy migration, and full store CRUD

## Testing
```
python -m pytest tests/test_composition_models.py -v
# 68 passed in 0.23s
```
