"""Structured recipe extraction from a recipe page's HTML.

Two complementary sources are combined:

1. **schema.org Recipe JSON-LD** (`<script type="application/ld+json">`, `@type: "Recipe"`) —
   the exact ingredient list with quantities, every instruction step, yield, times, nutrition,
   as the author entered them. Reliable, but it FLATTENS the recipe: it drops the ingredient
   section titles ("For the garlic confit", "For the pasta", "To finish") and the bold step
   lead-ins that make the method readable.

2. **WordPress Recipe Maker (WPRM) card HTML** — the dominant recipe plugin. Its markup keeps
   the section titles and per-step bold lead-ins. We parse it for GROUPED ingredients and
   instructions and prefer it when present; otherwise we fall back to the flat JSON-LD.

Public API:
  - `extract_recipe_jsonld(html)` — parse a page → normalized recipe dict (or None). Name kept
    for back-compat; it now also merges the HTML-derived groups.
  - `format_recipe_data_for_prompt(data)` — render it for Claude as an authoritative source.
  - `apply_structured_recipe(ai_result, content)` — deterministically backfill the note's
    recipe_* fields (flat + grouped) from the structured data. English sources only for the
    ingredient/instruction override (keeps Claude's translation for other languages).

All parsing is defensive: malformed JSON/HTML or unexpected shapes yield None/empty rather
than raising.
"""
import html as _html
import json
import logging
import re

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


def _clean_text(x) -> str:
    """Strip HTML tags, unescape entities, and collapse whitespace to a single line."""
    if x is None:
        return ""
    if not isinstance(x, str):
        x = str(x)
    x = _TAG_RE.sub(" ", x)
    x = _html.unescape(x)
    return re.sub(r"\s+", " ", x).strip()


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _type_matches(node: dict, wanted: str) -> bool:
    t = node.get("@type")
    return t == wanted or (isinstance(t, list) and wanted in t)


def iso8601_duration_to_human(s) -> str | None:
    """`PT1H30M` → `1 hr 30 min`, `PT45M` → `45 min`. Returns None if unparseable/zero."""
    if not s or not isinstance(s, str):
        return None
    m = re.fullmatch(
        r"\s*P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?\s*", s, re.I
    )
    if not m:
        return None
    weeks, days, hours, minutes, seconds = (int(x) if x else 0 for x in m.groups())
    total_min = weeks * 7 * 24 * 60 + days * 24 * 60 + hours * 60 + minutes
    if seconds and total_min == 0:
        total_min = 1  # round a sub-minute duration up so it isn't dropped
    if total_min <= 0:
        return None
    h, mm = divmod(total_min, 60)
    parts = []
    if h:
        parts.append(f"{h} hr")
    if mm:
        parts.append(f"{mm} min")
    return " ".join(parts) if parts else None


def _yield_str(v) -> str | None:
    """recipeYield can be '4 servings', ['4', '4 servings'], or a bare number."""
    if v is None:
        return None
    if isinstance(v, list):
        # Prefer the most descriptive entry (the one containing letters, e.g. '4 servings').
        best = ""
        for item in v:
            s = _clean_text(item)
            if any(c.isalpha() for c in s) and len(s) > len(best):
                best = s
            elif not best:
                best = s
        v = best
    s = _clean_text(v)
    if not s:
        return None
    if s.isdigit():
        return f"Serves {s}"
    return s


def _normalize_instructions(val) -> list[str]:
    """Flatten recipeInstructions (string, list of strings, HowToStep, or HowToSection with
    itemListElement) into an ordered list of clean step strings."""
    out: list[str] = []

    def add(x):
        t = _clean_text(x)
        if t:
            out.append(t)

    if val is None:
        return out
    if isinstance(val, str):
        for line in re.split(r"\n+", val):
            add(line)
        return out
    for item in _as_list(val):
        if isinstance(item, str):
            add(item)
        elif isinstance(item, dict):
            if _type_matches(item, "HowToSection") or item.get("itemListElement"):
                for sub in _as_list(item.get("itemListElement")):
                    if isinstance(sub, dict):
                        add(sub.get("text") or sub.get("name"))
                    else:
                        add(sub)
            else:
                add(item.get("text") or item.get("name"))
    return out


def _iter_jsonld_nodes(html: str):
    """Yield every dict node inside the page's JSON-LD blocks, descending @graph/nested."""
    for m in _JSONLD_RE.finditer(html or ""):
        raw = (m.group(1) or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            try:
                data = json.loads(raw.replace("\r", " ").replace("\n", " "))
            except Exception:
                continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                yield node
                for v in node.values():
                    if isinstance(v, (list, dict)):
                        stack.append(v)
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, (list, dict)):
                        stack.append(item)


