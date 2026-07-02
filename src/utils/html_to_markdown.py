"""Faithful HTML -> Markdown conversion (a compact Turndown-equivalent, lxml-based).

The Obsidian Web Clipper produces clean clippings by pairing Mozilla Readability (isolate the
main article, drop nav/ads/chrome) with Turndown (serialize that HTML to Markdown, preserving
heading levels, bold/italic with correct spacing, links, and lists). trafilatura -- our earlier
converter -- is excellent at *selecting* content but serializes this class of WordPress page
badly: it renders `<h2><strong>...</strong></h2>` section headers as mashed bold text instead of
`##` headings, drops the space after bold run-ins ("**Confit the garlic.**Place"), and mangles
links whose text is wrapped in `<strong>`.

This module is the Turndown half. `readability.Document(html).summary()` gives us the isolated
article HTML (the Readability half, already a dependency); `html_to_markdown()` serializes it.

Design goals (mirroring the Web Clipper's output quality):
- `<h1>`-`<h6>` -> `#`-`######`, with any inline `<strong>`/`<em>` inside the heading unwrapped
  (a heading is already emphatic -- Markdown headings don't take bold).
- Accordion / toggle titles (common WordPress FAQ blocks) are promoted to headings so the
  Q&A structure survives, even when the clickable header is a <button>/<summary>/<a>.
- `<strong>`/`<b>` -> `**...**`, `<em>`/`<i>` -> `*...*`, with TIGHT delimiters and the
  surrounding whitespace preserved (so "**lead-in.** detail" keeps its space). Nested identical
  emphasis is not double-wrapped.
- `<a href>` -> `[text](href)`, image `<img>` -> `![alt](src)` (localized downstream).
- `<ul>`/`<ol>` -> `-` / `1.` lists (one level of nesting handled), `<blockquote>` -> `> `.
- Junk tags (script/style/iframe/button/svg/form/nav/aside...) are dropped.

Pure/deterministic and defensive: malformed input yields "" rather than raising.
"""
import re

_HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
# Inline-level tags whose emphasis/anchor semantics we render inline.
_INLINE_TAGS = {
    "a", "strong", "b", "em", "i", "u", "code", "span", "sub", "sup", "abbr", "mark",
    "small", "time", "cite", "s", "del", "kbd", "var", "q", "wbr", "font", "label",
}
# Tags whose entire subtree is dropped (chrome, scripts, interactive/ad widgets).
_SKIP_TAGS = {
    "script", "style", "noscript", "iframe", "form", "button", "svg", "input", "select",
    "textarea", "nav", "aside", "template", "object", "embed", "video", "audio", "canvas",
    "map", "area", "ins",  # <ins> is almost always an ad slot on these pages
}
# Class/id substrings that mark non-content blocks even after Readability (share bars, ad
# leftovers, "my latest videos", newsletter widgets). Matched case-insensitively.
_JUNK_CLASS_RE = re.compile(
    r"(share|social|newsletter|subscribe|advert|\bad-|\bads\b|sponsor|related-|jp-relatedposts|"
    r"latest-videos|video-player|cookie|consent|breadcrumb|pagination|author-box|post-nav)",
    re.I,
)


def _norm_ws(s: str) -> str:
    """Collapse runs of whitespace to a single space (HTML whitespace semantics). Python 3's
    ``\\s`` is Unicode-aware, so it also folds nbsp (U+00A0) and other Unicode spaces; the
    explicit alternation adds the zero-width characters ``\\s`` does not cover."""
    return re.sub(r"(?:\s|​|‌|‍|﻿)+", " ", s) if s else ""


def _is_el(node) -> bool:
    return isinstance(getattr(node, "tag", None), str)


def _classes(el) -> str:
    return f"{el.get('class', '')} {el.get('id', '')}"


def _first_srcset_url(val: str) -> str:
    """First URL of a ``srcset`` value (``'a.jpg 576w, b.jpg 1024w'`` -> ``'a.jpg'``)."""
    first = (val or "").split(",", 1)[0].strip()
    return first.split()[0] if first else ""


