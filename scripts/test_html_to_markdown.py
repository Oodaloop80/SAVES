"""Regression checks for the lxml HTML->Markdown serializer (src/utils/html_to_markdown.py),
the Turndown half of our Web-Clipper-style page reproduction. Pure logic, no network.

Run: python scripts/test_html_to_markdown.py
Exits non-zero on the first failed assertion.

Each case encodes a real defect found while matching the Obsidian Web Clipper's output on a
WordPress recipe page (drveganblog.com):
  - <hN><strong>..</strong></hN> must become a real `##` heading, not mashed bold.
  - bold run-ins keep the following space ("**Lead.** text"), even when the source omits it.
  - a bold span that closes inside a link must not have a space injected into it, and a run of
    unrelated emphasis pairs across a link must not be swallowed into one span.
  - nested/mixed emphasis (<strong><strong>, <em><strong>) must not pile up delimiters.
  - FAQ accordion titles (often inside a <button>) are promoted to headings, glyph stripped.
  - lazy-loaded images (data-lazy-src / srcset) resolve to a real ![](url).
  - plugin recipe-card blocks are stripped (rendered separately in the note's recipe callout).
"""
import os
import sys

# Emit UTF-8 so the emoji in output don't crash on Windows' cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.html_to_markdown import html_to_markdown  # noqa: E402

_checks = 0


def check(cond: bool, msg: str) -> None:
    global _checks
    _checks += 1
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)
    print(f"  ok: {msg}")


def test_headings_unwrap_strong():
    md = html_to_markdown("<h2><strong>Why You'll Love This</strong></h2><p>Body.</p>")
    check("## Why You'll Love This" in md, "h2 wrapping <strong> becomes a real ## heading")
    check("**Why" not in md, "heading text is not left bold")


def test_bold_runin_keeps_space():
    md = html_to_markdown("<ul><li><strong>Sweet, mellow garlic.</strong> Slow-cooked.</li></ul>")
    check("- **Sweet, mellow garlic.** Slow-cooked." in md, "bold run-in keeps the trailing space")


def test_bold_runin_inserts_missing_space():
    # Source glues the bold lead-in directly to the next word (no space in the HTML).
    md = html_to_markdown("<p><strong>Confit the garlic:</strong>Submerge the cloves.</p>")
    check("**Confit the garlic:** Submerge the cloves." in md,
          "a missing space after a bold run-in is inserted")


def test_link_with_bold_inside():
    md = html_to_markdown('<p>Classic <a href="https://e.org/x"><strong>aglio e olio</strong></a> is great.</p>')
    check("[**aglio e olio**](https://e.org/x)" in md, "link text keeps its bold, delimiters tight")


def test_no_space_injected_across_links():
    # Two separate bold-in-link spans with prose between them: the run-in fixer must NOT match
    # from the first closing ** across the URL to the second opening ** (the old runaway bug).
    html = ('<p>Classic <a href="https://e.org/a"><strong>aglio e olio</strong></a> leans, but '
            'a slow <a href="https://e.org/c"><strong>confit</strong></a> is rounder.</p>')
    md = html_to_markdown(html)
    check("[**aglio e olio**](https://e.org/a)" in md, "first bold link intact")
    check("[**confit**](https://e.org/c)" in md, "second bold link intact (no injected inner space)")
    check("** confit" not in md and "olio **" not in md, "no space injected inside either bold span")


def test_nested_strong_not_doubled():
    md = html_to_markdown("<p><strong><strong>Bold once</strong></strong></p>")
    check("**Bold once**" in md and "****" not in md, "nested <strong><strong> is not double-wrapped")


def test_em_wrapping_strong_flattens():
    # Italic sentence with a bold lead-in: must not produce *** / ****; keep the bold lead-in.
    md = html_to_markdown("<figcaption><em><strong>Confit the garlic:</strong> Submerge cloves.</em></figcaption>")
    check("***" not in md and "****" not in md, "em-wrapping-strong does not pile up delimiters")
    check("**Confit the garlic:**" in md, "the bold lead-in is preserved")


def test_faq_accordion_promoted_to_heading():
    html = (
        '<div class="wp-block-accordion-item">'
        '<button class="accordion-toggle"><span class="wp-block-accordion-heading__toggle-title">'
        '<strong>What is garlic confit?</strong></span><span class="toggle-icon">+</span></button>'
        '<div class="accordion-content"><p>Slow-cooked garlic in oil.</p></div></div>'
    )
    md = html_to_markdown(html)
    check("### What is garlic confit?" in md, "accordion title inside a <button> is promoted to a heading")
    check("?+" not in md and "\n+" not in md, "the +/- toggle glyph is stripped")
    check("Slow-cooked garlic in oil." in md, "the accordion answer body is kept")


def test_lazy_image_resolved():
    md = html_to_markdown(
        '<figure><img src="" data-lazy-src="https://cdn.example.com/pic.jpg" alt="A dish"></figure>'
    )
    check("![A dish](https://cdn.example.com/pic.jpg)" in md, "lazy-loaded image resolves to ![](real-url)")


def test_srcset_image_resolved():
    md = html_to_markdown(
        '<img src="data:image/gif;base64,R0=" srcset="https://cdn.example.com/s.jpg 576w, '
        'https://cdn.example.com/l.jpg 1024w" alt="x">'
    )
    check("![x](https://cdn.example.com/s.jpg)" in md, "srcset first URL is used when src is a data: placeholder")


def test_recipe_card_stripped():
    html = (
        "<article><p>Story intro.</p>"
        '<div class="wprm-recipe-container"><h2>Ingredients</h2>'
        '<span class="wprm-recipe-ingredient-amount">10</span>'
        '<span class="wprm-recipe-ingredient-unit">oz</span>'
        '<span class="wprm-recipe-ingredient-name">spaghetti</span></div>'
        "<p>Story outro.</p></article>"
    )
    md = html_to_markdown(html)
    check("Story intro." in md and "Story outro." in md, "prose around the recipe card is kept")
    check("spaghetti" not in md and "10oz" not in md,
          "the plugin recipe-card block is stripped (rendered separately in the note)")
    md_keep = html_to_markdown(html, drop_recipe_card=False)
    check("spaghetti" in md_keep, "drop_recipe_card=False keeps the card")


def test_lists_and_ordered():
    md = html_to_markdown("<ul><li>First</li><li>Second</li></ul><ol><li>Step one</li><li>Step two</li></ol>")
    check("- First" in md and "- Second" in md, "unordered list renders with - markers")
    check("1. Step one" in md and "2. Step two" in md, "ordered list renders with numbers")


def test_empty_and_garbage_safe():
    check(html_to_markdown("") == "", "empty input -> empty string")
    check(html_to_markdown("   ") == "", "whitespace input -> empty string")
    check(isinstance(html_to_markdown("<p>hi"), str), "unterminated tag does not raise")


def main():
    print("HTML->Markdown serializer checks")
    for fn in (
        test_headings_unwrap_strong,
        test_bold_runin_keeps_space,
        test_bold_runin_inserts_missing_space,
        test_link_with_bold_inside,
        test_no_space_injected_across_links,
        test_nested_strong_not_doubled,
        test_em_wrapping_strong_flattens,
        test_faq_accordion_promoted_to_heading,
        test_lazy_image_resolved,
        test_srcset_image_resolved,
        test_recipe_card_stripped,
        test_lists_and_ordered,
        test_empty_and_garbage_safe,
    ):
        print(f"\n[{fn.__name__}]")
        fn()
    print(f"\n{_checks} checks passed.")


if __name__ == "__main__":
    main()
