"""Jinja filters."""

import re
from urllib.parse import urlsplit

from markupsafe import Markup, escape

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# Schemes permitted in [label](url) links; anything else (javascript:, data:,
# vbscript:, malformed URLs, ...) is rendered as plain text instead.
_LINK_SCHEMES = {"http", "https", "mailto"}


def _link(match: re.Match) -> str:
    label, url = match.group(1), match.group(2)
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        scheme = ""
        allowed = False
    else:
        allowed = not scheme or scheme in _LINK_SCHEMES
    if not allowed:
        return f"{label} ({url})"
    return f'<a href="{url}" rel="noopener">{label}</a>'


def markdown_lite(text: str) -> Markup:
    """Render the small Markdown subset used in descriptions and comments:
    **bold**, `code`, [links](https://...) and blank-line paragraphs."""
    if not text:
        return Markup("")
    # Escape first: stored text is attacker-controlled, and the result is
    # marked safe below, so every byte of it must already be HTML-safe.
    text = str(escape(str(text)))
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _LINK.sub(_link, text)
    paragraphs = [p.strip().replace("\n", "<br>") for p in text.split("\n\n") if p.strip()]
    return Markup("".join(f"<p>{p}</p>" for p in paragraphs))


def money(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def register(app) -> None:
    app.jinja_env.filters["markdown"] = markdown_lite
    app.jinja_env.filters["money"] = money
