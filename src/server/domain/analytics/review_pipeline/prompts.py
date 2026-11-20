"""Prompt template loader. Templates are versioned markdown files in ``prompts/``.

File format::

    <!-- @header ... free-form notes, never sent to the model ... -->
    <!-- @section system.role -->
    text with {{placeholders}}
    <!-- @section user -->
    ...

Rules: a released version is never edited (add ``name.v2.md``); a missing placeholder value or an
unknown section raises ``PromptError`` instead of sending a half-filled prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from src.server.domain.analytics.review_pipeline import config as cfg

PROMPT_DIR = Path(__file__).parent / "prompts"
_SECTION = re.compile(r"^<!--\s*@section\s+([\w.\-]+)\s*-->[ \t]*\n?", re.M)
_HEADER = re.compile(r"<!--\s*@header.*?-->\s*", re.S)
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


class PromptError(RuntimeError):
    pass


@dataclass(frozen=True)
class Template:
    name: str
    version: str
    sections: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, name: str, version: str, text: str) -> "Template":
        body = _HEADER.sub("", text, count=1)
        marks = list(_SECTION.finditer(body))
        if not marks:
            raise PromptError(f"{name}.{version}: no '<!-- @section x -->' markers")
        sections: dict[str, str] = {}
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
            sections[m.group(1)] = body[m.end():end].strip("\n")
        return cls(name, version, sections)

    def render(self, section: str, **values: object) -> str:
        if section not in self.sections:
            raise PromptError(f"{self.name}.{self.version}: no section {section!r}")
        text = self.sections[section]
        missing = sorted({k for k in _PLACEHOLDER.findall(text) if k not in values})
        if missing:
            raise PromptError(f"{self.name}.{self.version}[{section}]: no value for {', '.join(missing)}")
        return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), text)   # single pass: values are never re-expanded


@dataclass(frozen=True)
class QualityStandard:
    version: str
    text: str


@lru_cache(maxsize=None)
def _read(name: str, version: str) -> Template:
    path = PROMPT_DIR / f"{name}.{version}.md"
    if not path.is_file():
        raise PromptError(f"prompt {name}.{version} not found at {path}")
    return Template.parse(name, version, path.read_text(encoding="utf-8"))


def load(name: str, version: str | None = None) -> Template:
    return _read(name, version or cfg.PROMPT_VERSIONS[name])


def quality_standard(version: str | None = None) -> QualityStandard:
    t = load("quality_standard", version)
    return QualityStandard(t.version, t.sections["standard"])
