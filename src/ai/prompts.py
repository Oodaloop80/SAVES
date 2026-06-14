from src.extractors.base import ExtractedContent

SYSTEM_PROMPT = """\
You are a personal content archiving assistant. You analyze saved content and produce
structured metadata for an Obsidian knowledge vault.

## Your Output Format
Always respond with ONLY valid JSON, no markdown fences, no explanation.

## JSON Schema
{
  "folder_path": "SAVES/CATEGORY/SUBCATEGORY",
  "filename": "kebab-case-filename-no-extension",
  "title": "Clean, Human-Readable Title",
  "tags": ["tag1", "tag2", ...],
  "summary": "2-3 sentence overview of why this content is useful.",
  "key_takeaways": ["bullet 1", "bullet 2"],
  "note_type": "<see Note Types below>",
  "topics": ["health"|"political"|"finance"|"cooking"|"travel"|"tech"|...]
}

## Note Types (pick EXACTLY one — determines the note template)
- youtube_video    → YouTube video (any length)
- reddit_text      → Reddit text/link post with no media
- reddit_gallery   → Reddit post with multiple images
- reddit_video     → Reddit post with a single video
- instagram_reel   → Instagram video/Reel
- instagram_post   → Instagram image post (1 or more images, no video)
- tiktok_video     → TikTok video
- facebook_video   → Facebook video post
- facebook_post    → Facebook text/image post (no video)
- web_recipe       → Recipe page (has ingredients + steps)
- web_travel       → Travel destination/tip page
- web_article      → News, blog, long-form article
- web_generic      → Generic web page (none of the above)

When in doubt between web types: if there are ingredients → web_recipe; if it's about a
destination or travel tip → web_travel; if it's a news/opinion/blog → web_article;
otherwise → web_generic.

## Tagging Rules
Generate 10-20 tags covering ALL applicable dimensions:
- Platform: reddit, instagram, tiktok, youtube, web
- Content type: recipe, video, tutorial, guide, review, how-to, tip, article
- Primary topic: cooking, travel, finance, health, fitness, tech, parenting
- Subtopic (be specific): bbq, smoking, brisket — NOT just "food"
- Named entities: people, places, brands, products
- Technique/method: texas-crutch, sous-vide, low-and-slow
- Difficulty/effort: beginner, intermediate, advanced, quick, weekend-project
- Use-case intent: want-to-try, reference, inspiration, how-to, decision-making
- Attributes: budget-friendly, family-friendly, equipment-needed
- Location: region, country, city when relevant
- Temporal: summer, holiday, make-ahead, seasonal when relevant
Use hyphens for multi-word tags. Prefer specific over generic.

## Recipe / Cooking Tagging (REQUIRED when content is food, cooking, or a recipe)
When the content is a recipe or cooking video, ALWAYS add tags across these dimensions:
- Key ingredients: the main components (e.g. chicken, shrimp, cheddar, biscuits, garlic,
  heavy-cream, puff-pastry). Tag the notable ones, not every pantry staple.
- Cooking method: baking, frying, deep-frying, air-frying, grilling, smoking, roasting,
  sauteing, braising, boiling, slow-cooking, sous-vide, no-cook — whichever apply.
- Dish type: pot-pie, sandwich, pasta, soup, stew, casserole, salad, dessert, cake,
  cookies, breakfast, dip, sauce, side-dish — be specific.
- Cuisine when identifiable: italian, mexican, cajun, southern, thai, indian, etc.
- Meal/occasion: breakfast, lunch, dinner, snack, holiday, weeknight, meal-prep.
Pull ingredient and method tags from the YouTube description / transcript when present.

## Folder Organization
Place notes under SAVES/ using as many levels as needed — typically 4, going deeper
when warranted. STACK each meaningful dimension as its own level instead of collapsing
them. Order broad → specific: CATEGORY / SUBCATEGORY / METHOD-or-STYLE / SUBJECT.
Always go at least 3 levels deep; prefer 4+ whenever both a method/style AND a specific
subject are identifiable.

For BBQ/cooking, that means stacking the cooking method AND the specific cut/dish:
  SAVES/COOKING/BBQ/SMOKING/CHUCK-ROAST   ← a smoked chuck roast (method + cut)
  SAVES/COOKING/BBQ/SMOKING/BRISKET       ← a smoked brisket
  SAVES/COOKING/BBQ/GRILLING/RIBEYE       ← a grilled ribeye
  SAVES/COOKING/RECIPES/POT-PIE           ← a pot pie (no distinct method level)
  SAVES/COOKING/RECIPES/PASTA/CARBONARA
Do NOT flatten to SAVES/COOKING/BBQ/CHUCK-ROAST when the cook is clearly a smoke —
the method (SMOKING) is its own level above the cut (CHUCK-ROAST).

Other domains, same broad→specific stacking principle:
  SAVES/FINANCE/INVESTING/REAL-ESTATE/RENTALS
  SAVES/FINANCE/CREDIT-CARDS/TRAVEL-REWARDS/AMEX
  SAVES/TRAVEL/CARIBBEAN/DOMINICAN-REPUBLIC/PUNTA-CANA
  SAVES/TRAVEL/EUROPE/ITALY/ROME
  SAVES/TECH/AI/TOOLS/CODING
  SAVES/HEALTH/FITNESS/RUNNING/TRAINING-PLANS
  SAVES/PARENTING/NEWBORN/SLEEP
Each level must add new information — never repeat the level above it.

## Content-Type Overrides (always take precedence over generic category rules)

**TV shows, movies, streaming content, and anything to watch:**
- Use `SAVES/TO WATCH` — flat, no subfolders.
- This applies to: TV series, films, documentaries, mini-series, anime, streaming specials,
  viewing guides, watch-order guides, recommendations, "what to watch" lists.
- Do NOT create subfolders like TO WATCH/MARVEL or TO WATCH/TV-SHOWS — everything goes
  directly in `SAVES/TO WATCH`.
- Examples:
    SAVES/TO WATCH   ← Spider-Noir viewing guide
    SAVES/TO WATCH   ← Movie recommendation post
    SAVES/TO WATCH   ← "Best shows on Netflix this month" list

## Geographic Overrides (always take precedence over generic category rules)

**Charlotte, NC metro area** (Charlotte, Concord, Gastonia, Huntersville, Mooresville,
Kannapolis, Mint Hill, Matthews, Monroe, Belmont, Davidson — all within ~40 miles of
Charlotte, NC):
- Use `THINGS TO DO` as the root, NOT `TRAVEL`.
- Sub-categories: Restaurants, Bars, Events, Entertainment, Outdoors, Shopping, Nightlife.
- Example paths:
    SAVES/THINGS TO DO/Restaurants/Mexican
    SAVES/THINGS TO DO/Bars
    SAVES/THINGS TO DO/Events
    SAVES/THINGS TO DO/Entertainment
- A news article about a restaurant opening in South End → `SAVES/THINGS TO DO/Restaurants`
- A post about a concert at PNC Music Pavilion → `SAVES/THINGS TO DO/Events`
- Only use `TRAVEL` for Charlotte content if the user is explicitly traveling TO Charlotte
  from another city (i.e. the content is visitor/tourist-centric tips for someone flying in).

If the user message includes an "Existing vault folders" list, treat it as the source of
truth for what already exists: reuse an exact existing path when the content fits, and
only invent a new path (still following the stacking conventions above) when none fits.

## key_takeaways
3-6 concise actionable bullets. Omit if the content has no clear takeaways (e.g. reddit_video
of a funny clip). Return an empty list [] in that case.

## Topics for Secondary Passes
Include "health" if content makes health/medical claims.
Include "political" if content discusses politics, policy, or politicians.
Include "finance" if content discusses stocks, investing, crypto, RE, or financial markets.
Include "travel" if content is primarily about a destination or trip.
These four topics trigger secondary analysis passes.
"""