def _img_md(img) -> str:
    src = (img.get("src") or "").strip()
    if not src or src.startswith("data:"):
        # Lazy-loaded image: the real URL hides in a data-* attribute. (In the live pipeline
        # Playwright promotes these to src before capture, but be robust to raw HTML too.)
        for attr in ("data-lazy-src", "data-src", "data-original", "data-lazy-srcset",
                     "data-srcset", "srcset"):
            v = (img.get(attr) or "").strip()
            if not v:
                continue
            cand = _first_srcset_url(v) if "srcset" in attr else v
            if cand and not cand.startswith("data:"):
                src = cand
                break
    if not src or src.startswith("data:"):
        return ""
    alt = _norm_ws(img.get("alt") or "").strip()
    return f"![{alt}]({src})"


def _emphasis(inner: str, mark: str) -> str:
    """Wrap ``inner`` in tight emphasis delimiters, hoisting any leading/trailing space outside
    the markers (Markdown needs `**tight**`, not `** loose **`). If ``inner`` is already a single
    span of the same emphasis (nested ``<strong><strong>``), it is not double-wrapped."""
    stripped = inner.strip()
    if not stripped:
        return inner  # whitespace-only -- keep as a plain separating space
    lead = " " if inner[:1].isspace() else ""
    trail = " " if inner[-1:].isspace() else ""
    # If the inner content already carries this emphasis marker anywhere -- a nested
    # `<strong><strong>`, or an `<em>` wrapping a `<strong>` lead-in plus trailing prose --
    # adding another layer produces ugly/ambiguous delimiter runs ("****", "***text**").
    # Flatten: keep the inner emphasis, drop this outer wrapper (matches Web Clipper style).
    if mark in stripped:
        return f"{lead}{stripped}{trail}"
    return f"{lead}{mark}{stripped}{mark}{trail}"


def _render_inline_node(node) -> str:
    """Markdown for a single inline element (its text + inline children + their tails)."""
    tag = node.tag.lower()
    if tag == "br":
        return "\n"
    if tag == "img":
        return _img_md(node)
    if tag in _SKIP_TAGS:
        return ""
    inner = _render_inline_children(node)
    if tag == "a":
        href = (node.get("href") or "").strip()
        text = inner.strip()
        if not text:
            return ""
        if not href or href.startswith(("javascript:", "#")):
            return inner
        return f"[{text}]({href})"
    if tag in ("strong", "b"):
        return _emphasis(inner, "**")
    if tag in ("em", "i", "cite", "var"):
        return _emphasis(inner, "*")
    if tag in ("code", "kbd"):
        t = inner.strip()
        return f"`{t}`" if t else ""
    # span/sub/sup/abbr/small/time/mark/etc. -- pass content through unchanged.
    return inner


def _render_inline_children(el) -> str:
    """Concatenate an element's inline content: its .text, each child rendered inline, and
    each child's .tail. Whitespace is normalized but single spaces at boundaries are kept."""
    parts: list[str] = []
    if el.text:
        parts.append(_norm_ws(el.text))
    for c in el:
        if not _is_el(c):
            if c.tail:
                parts.append(_norm_ws(c.tail))
            continue
        parts.append(_render_inline_node(c))
        if c.tail:
            parts.append(_norm_ws(c.tail))
    return "".join(parts)


# A completed bold/italic span glued directly to a following word char -- the run-in case
# ("**Confit the garlic.**Place" -> "**Confit the garlic.** Place"). The content class forbids
# brackets/parens/newlines and is length-capped so the match cannot run away across a
# `](url)` boundary and swallow the text between two unrelated emphasis pairs; the lookahead
# requires an alphanumeric (never `]`/`)`), so it never fires on a span that closes in a link.
_GLUED_EMPHASIS_RE = re.compile(
    r"(\*\*[^\s*][^*\n\[\]()]{0,70}?\*\*|\*[^\s*][^*\n\[\]()]{0,70}?\*)(?=[A-Za-z0-9])"
)


def _postprocess_inline(s: str) -> str:
    s = _GLUED_EMPHASIS_RE.sub(r"\1 ", s)
    return re.sub(r" {2,}", " ", s).strip()


def _heading_text(el) -> str:
    """Inline content of a heading. Emphasis was unwrapped at the DOM level already; the regex
    strip is a belt-and-suspenders pass for any that slipped through."""
    txt = _postprocess_inline(_render_inline_children(el))
    txt = re.sub(r"\*\*(.+?)\*\*", r"\1", txt)
    txt = re.sub(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)", r"\1", txt)
    return txt.strip()


