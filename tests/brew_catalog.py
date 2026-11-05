"""Test helper: seed a FakeRedis with the card catalog records the Brew gate
needs (commander eligibility, colour identity, Commander legality)."""
import json
import uuid

_NS = uuid.UUID("00000000-0000-4000-8000-00000000b0e1")


def oracle_id_for(name: str) -> str:
    return str(uuid.uuid5(_NS, name))


def seed_brew_catalog(r, commander="Atraxa, Praetors' Voice", commander_ci="WUBG", names=(), oids=None):
    """Write `card:{oid}` + `idx:card_name_to_oracle:{name}` for the commander
    (legendary creature) and each of `names` (colourless, legal). `oids` maps
    name -> oracle_id to reuse fixed ids."""
    oids = oids or {}

    def put(name, rec):
        oid = oids.get(name) or oracle_id_for(name)
        rec = {"name": name, "oracle_id": oid, "commander_legality": "legal", **rec}
        r.set(f"card:{oid}", json.dumps(rec))
        r.set(f"idx:card_name_to_oracle:{name}", oid)

    put(commander, {"color_identity": commander_ci, "type_line": "Legendary Creature — Test"})
    for n in names:
        if n != commander:
            put(n, {"color_identity": "", "type_line": "Artifact"})
