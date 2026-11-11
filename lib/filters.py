"""Jinja filters."""

import re

from markupsafe import Markup, escape

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
_SAFE_SCHEMES = frozenset({"http", "https", "mailto"})


def _link(match: "re.Match") -> str:
    label, url = match.group(1), match.group(2)
    scheme = _SCHEME.match(url)
    if scheme and scheme.group(1).lower() not in _SAFE_SCHEMES:
        # Unsafe protocol (javascript:, data:, ...): keep the label, drop the link.
        return label
    return f'<a href="{url}" rel="noopener">{label}</a>'


def markdown_lite(text: str) -> Markup:
    """Render the small Markdown subset used in descriptions and comments:
    **bold**, `code`, [links](https://...) and blank-line paragraphs."""
    if not text:
        return Markup("")
    # Escape user input first: everything below then only adds tags we
    # generate ourselves, so the result is safe to mark as Markup.
    text = str(escape(text))
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
