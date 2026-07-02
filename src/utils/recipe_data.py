"""Structured recipe extraction from schema.org Recipe JSON-LD.

Almost every recipe blog (WordPress Recipe Maker, Tasty, etc.) embeds the full recipe as
`<script type="application/ld+json">` with `@type: "Recipe"` — the exact ingredient list with
quantities, every instruction step, yield, and times as the author entered them. That is far
more accurate than scraping the rendered article text (which mixes in a long personal-story
preamble and often gets truncated before the recipe card).

This module parses that JSON-LD into a normalized dict and provides:
  - `extract_recipe_jsonld(html)` — parse a page's HTML → recipe dict (or None).
  - `format_recipe_data_for_prompt(data)` — render it for Claude as an authoritative source.
  - `apply_structured_recipe(ai_result, content)` — deterministically backfill the note's
    recipe_* fields from the structured data (exact quantities, all steps), so accuracy does
    not depend on the model transcribing a long page. English sources only (keeps Claude's
    translation for other languages).

All parsing is defensive: malformed JSON or unexpected shapes yield None rather than raising.
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


def extract_recipe_jsonld(html: str) -> dict | None:
    """Find the schema.org Recipe in the page's JSON-LD and normalize it. None if absent."""
    node = None
    for n in _iter_jsonld_nodes(html):
        if _type_matches(n, "Recipe") and (n.get("recipeIngredient") or n.get("recipeInstructions")):
            node = n
            break
    if node is None:
        return None

    ingredients = [_clean_text(i) for i in _as_list(node.get("recipeIngredient"))]
    ingredients = [i for i in ingredients if i]
    instructions = _normalize_instructions(node.get("recipeInstructions"))
    if not ingredients and not instructions:
        return None

    data = {
        "name": _clean_text(node.get("name")) or None,
        "ingredients": ingredients or None,
        "instructions": instructions or None,
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
        "schema.org Recipe JSON-LD: %d ingredient(s), %d step(s)",
        len(ingredients), len(instructions),
    )
    return data


def format_recipe_data_for_prompt(data: dict, limit: int = 8000) -> str:
    """Render the structured recipe as plain text for Claude's user prompt."""
    lines: list[str] = []
    if data.get("name"):
        lines.append(f"Name: {data['name']}")
    if data.get("yield"):
        lines.append(f"Yield: {data['yield']}")
    if data.get("time_str"):
        lines.append(f"Time: {data['time_str']}")
    if data.get("ingredients"):
        lines.append("Ingredients:")
        lines += [f"- {i}" for i in data["ingredients"]]
    if data.get("instructions"):
        lines.append("Instructions:")
        lines += [f"{n}. {s}" for n, s in enumerate(data["instructions"], 1)]
    return "\n".join(lines)[:limit]


def apply_structured_recipe(ai_result: dict, content) -> dict:
    """Backfill the note's recipe_* fields from authoritative schema.org data when available,
    so exact quantities and every step survive regardless of what the model transcribed.

    Overrides ingredients/instructions only for English sources (keeps Claude's translated
    recipe otherwise); fills servings/time only when the model left them blank. Non-fatal and
    idempotent. Reads `followed_recipe_data` (bio-follow) or `recipe_data` (direct paste)."""
    m = getattr(content, "metadata", None) or {}
    data = m.get("followed_recipe_data") or m.get("recipe_data")
    if not data:
        return ai_result
    lang = (ai_result.get("source_language") or "English").strip().lower()
    is_english = lang.startswith("english") or lang in ("", "en")
    if is_english:
        if data.get("ingredients"):
            ai_result["recipe_ingredients"] = data["ingredients"]
        if data.get("instructions"):
            ai_result["recipe_instructions"] = data["instructions"]
    if not ai_result.get("recipe_servings") and data.get("yield"):
        ai_result["recipe_servings"] = data["yield"]
    if not ai_result.get("recipe_time") and data.get("time_str"):
        ai_result["recipe_time"] = data["time_str"]
    return ai_result