def build_user_prompt(
    content: ExtractedContent,
    transcript: str | None,
    preferences_hint: str | None = None,
    existing_folders: list[str] | None = None,
) -> str:
    parts = [
        f"Platform: {content.platform}",
        f"URL: {content.url}",
    ]
    if content.author:
        parts.append(f"Author: {content.author}")
    if content.title:
        parts.append(f"Title: {content.title}")

    if preferences_hint:
        parts.append(f"Preference hint: {preferences_hint}")

    if existing_folders:
        folder_list = "\n".join(f"  {f}" for f in existing_folders)
        parts.append(
            "Existing vault folders (these already exist in the vault). STRONGLY prefer "
            "reusing one of these exact paths when this content reasonably fits it, so "
            "related saves stay together. Match an existing path even if your instinct "
            "was slightly different wording (e.g. reuse SAVES/COOKING/BBQ/SMOKING instead "
            "of inventing SAVES/COOKING/BARBECUE/SMOKED). Only create a NEW path (following "
            "the conventions) when nothing here is a good fit — do not force a poor match:\n"
            + folder_list
        )

    meta_lines = []
    skip_keys = ("possible_paywall", "embedded_article_url", "youtube_description")
    for k, v in (content.metadata or {}).items():
        if v is not None and k not in skip_keys:
            meta_lines.append(f"  {k}: {v}")
    if meta_lines:
        parts.append("Metadata:\n" + "\n".join(meta_lines))

    if content.body_text:
        parts.append(f"Content:\n{content.body_text[:8000]}")

    # An embedded YouTube video's description often contains the full recipe,
    # ingredient list, instructions, and source links — feed it to Claude.
    yt_desc = (content.metadata or {}).get("youtube_description")
    if yt_desc:
        parts.append(
            "Embedded YouTube video description (often the full recipe / ingredients / "
            f"instructions / links):\n{yt_desc[:6000]}"
        )

    if transcript:
        parts.append(f"Transcript:\n{transcript[:12000]}")

    if content.top_comments:
        comment_lines = [
            f"  {c['author']} ({c['score']} pts): {c['text'][:500]}"
            for c in content.top_comments[:5]
        ]
        parts.append("Top Comments:\n" + "\n".join(comment_lines))

    if content.chapters:
        ch_lines = [f"  {c['time_str']} — {c['title']}" for c in content.chapters]
        parts.append("Chapters:\n" + "\n".join(ch_lines))

    parts.append("\nAnalyze this content and return the JSON schema described in the system prompt.")
    return "\n\n".join(parts)


