#!/usr/bin/env python3
"""Keep a Changelog 1.1.0 helper: build release bodies from CHANGELOG.md only.

Never reads commit logs, PR titles or GitHub generated notes.

  body --old OLD.md --new NEW.md   release body = entries newly added to [Unreleased]
  extract VERSION                  body of a dated section (or "Unreleased")
  promote VERSION [--date D] [--dry-run]   move [Unreleased] into a dated section
  validate                         check headings / dates / ordering
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date as _date

CATEGORIES = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]
MAINTENANCE = "Maintenance release, no user-facing changes."
_SECTION_RE = re.compile(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?\s*$")
_LINKDEF_RE = re.compile(r"^\[[^\]]+\]:\s+\S+")
# Lines that must never appear in an entry (merge/PR/hash/CI/bump noise).
NOISE_RE = re.compile(
    r"(merged? (pr|pull)|merge (pull|branch)|pull request|\bPR\s*#\d+|#\d{2,}\b"
    r"|\b[0-9a-f]{7,40}\b(?<![0-9]{7})|bump(ed)? version|version bump|\bCI\b)",
    re.IGNORECASE,
)


class ChangelogError(ValueError):
    pass


def split_sections(text: str):
    """Return (preamble_lines, [(name, date, lines)], linkdef_lines)."""
    lines = text.splitlines()
    links = []
    while lines and (not lines[-1].strip() or _LINKDEF_RE.match(lines[-1])):
        if lines[-1].strip():
            links.insert(0, lines[-1])
        lines.pop()
    pre, secs, cur = [], [], None
    for ln in lines:
        m = _SECTION_RE.match(ln)
        if m:
            cur = (m.group(1), m.group(2), [])
            secs.append(cur)
        elif cur is None:
            pre.append(ln)
        else:
            cur[2].append(ln)
    return pre, secs, links


def parse_entries(lines):
    """-> {category: [entry_text, ...]} preserving order; entries are multi-line blocks."""
    out: dict[str, list[str]] = {}
    cat, block = None, None
    for ln in lines:
        h = re.match(r"^### (.+?)\s*$", ln)
        if h:
            cat, block = h.group(1), None
            out.setdefault(cat, [])
        elif cat is not None and re.match(r"^[-*] ", ln):
            block = [ln]
            out[cat].append(block)
        elif block is not None and ln.strip() and ln.startswith((" ", "\t")):
            block.append(ln)
        elif not ln.strip():
            block = block if block is None else block
    return {c: ["\n".join(b) for b in bl] for c, bl in out.items()}


def get_section(text: str, name: str):
    _, secs, _ = split_sections(text)
    for n, d, lines in secs:
        if n.lower() == name.lower():
            return d, lines
    raise ChangelogError(f"section [{name}] not found")


def render(entries: dict[str, list[str]]) -> str:
    parts = []
    for cat in CATEGORIES:
        if entries.get(cat):
            parts.append(f"### {cat}\n\n" + "\n".join(entries[cat]))
    return ("\n\n".join(parts) + "\n") if parts else MAINTENANCE + "\n"


def extract(text: str, name: str) -> str:
    _, lines = get_section(text, name)
    return render(parse_entries(lines))


def delta_body(old: str | None, new: str) -> str:
    """Entries present in new [Unreleased] but not old [Unreleased]."""
    new_e = parse_entries(get_section(new, "Unreleased")[1])
    old_e = {}
    if old:
        try:
            old_e = parse_entries(get_section(old, "Unreleased")[1])
        except ChangelogError:
            pass
    added = {
        c: [e for e in es if e not in old_e.get(c, [])] for c, es in new_e.items()
    }
    return render(added)


def validate(text: str) -> list[str]:
    errs = []
    pre, secs, links = split_sections(text)
    if not secs or secs[0][0].lower() != "unreleased":
        errs.append("[Unreleased] must be the first section")
    prev = None
    for name, d, lines in secs:
        if name.lower() != "unreleased":
            if not d:
                errs.append(f"[{name}] missing ISO date")
            else:
                try:
                    _date.fromisoformat(d)
                except ValueError:
                    errs.append(f"[{name}] bad date {d}")
                if prev and d > prev:
                    errs.append(f"[{name}] not newest-first")
                prev = d
        for ln in lines:
            h = re.match(r"^### (.+?)\s*$", ln)
            if h and h.group(1) not in CATEGORIES:
                errs.append(f"[{name}] unknown heading '{h.group(1)}'")
        for es in parse_entries(lines).values():
            for e in es:
                if NOISE_RE.search(e):
                    errs.append(f"[{name}] noisy entry: {e.strip()[:60]}")
    defined = {re.match(r"^\[([^\]]+)\]", l).group(1) for l in links}
    for name, _, _ in secs:
        if name not in defined:
            errs.append(f"[{name}] missing compare link")
    return errs


def promote(text: str, version: str, when: str) -> str:
    pre, secs, links = split_sections(text)
    if not secs or secs[0][0].lower() != "unreleased":
        raise ChangelogError("no [Unreleased] section")
    entries = parse_entries(secs[0][2])
    if not any(entries.values()):
        raise ChangelogError("[Unreleased] is empty; nothing to release")
    _date.fromisoformat(when)
    if any(n == version for n, _, _ in secs):
        raise ChangelogError(f"[{version}] already exists")
    prev_tag = next((n for n, _, _ in secs[1:]), None)
    body = render(entries).rstrip("\n")
    out = pre[:] + ["## [Unreleased]", "", f"## [{version}] - {when}", ""]
    out += body.splitlines() + [""]
    for n, d, lines in secs[1:]:
        out += [f"## [{n}]" + (f" - {d}" if d else "")] + lines
    while out and not out[-1].strip():
        out.pop()
    out.append("")
    # compare links
    url = None
    for l in links:
        m = re.match(r"^\[Unreleased\]:\s+(\S+)/compare/", l)
        if m:
            url = m.group(1)
    if not url:
        raise ChangelogError("cannot derive repo URL from [Unreleased] link")
    new_links = [f"[Unreleased]: {url}/compare/{version}...HEAD"]
    new_links.append(
        f"[{version}]: {url}/compare/{prev_tag}...{version}" if prev_tag
        else f"[{version}]: {url}/releases/tag/{version}"
    )
    new_links += [l for l in links if not l.startswith("[Unreleased]:")]
    return "\n".join(out + new_links) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("body"); b.add_argument("--old"); b.add_argument("--new", default="CHANGELOG.md")
    e = sub.add_parser("extract"); e.add_argument("version"); e.add_argument("--file", default="CHANGELOG.md")
    p = sub.add_parser("promote"); p.add_argument("version"); p.add_argument("--date")
    p.add_argument("--file", default="CHANGELOG.md"); p.add_argument("--dry-run", action="store_true")
    v = sub.add_parser("validate"); v.add_argument("--file", default="CHANGELOG.md")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "body":
            old = open(a.old).read() if a.old else None
            sys.stdout.write(delta_body(old, open(a.new).read()))
        elif a.cmd == "extract":
            sys.stdout.write(extract(open(a.file).read(), a.version))
        elif a.cmd == "promote":
            res = promote(open(a.file).read(), a.version, a.date or _date.today().isoformat())
            if a.dry_run:
                sys.stdout.write(res)
            else:
                open(a.file, "w").write(res)
        else:
            errs = validate(open(a.file).read())
            print("\n".join(errs) or "OK")
            return 1 if errs else 0
    except (ChangelogError, OSError, ValueError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
