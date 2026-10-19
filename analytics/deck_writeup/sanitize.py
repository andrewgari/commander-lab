"""Markdown/HTML -> safe primer HTML. The deck page renders saved primers with innerHTML, so everything
that reaches storage goes through `sanitize_primer_html`: an allow-list of tags, no attributes except the
card-name span, and in-deck card names wrapped for the hover preview."""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from typing import Any, Iterable, List, Mapping, Optional, Union

ALLOWED_TAGS = {"p", "ul", "ol", "li", "b", "strong", "em", "i", "code", "br", "h3", "h4", "span"}
_TAG_MAP = {"h1": "h3", "h2": "h3", "h5": "h4", "h6": "h4", "div": "p", "u": "em"}
_DROP_CONTENT = {"script", "style", "iframe", "object", "embed", "template", "noscript", "svg", "math", "textarea"}
_VOID = {"br"}
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")

KnownCards = Union[Mapping[str, Any], Iterable[str], None]


def _names(known: KnownCards) -> List[str]:
    if not known:
        return []
    names = list(known.keys()) if isinstance(known, Mapping) else list(known)
    return sorted({str(n) for n in names if n and len(str(n)) > 2}, key=lambda n: (-len(n), n))


def card_span(name: str, text: Optional[str] = None) -> str:
    """The span the hover preview binds to (static/js/card_hover_preview.js reads data-card-preview/data-cp-name)."""
    n = _html.escape(name, quote=True)
    return (f'<span class="pf-cardname" tabindex="0" data-card="{n}" data-card-preview data-cp-name="{n}">'
            f'{_html.escape(text if text is not None else name)}</span>')


class _Clean(HTMLParser):
    def __init__(self, names: List[str]):
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.stack: List[str] = []   # emitted tags
        self.skip = 0                # depth inside a dropped-content element
        self.card_depth = 0
        self.pattern = None
        self.canon = {}
        if names:
            self.canon = {n.lower(): n for n in names}
            alt = "|".join(re.escape(n) for n in names)
            self.pattern = re.compile(r"(?<![\w'’-])(" + alt + r")(?![\w'’-])", re.I)

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self.skip += 1
            return
        if self.skip:
            return
        tag = _TAG_MAP.get(tag, tag)
        if tag not in ALLOWED_TAGS:
            return
        if tag == "span":
            a = dict(attrs)
            card = a.get("data-card")
            if card and "pf-cardname" in (a.get("class") or ""):
                self.out.append(card_span(card, "")[:-len("</span>")])
                self.stack.append("span")
                self.card_depth += 1
            return  # plain spans are dropped (text kept)
        self.out.append(f"<{tag}>")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br" and not self.skip:
            self.out.append("<br>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        tag = _TAG_MAP.get(tag, tag)
        if tag == "span":
            if self.card_depth and "span" in self.stack:
                self._close("span")
                self.card_depth -= 1
            return
        if tag in ALLOWED_TAGS and tag not in _VOID and tag in self.stack:
            self._close(tag)

    def _close(self, tag):
        while self.stack:
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == "span" and tag != "span":
                self.card_depth = max(0, self.card_depth - 1)
            if t == tag:
                break

    def handle_data(self, data):
        if self.skip:
            return
        if self.pattern and not self.card_depth:
            pos, parts = 0, []
            for m in self.pattern.finditer(data):
                parts.append(_html.escape(data[pos:m.start()]))
                parts.append(card_span(self.canon.get(m.group(1).lower(), m.group(1)), m.group(1)))
                pos = m.end()
            parts.append(_html.escape(data[pos:]))
            self.out.append("".join(parts))
        else:
            self.out.append(_html.escape(data))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


# ---- markdown ----------------------------------------------------------------------------------------

def _inline(s: str) -> str:
    s = _html.escape(s)
    s = re.sub(r"\[\[(.+?)\]\]", r"\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def markdown_to_html(md: str) -> str:
    """Small markdown subset: ##/### headings, -/* and 1. lists, paragraphs, **bold**, *em*, `code`, [[Card]]."""
    out: List[str] = []
    para: List[str] = []
    lst = None  # "ul" | "ol"

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()

    def close_list():
        nonlocal lst
        if lst:
            out.append(f"</{lst}>")
            lst = None

    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        b = re.match(r"^\s*[-*+]\s+(.*)$", line)
        n = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if not line.strip():
            flush_para()
            close_list()
        elif m:
            flush_para()
            close_list()
            tag = "h3" if len(m.group(1)) <= 2 else "h4"
            out.append(f"<{tag}>{_inline(m.group(2))}</{tag}>")
        elif b or n:
            flush_para()
            item = b or n
            kind = "ul" if b else "ol"
            if lst != kind:
                close_list()
                out.append(f"<{kind}>")
                lst = kind
            out.append(f"<li>{_inline(item.group(1) if item else '')}</li>")
        else:
            close_list()
            para.append(line.strip())
    flush_para()
    close_list()
    return "".join(out)


def sanitize_primer_html(html_or_md: str, known_cards: KnownCards = None) -> str:
    """Safe HTML for storage/rendering. Markdown input is converted first. Unknown tags are dropped (text kept),
    scripts/styles/iframes are removed with their content, every attribute is stripped except on card-name spans,
    and known in-deck card names get a `pf-cardname` span."""
    text = html_or_md or ""
    if not _TAG_RE.search(text):
        text = markdown_to_html(text)
    else:
        # raw HTML may still carry markdown-looking card brackets
        text = re.sub(r"\[\[(.+?)\]\]", r"\1", text)
    p = _Clean(_names(known_cards))
    p.feed(text)
    p.close()
    return p.result()


def html_to_text(html_text: str) -> str:
    """Plain text of sanitized HTML (used to fold kept sections into the markdown form of a primer)."""
    t = re.sub(r"<br\s*/?>|</p>|</li>|</h[34]>", "\n", html_text or "")
    t = re.sub(r"<[^>]+>", "", t)
    return "\n".join(x.strip() for x in _html.unescape(t).splitlines() if x.strip())
