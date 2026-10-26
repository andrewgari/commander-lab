"""Forge auto-recorder: parse Forge game logs (FORGELOG agent lines) into
structured game records, derive play-style profiles, and persist both.

Capture path: a javaagent on the Forge host hooks forge.game.GameLog.add and
prints FORGELOG|<TYPE>|<message> to stdout, which Forge multiplexes into
~/.forge/forge.log. The watcher (scripts/forge_log_watcher.py) tails those
logs and POSTs completed game blocks to /api/forge/ingest.
"""