def _render_list(el, ordered: bool, depth: int) -> str:
    lines: list[str] = []
    idx = 1
    for li in el.xpath("./li"):
        blocks: list[str] = []
        _render_blocks(li, blocks)
        if not blocks:
            continue
        marker = f"{idx}." if ordered else "-"
        indent = "  " * depth
        first, *rest = blocks
        lines.append(f"{indent}{marker} {first}")
        for r in rest:
            for ln in r.split("\n"):
                lines.append(f"{indent}  {ln}" if ln else "")
        idx += 1
    return "\n".join(lines)


def _render_blocks(el, out: list[str]) -> None:
    """Walk block-level children of ``el``, appending Markdown chunks to ``out``. Inline runs
    between block elements are gathered into paragraphs."""
    inline_buf: list[str] = []

    def flush():
        if inline_buf:
            para = _postprocess_inline("".join(inline_buf))
            if para:
                out.append(para)
            inline_buf.clear()

    if el.text and el.text.strip():
        inline_buf.append(_norm_ws(el.text))

    for c in el:
        if not _is_el(c):
            if c.tail and c.tail.strip():
                inline_buf.append(_norm_ws(c.tail))
            continue
        tag = c.tag.lower()

        if tag in _SKIP_TAGS or (tag != "a" and _JUNK_CLASS_RE.search(_classes(c))):
            pass
        elif tag in _HEADING_TAGS:
            flush()
            txt = _heading_text(c)
            if txt:
                out.append(f"{_HEADING_TAGS[tag]} {txt}")
        elif tag == "p":
            flush()
            txt = _postprocess_inline(_render_inline_children(c))
            if txt:
                out.append(txt)
        elif tag in ("ul", "ol"):
            flush()
            lst = _render_list(c, ordered=(tag == "ol"), depth=0)
            if lst:
                out.append(lst)
        elif tag == "blockquote":
            flush()
            inner: list[str] = []
            _render_blocks(c, inner)
            body = "\n\n".join(inner)
            if body:
                out.append("\n".join((f"> {ln}" if ln else ">") for ln in body.split("\n")))
        elif tag == "figcaption":
            flush()
            txt = _postprocess_inline(_render_inline_children(c))
            if txt:
                # Italicize the caption, but flatten (no italic) when it already carries emphasis
                # so a bold lead-in doesn't pile up delimiters into "***".
                out.append(_emphasis(txt, "*"))
        elif tag == "img":
            flush()
            md = _img_md(c)
            if md:
                out.append(md)
        elif tag == "pre":
            flush()
            code = (c.text_content() or "").strip("\n")
            if code.strip():
                out.append(f"```\n{code}\n```")
        elif tag == "hr":
            flush()
            out.append("---")
        elif tag == "table":
            flush()
            tbl = _render_table(c)
            if tbl:
                out.append(tbl)
        elif tag in _INLINE_TAGS:
            inline_buf.append(_render_inline_node(c))
        else:
            # Structural wrapper (div/section/article/figure/header/footer/main/details/...):
            # flush the current paragraph and recurse so nested blocks render at top level.
            flush()
            _render_blocks(c, out)

        if c.tail and c.tail.strip():
            inline_buf.append(_norm_ws(c.tail))

    flush()


def _render_table(el) -> str:
    """Minimal GFM table: first row becomes the header. Best-effort; skipped if degenerate."""
    rows = []
    for tr in el.xpath(".//tr"):
        cells = [
            _postprocess_inline(_render_inline_children(td)).replace("|", "\\|")
            for td in tr.xpath("./td|./th")
        ]
        if cells:
            rows.append(cells)
    if len(rows) < 2:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * width) + " |"
    body = ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join([head, sep, *body])


# Plugin recipe-card containers. In a SAVES note the structured recipe is rendered separately
# (its own styled callout, from JSON-LD/WPRM), so the card is stripped from the page reproduction
# to avoid a duplicated -- and, because card markup packs amount/unit/name into adjacent spans
# with no whitespace, badly-spaced -- second copy.
_RECIPE_CARD_CLASSES = (
    "wprm-recipe-container", "tasty-recipes", "mv-recipe-card", "wp-block-recipe-card",
    "easyrecipe", "recipe-card-container", "wp-block-recipe-block",
)