def _find_recipe_node(html: str) -> dict | None:
    for n in _iter_jsonld_nodes(html):
        if _type_matches(n, "Recipe") and (n.get("recipeIngredient") or n.get("recipeInstructions")):
            return n
    return None


# ── Inline HTML → Markdown (preserve bold/italic step lead-ins; drop links & other tags) ──

def _inline_md(fragment: str) -> str:
    """Convert a small HTML fragment (one instruction step) to inline Markdown, keeping
    <strong>/<b> as **bold** and <em>/<i> as *italic* (this is what makes the method
    readable — e.g. "**Confit the garlic.** Place the cloves…"), turning links into plain
    text, and dropping everything else."""
    if not fragment:
        return ""
    s = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
    s = re.sub(r"<(strong|b)\b[^>]*>(.*?)</\1>",
               lambda m: f" **{_clean_text(m.group(2))}** ", s, flags=re.I | re.S)
    s = re.sub(r"<(em|i)\b[^>]*>(.*?)</\1>",
               lambda m: f" *{_clean_text(m.group(2))}* ", s, flags=re.I | re.S)
    s = re.sub(r"<a\b[^>]*>(.*?)</a>", lambda m: f" {m.group(1)} ", s, flags=re.I | re.S)
    out = _clean_text(s)  # strips remaining tags, unescapes, collapses whitespace
    out = out.replace("** **", " ").replace("* *", " ").replace("****", "")
    return re.sub(r"\s+", " ", out).strip()


def _inner_html(el) -> str:
    from lxml import etree
    parts = [el.text or ""]
    for child in el:
        parts.append(etree.tostring(child, encoding="unicode", method="html"))
    return "".join(parts)


def _first_group_name(elements) -> str | None:
    if not elements:
        return None
    name = _clean_text(elements[0].text_content()).rstrip(":").strip()
    return name or None


def _assemble_ingredient(li) -> str:
    """Rebuild one ingredient from WPRM's amount/unit/name/notes spans, dropping affiliate
    links (kept as plain text). Falls back to the li's whole text if the spans are absent."""
    def cls(c):
        els = li.find_class(c)
        return _clean_text(els[0].text_content()) if els else ""

    amount = cls("wprm-recipe-ingredient-amount")
    unit = cls("wprm-recipe-ingredient-unit")
    name = cls("wprm-recipe-ingredient-name")
    notes = cls("wprm-recipe-ingredient-notes")
    head = " ".join(x for x in (amount, unit, name) if x)
    if notes:
        if not head:
            return notes
        return f"{head} {notes}" if notes.startswith("(") else f"{head}, {notes}"
    return head or _clean_text(li.text_content())


def _assemble_instruction(li) -> str:
    els = li.find_class("wprm-recipe-instruction-text")
    el = els[0] if els else li
    return _inline_md(_inner_html(el))


def extract_recipe_groups_from_html(html: str):
    """Parse WordPress-Recipe-Maker card HTML for GROUPED ingredients/instructions with their
    section titles ('For the garlic confit', …). Returns (ingredient_groups, instruction_groups)
    where each group is {'name': str|None, 'items'|'steps': [...]}. Empty lists if not WPRM."""
    if not html:
        return [], []
    try:
        import lxml.html as LH
        doc = LH.fromstring(html)
    except Exception:
        return [], []

    ing_groups = []
    for g in doc.find_class("wprm-recipe-ingredient-group"):
        name = _first_group_name(g.find_class("wprm-recipe-ingredient-group-name"))
        items = [_assemble_ingredient(li) for li in g.find_class("wprm-recipe-ingredient")]
        items = [i for i in items if i]
        if items:
            ing_groups.append({"name": name, "items": items})

    ins_groups = []
    for g in doc.find_class("wprm-recipe-instruction-group"):
        name = _first_group_name(g.find_class("wprm-recipe-instruction-group-name"))
        steps = [_assemble_instruction(li) for li in g.find_class("wprm-recipe-instruction")]
        steps = [s for s in steps if s]
        if steps:
            ins_groups.append({"name": name, "steps": steps})

    return ing_groups, ins_groups


def _flatten_groups(groups, key) -> list:
    out = []
    for g in groups or []:
        out.extend(g.get(key) or [])
    return out


