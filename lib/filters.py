"""Jinja filters."""

import re
from urllib.parse import urlsplit

from markupsafe import Markup, escape

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")

# Only these URL schemes (plus scheme-less relative URLs) may become href
# targets; anything else (javascript:, data:, vbscript:, ...) is rendered as
# plain text instead of a link.
_LINK_SCHEMES = frozenset({"http", "https", "mailto"})


def _render_link(match: "re.Match") -> str:
    label, url = match.group(1), match.group(2)
    # label and url come from already-escaped text, so they cannot close the
    # href attribute; the remaining risk is the URL scheme itself.
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        scheme = ""
    if scheme and scheme not in _LINK_SCHEMES:
        return label
    return f'<a href="{url}" rel="noopener">{label}</a>'


def markdown_lite(text: str) -> Markup:
    """Render the small Markdown subset used in descriptions and comments:
    **bold**, `code`, [links](https://...) and blank-line paragraphs."""
    if not text:
        return Markup("")
    text = str(escape(text))
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _LINK.sub(_render_link, text)
    paragraphs = [p.strip().replace("\n", "<br>") for p in text.split("\n\n") if p.strip()]
    return Markup("".join(f"<p>{p}</p>" for p in paragraphs))


def money(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def register(app) -> None:
    app.jinja_env.filters["markdown"] = markdown_lite
    app.jinja_env.filters["money"] = money