def _strip_recipe_cards(root) -> None:
    xpath = " | ".join(f"//*[contains(@class,'{c}')]" for c in _RECIPE_CARD_CLASSES)
    for el in root.xpath(xpath):
        if el.getparent() is not None:
            el.drop_tree()


def _unwrap_heading_emphasis(root) -> None:
    """A Markdown heading is already emphatic, so strip any <strong>/<b>/<em>/<i> that wrap a
    heading's text (this WordPress theme wraps every <h2>/<h3> label in <strong>, which is what
    broke heading detection in the first place). Done at the DOM level so nested emphasis is
    flattened cleanly."""
    for h in root.xpath("//h1|//h2|//h3|//h4|//h5|//h6"):
        for inline in h.xpath(".//strong|.//b|.//em|.//i"):
            inline.drop_tag()


def _promote_accordions(root) -> None:
    """Turn WordPress accordion/toggle titles (FAQ blocks) into real <h3> headings so the
    question/answer structure survives, matching how the Web Clipper renders them. The clickable
    header is often a <button>/<summary>/<a> -- which we otherwise drop -- so neutralize those
    ancestors to plain <div> first."""
    # Drop the +/- toggle glyph elements so they don't get glued onto the question heading.
    for icon in root.xpath(
        "//*[contains(@class,'toggle-icon') or contains(@class,'accordion-icon')"
        " or contains(@class,'toggle-arrow') or contains(@class,'accordion__icon')]"
    ):
        icon.drop_tree()
    for sp in root.xpath(
        "//*[contains(@class,'toggle-title')] | //*[contains(@class,'accordion-title')]"
        " | //summary"
    ):
        text = _norm_ws(sp.text_content()).strip()
        # Accordion headers often embed a toggle glyph (+ / - / chevron) in the title text.
        text = text.rstrip(" +−–—▼▲▾⌄›❯").strip()
        if not text:
            continue
        anc, hops = sp.getparent(), 0
        while anc is not None and hops < 4:
            if anc.tag in ("button", "summary", "a"):
                anc.tag = "div"
                anc.attrib.pop("href", None)
            anc, hops = anc.getparent(), hops + 1
        for ch in list(sp):
            sp.remove(ch)
        sp.text = text
        sp.tag = "h3"
        sp.attrib.clear()


def html_to_markdown(html: str, drop_recipe_card: bool = True) -> str:
    """Convert an HTML fragment (typically a Readability ``.summary()``) to clean Markdown.
    Returns "" on empty/unparseable input. When ``drop_recipe_card`` is true (the default),
    plugin recipe-card blocks are removed so they don't duplicate the note's styled recipe."""
    if not html or not html.strip():
        return ""
    try:
        import lxml.html

        root = lxml.html.fromstring(html)
    except Exception:
        return ""

    if drop_recipe_card:
        _strip_recipe_cards(root)
    _promote_accordions(root)
    _unwrap_heading_emphasis(root)
    body = root.find(".//body")
    scope = body if body is not None else root
    # Wrap the scope so _render_blocks always sees it as a *child* to dispatch. Without this, a
    # fragment whose root is itself a block element (a bare <ul>/<table>, e.g. a Readability
    # summary that is just a list) would have its children walked directly, losing the list/table
    # semantics. For the usual <body>/<div> scope this is output-neutral (the container just
    # recurses one extra level).
    wrapper = scope.makeelement("div")
    wrapper.append(scope)

    out: list[str] = []
    _render_blocks(wrapper, out)

    md = "\n\n".join(b for b in out if b.strip())
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return _dedupe_adjacent_headings(md)


def _dedupe_adjacent_headings(md: str) -> str:
    """Drop an immediately-repeated identical heading line (Readability sometimes keeps a
    visually-duplicated title)."""
    lines = md.split("\n")
    kept: list[str] = []
    for ln in lines:
        if ln.startswith("#") and kept and kept[-1].strip() == ln.strip():
            continue
        kept.append(ln)
    return "\n".join(kept)