def extract_recipe_jsonld(html: str) -> dict | None:
    """Find the page's recipe (schema.org JSON-LD + WPRM grouping) and normalize it. Prefers
    the grouped WPRM lists (they carry the section titles and readable bold step lead-ins) and
    falls back to the flat JSON-LD. None if the page has no recipe at all."""
    ing_groups, ins_groups = extract_recipe_groups_from_html(html)
    node = _find_recipe_node(html)
    if node is None and not (ing_groups or ins_groups):
        return None
    node = node or {}

    jl_ingredients = [i for i in (_clean_text(x) for x in _as_list(node.get("recipeIngredient"))) if i]
    jl_instructions = _normalize_instructions(node.get("recipeInstructions"))

    ingredients = _flatten_groups(ing_groups, "items") if ing_groups else jl_ingredients
    instructions = _flatten_groups(ins_groups, "steps") if ins_groups else jl_instructions
    if not ingredients and not instructions:
        return None

    data = {
        "name": _clean_text(node.get("name")) or None,
        "ingredients": ingredients or None,
        "instructions": instructions or None,
        "ingredient_groups": ing_groups or None,
        "instruction_groups": ins_groups or None,
        "yield": _yield_str(node.get("recipeYield")),
        "prep_time": iso8601_duration_to_human(node.get("prepTime")),
        "cook_time": iso8601_duration_to_human(node.get("cookTime")),
        "total_time": iso8601_duration_to_human(node.get("totalTime")),
    }
    bits = []
    if data["prep_time"]:
        bits.append(f"Prep: {data['prep_time']}")
    if data["cook_time"]:
        bits.append(f"Cook: {data['cook_time']}")
    if data["total_time"]:
        bits.append(f"Total: {data['total_time']}")
    data["time_str"] = " · ".join(bits) or None
    logger.info(
        "Recipe parsed: %d ingredient(s) in %d group(s), %d step(s) in %d group(s)",
        len(ingredients), len(ing_groups), len(instructions), len(ins_groups),
    )
    return data


def format_recipe_data_for_prompt(data: dict, limit: int = 8000) -> str:
    """Render the structured recipe as plain text for Claude's user prompt, preserving the
    ingredient/instruction section titles so the model knows the recipe has parts."""
    lines: list[str] = []
    if data.get("name"):
        lines.append(f"Name: {data['name']}")
    if data.get("yield"):
        lines.append(f"Yield: {data['yield']}")
    if data.get("time_str"):
        lines.append(f"Time: {data['time_str']}")

    ing_groups = data.get("ingredient_groups")
    if ing_groups:
        lines.append("Ingredients (keep these section titles):")
        for g in ing_groups:
            if g.get("name"):
                lines.append(f"  [{g['name']}]")
            lines += [f"  - {i}" for i in g.get("items") or []]
    elif data.get("ingredients"):
        lines.append("Ingredients:")
        lines += [f"- {i}" for i in data["ingredients"]]

    ins_groups = data.get("instruction_groups")
    if ins_groups:
        lines.append("Instructions (keep these section titles and the bold step lead-ins):")
        for g in ins_groups:
            if g.get("name"):
                lines.append(f"  [{g['name']}]")
            for n, s in enumerate(g.get("steps") or [], 1):
                lines.append(f"  {n}. {s}")
    elif data.get("instructions"):
        lines.append("Instructions:")
        lines += [f"{n}. {s}" for n, s in enumerate(data["instructions"], 1)]

    return "\n".join(lines)[:limit]


def apply_structured_recipe(ai_result: dict, content) -> dict:
    """Backfill the note's recipe_* fields from authoritative structured data when available,
    so exact quantities, every step, the section titles, and the bold method lead-ins survive
    regardless of what the model transcribed.

    Sets both the grouped fields (`recipe_ingredient_groups`, `recipe_instruction_groups`) and
    the flat fields (kept in sync). Overrides only for English sources (keeps Claude's
    translated recipe otherwise, and never clobbers grouping the model itself produced with a
    flat JSON-LD fallback). Fills servings/time when blank. Non-fatal and idempotent. Reads
    `followed_recipe_data` (bio-follow) or `recipe_data` (direct paste)."""
    m = getattr(content, "metadata", None) or {}
    data = m.get("followed_recipe_data") or m.get("recipe_data")
    if not data:
        return ai_result
    lang = (ai_result.get("source_language") or "English").strip().lower()
    is_english = lang.startswith("english") or lang in ("", "en")

    if is_english:
        ig = data.get("ingredient_groups")
        if ig:
            ai_result["recipe_ingredient_groups"] = ig
            ai_result["recipe_ingredients"] = _flatten_groups(ig, "items")
        elif data.get("ingredients") and not ai_result.get("recipe_ingredient_groups"):
            ai_result["recipe_ingredients"] = data["ingredients"]

        mg = data.get("instruction_groups")
        if mg:
            ai_result["recipe_instruction_groups"] = mg
            ai_result["recipe_instructions"] = _flatten_groups(mg, "steps")
        elif data.get("instructions") and not ai_result.get("recipe_instruction_groups"):
            ai_result["recipe_instructions"] = data["instructions"]

    if not ai_result.get("recipe_servings") and data.get("yield"):
        ai_result["recipe_servings"] = data["yield"]
    if not ai_result.get("recipe_time") and data.get("time_str"):
        ai_result["recipe_time"] = data["time_str"]
    return ai_result
