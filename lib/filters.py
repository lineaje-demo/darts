"""Jinja filters."""

import re

from markupsafe import Markup, escape

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)


def _link_repl(match) -> str:
    label, url = match.group(1), match.group(2)
    if not _SAFE_HREF.match(url):
        # Disallowed URL scheme: keep the (already escaped) source text
        # instead of emitting an <a> tag.
        return match.group(0)
    return f'<a href="{url}" rel="noopener">{label}</a>'


def markdown_lite(text: str) -> Markup:
    """Render the small Markdown subset used in descriptions and comments:
    **bold**, `code`, [links](https://...) and blank-line paragraphs."""
    if not text:
        return Markup("")
    # Escape the raw text first so no stored HTML survives; the Markdown
    # substitutions below then only re-introduce fixed, safe tags around
    # already-escaped content.
    text = str(escape(str(text)))
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _LINK.sub(_link_repl, text)
    paragraphs = [p.strip().replace("\n", "<br>") for p in text.split("\n\n") if p.strip()]
    return Markup("".join(f"<p>{p}</p>" for p in paragraphs))


def money(cents) -> str:
    return f"${(cents or 0) / 100:,.2f}"


def register(app) -> None:
    app.jinja_env.filters["markdown"] = markdown_lite
    app.jinja_env.filters["money"] = money