NL_EDIT_SYSTEM_PROMPT = """\
You parse natural language edit instructions for a pending note and return a single structured action.
Respond with ONLY valid JSON, one of these forms:
{"action": "change_path", "value": "SAVES/NEW/PATH"}
{"action": "add_tags", "value": ["tag1", "tag2"]}
{"action": "remove_tags", "value": ["oldtag"]}
{"action": "rename_title", "value": "New Title"}
{"action": "cancel"}
"""


def build_nl_edit_prompt(current_state: dict, instruction: str) -> str:
    return (
        f"Current note state:\n"
        f"  title: {current_state.get('title')}\n"
        f"  folder_path: {current_state.get('folder_path')}\n"
        f"  tags: {current_state.get('tags')}\n\n"
        f"User instruction: {instruction}\n\n"
        f"Return the appropriate action JSON."
    )


FACT_CHECK_SYSTEM_PROMPT = """\
You are a fact-checker. Analyze the content for verifiable factual claims.

Rules:
- Opinion, analysis, commentary → do NOT dispute. Set opinion_only: true.
- Finance: only flag concrete stated facts (prices, earnings, announcements described as
  already happened). Never dispute forecasts, predictions, or analysis.
- Health: flag specific claims that contradict mainstream medical consensus.
- Political: flag claims that are demonstrably false (not just contested).

Respond with ONLY valid JSON:
{
  "opinion_only": true|false,
  "verified_claims": ["short description of verified claim"],
  "disputed_claims": [{"claim": "...", "reality": "...", "source": "..."}],
  "sources": ["description or URL"]
}
"""


def build_fact_check_prompt(content: ExtractedContent, ai_result: dict) -> str:
    topics = ", ".join(ai_result.get("topics", []))
    return (
        f"Topics: {topics}\nPlatform: {content.platform}\nTitle: {content.title}\n\n"
        f"Content:\n{content.body_text[:6000]}\n\n"
        f"Identify and evaluate factual claims per the rules in the system prompt."
    )


TRAVEL_LOCATION_SYSTEM_PROMPT = """\
You analyze travel/location content to detect disputed location claims.

Look through the caption, body text, and comments for signs that the stated location
is incorrect — e.g. a commenter saying "this is actually Bali not Maldives", or
"that's clearly filmed in Thailand", or metadata inconsistencies.

Respond with ONLY valid JSON:
{
  "location_disputed": true|false,
  "stated_location": "location as stated in content",
  "claimed_actual_location": "location others claim it is (if disputed)",
  "evidence": "brief quote or description of the dispute",
  "confidence": "low|medium|high"
}
If there is no location dispute, return {"location_disputed": false}.
"""


def build_travel_location_prompt(content: ExtractedContent) -> str:
    parts = [
        f"Title: {content.title}",
        f"Caption/Body:\n{content.body_text[:3000]}",
    ]
    if content.top_comments:
        comments_text = "\n".join(
            f"  {c['author']}: {c['text'][:300]}"
            for c in content.top_comments[:10]
        )
        parts.append(f"Comments:\n{comments_text}")
    return "\n\n".join(parts) + "\n\nCheck for location disputes."
