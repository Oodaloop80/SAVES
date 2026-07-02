"""Deterministic metric → imperial / Celsius → Fahrenheit conversion for recipe text.

Given a recipe string like ``"Bake at 180°C for 25 min. Add 250 g flour and 500 ml milk."``
this appends the imperial/Fahrenheit equivalent in parentheses after each metric quantity,
preserving the original:

    "Bake at 180°C (≈355°F) for 25 min. Add 250 g (≈8.8 oz) flour and 500 ml (≈2 cups) milk."

Design constraints:
- Original text is never replaced — only annotated — so nothing is lost or mis-rounded away.
- Idempotent: a quantity already followed by a parenthetical (e.g. the source recipe wrote
  "180°C (356°F)", or this ran once already) is skipped via a ``(?!\\s*\\()`` lookahead.
- A quantity that is itself the tail of a parenthetical the author already wrote — e.g.
  "¼ cup (60 ml)" — is likewise skipped (lookahead also rejects a following ``)``), so we
  never nest a conversion inside the author's own equivalent: "¼ cup (60 ml (≈0.3 cups))".
- Deliberately conservative about ambiguous tokens. Bare "C" is NOT treated as Celsius
  (US recipes abbreviate *cup* as "C" — "1 C sugar"); Celsius requires the ° symbol, the
  word, or a 3-digit oven-temp magnitude. Bare lowercase "l" is not treated as litres
  (looks like a 1). This trades a few missed conversions for never emitting a wrong one.
"""

import re

# ── conversion factors ───────────────────────────────────────────────
_G_PER_OZ = 28.349523
_G_PER_LB = 453.59237
_ML_PER_TSP = 4.928922
_ML_PER_TBSP = 14.786765
_ML_PER_CUP = 236.588236
_CM_PER_IN = 2.54
_MM_PER_IN = 25.4


def _num(n: float) -> str:
    """Compact number: drop the decimal when it rounds to a whole, else one decimal place."""
    r = round(n, 1)
    if abs(r - round(r)) < 0.05:
        return str(int(round(r)))
    return f"{r:.1f}"


def _c_to_f(c: float) -> str:
    return f"≈{_num(c * 9 / 5 + 32)}°F"


def _mass_g(g: float) -> str:
    # Ounces for the typical recipe range; pounds only once grams get large enough that
    # dozens of ounces would read worse (kilograms always go straight to pounds below).
    if g >= 1000:
        return f"≈{_num(g / _G_PER_LB)} lb"
    return f"≈{_num(g / _G_PER_OZ)} oz"


def _mass_kg(kg: float) -> str:
    return f"≈{_num(kg * 1000 / _G_PER_LB)} lb"


def _volume_ml(ml: float) -> str:
    if ml < 15:
        return f"≈{_num(ml / _ML_PER_TSP)} tsp"
    if ml < 60:
        return f"≈{_num(ml / _ML_PER_TBSP)} tbsp"
    cups = ml / _ML_PER_CUP
    return f"≈{_num(cups)} cup" if abs(cups - 1) < 0.05 else f"≈{_num(cups)} cups"


def _len_cm(cm: float) -> str:
    return f"≈{_num(cm / _CM_PER_IN)} in"


def _len_mm(mm: float) -> str:
    return f"≈{_num(mm / _MM_PER_IN)} in"


_NUM = r"(\d+(?:\.\d+)?)"
_NOT_ALREADY_CONVERTED = r"(?!\s*[()])"  # skip if a parenthetical already follows, or if this
#                                          quantity is the tail of one ("¼ cup (60 ml)") — either
#                                          way, annotating it would nest parens.

# Each rule: (compiled pattern with one numeric capture group, converter(value) -> annotation).
# Order matters only in that longer units (kg, ml) must be tried before their substrings; the
# alternations below already encode that, and every rule is applied in its own single pass.
_RULES: list[tuple[re.Pattern, callable]] = [
    # Temperature — requires ° or the explicit word, so "1 C sugar" (a cup) is never touched.
    # Longer forms first so "°Celsius" isn't half-matched as "°C" then annotated mid-word.
    (re.compile(_NUM + r"\s*(?:°\s*[Cc]elsius\b|°\s*[Cc]\b|degrees?\s*[Cc](?:elsius)?\b|[Cc]elsius\b)" + _NOT_ALREADY_CONVERTED), _c_to_f),
    # Bare 3-digit "C" (e.g. "180C", "200 C") — an oven temperature; no recipe means 180 cups.
    # Lookbehind keeps it from matching the last 3 digits of a longer number ("1800C").
    (re.compile(r"(?<!\d)(\d{3})\s*C\b" + _NOT_ALREADY_CONVERTED), _c_to_f),
    # Mass — kg before g so "500 kg" isn't half-matched by the gram rule.
    (re.compile(_NUM + r"\s*(?:kg|kilograms?|kilos?)\b" + _NOT_ALREADY_CONVERTED), _mass_kg),
    (re.compile(_NUM + r"\s*(?:g|grams?)\b" + _NOT_ALREADY_CONVERTED), _mass_g),
    # Volume — ml, then litres (capital L or the word only; bare lowercase "l" is ambiguous).
    (re.compile(_NUM + r"\s*(?:ml|millilit(?:er|re)s?)\b" + _NOT_ALREADY_CONVERTED), _volume_ml),
    (re.compile(_NUM + r"\s*(?:L|lit(?:er|re)s?)\b" + _NOT_ALREADY_CONVERTED), lambda v: _volume_ml(v * 1000)),
    # Length — cm / mm (bare "m"/"km" skipped; rare in recipes and easy to mis-hit).
    (re.compile(_NUM + r"\s*(?:cm|centimet(?:er|re)s?)\b" + _NOT_ALREADY_CONVERTED), _len_cm),
    (re.compile(_NUM + r"\s*(?:mm|millimet(?:er|re)s?)\b" + _NOT_ALREADY_CONVERTED), _len_mm),
]


def convert_measurements(text: str) -> str:
    """Annotate metric measurements and Celsius temperatures in ``text`` with imperial /
    Fahrenheit equivalents in parentheses. Returns the text unchanged when there is nothing
    to convert. Safe to call more than once (idempotent)."""
    if not text:
        return text
    for pattern, convert in _RULES:
        def _repl(m: "re.Match", _conv=convert) -> str:
            try:
                value = float(m.group(1))
            except (TypeError, ValueError):
                return m.group(0)
            return f"{m.group(0)} ({_conv(value)})"
        text = pattern.sub(_repl, text)
    return text
