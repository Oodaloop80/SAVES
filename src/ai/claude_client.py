import json
import logging
import os

import anthropic

from src.ai.prompts import (
    FACT_CHECK_SYSTEM_PROMPT,
    IMAGE_OCR_SYSTEM_PROMPT,
    NL_EDIT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_fact_check_prompt,
    build_image_ocr_prompt,
    build_nl_edit_prompt,
    build_user_prompt,
)
from src.extractors.base import ExtractedContent

logger = logging.getLogger(__name__)

# Models that have rejected the `temperature` parameter this process. Once a model 400s on
# temperature we stop sending it for that model entirely — so we don't fire a doomed request
# (and log a line) on every subsequent call. Self-learning: models that DO accept temperature
# (e.g. sonnet/haiku) keep getting it.
_MODELS_REJECTING_TEMPERATURE: set[str] = set()


def _make_client(config: dict | None = None) -> anthropic.Anthropic:
    # The Anthropic SDK retries transient failures (429/500/502/503/529 + connection errors)
    # with exponential backoff and honors the server's Retry-After header — strictly better
    # for an HTTP API than a fixed-delay wrapper. We just make the attempt count configurable
    # (ai.max_retries) so a flaky-network deploy can lean on it harder. This is the "Claude
    # API backoff"; utils/retry.py is instead wired into the remote-transcription POST, which
    # the SDK does not cover.
    max_retries = (config or {}).get("ai", {}).get("max_retries", 4)
    return anthropic.Anthropic(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        max_retries=max_retries,
    )


def _call(
    client: anthropic.Anthropic,
    system: str,
    user: str | list,
    config: dict,
) -> str:
    """Call the Claude API. `user` may be a plain string or a list of content blocks.

    Newer models (e.g. claude-opus-4-8) reject the `temperature` parameter. We send it
    when configured, but transparently retry without it if the API reports it deprecated.
    """
    ai_cfg = config.get("ai", {})
    model = ai_cfg.get("model", "claude-opus-4-8")
    params = {
        "model": model,
        "max_tokens": ai_cfg.get("max_tokens", 4096),
        # System prompts here (analysis/OCR/fact-check/NL-edit) are large static strings.
        # Marking them as an ephemeral cache breakpoint lets the JSON-retry call and
        # back-to-back posts read the prefix from cache (~90% cheaper) instead of re-billing
        # it. Below the model's cache minimum the marker is simply ignored — never an error.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }
    # Only send temperature if configured AND this model hasn't already rejected it. This
    # avoids a wasted failed request (and a log line) on every call to a model like
    # claude-opus-4-8 that doesn't accept the parameter.
    temperature = ai_cfg.get("temperature")
    if temperature is not None and model not in _MODELS_REJECTING_TEMPERATURE:
        params["temperature"] = temperature

    try:
        msg = client.messages.create(**params)
    except anthropic.BadRequestError as e:
        if "temperature" in str(e) and "temperature" in params:
            logger.debug("Model %s rejects `temperature`; retrying without it (and skipping it from now on)", model)
            _MODELS_REJECTING_TEMPERATURE.add(model)
            params.pop("temperature", None)
            msg = client.messages.create(**params)
        else:
            raise
    return msg.content[0].text


async def analyze_content(
    content: ExtractedContent,
    transcript: str | None,
    config: dict,
    preferences_hint: str | None = None,
    image_blocks: list[dict] | None = None,
    existing_folders: list[str] | None = None,
) -> dict:
    import asyncio
    return await asyncio.to_thread(
        _analyze_sync, content, transcript, config, preferences_hint,
        image_blocks, existing_folders,
    )


def _analyze_sync(
    content: ExtractedContent,
    transcript: str | None,
    config: dict,
    preferences_hint: str | None = None,
    image_blocks: list[dict] | None = None,
    existing_folders: list[str] | None = None,
) -> dict:
    client = _make_client(config)

    # Two-stage option: a cheap vision model (vision.ocr_model) reads the image slides
    # into text, then the main ai.model does the full analysis on text only — no images.
    # This keeps the costly vision tokens on the cheap model while reasoning/routing stays
    # on the capable model. If ocr_model is unset, fall back to a single combined call.
    ocr_model = config.get("vision", {}).get("ocr_model")
    ocr_text: str | None = None
    if image_blocks and ocr_model:
        try:
            ocr_text = _ocr_images_sync(client, content, image_blocks, config, ocr_model)
        except Exception as e:
            logger.warning("Image OCR stage failed (%s); falling back to combined call", e)
            ocr_text = None

    if image_blocks and ocr_text is not None:
        # Stage 2: text-only analysis on the main model, OCR text injected.
        user_text = build_user_prompt(
            content, transcript, preferences_hint, existing_folders, image_text=ocr_text
        )
        user_content: str | list = user_text
        image_blocks_for_analysis: list[dict] | None = None
    else:
        user_text = build_user_prompt(content, transcript, preferences_hint, existing_folders)
        if image_blocks:
            n = len(image_blocks)
            vision_note = (
                f"\n\nI am also providing {n} image(s)/video frame(s) from this content. "
                f"Please read and incorporate any visible text, on-screen captions, titles, "
                f"labels, or other relevant visual information in your analysis."
            )
            user_content = image_blocks + [{"type": "text", "text": user_text + vision_note}]
        else:
            user_content = user_text
        image_blocks_for_analysis = image_blocks

    def _finalize(result: dict) -> dict:
        # When OCR ran on a separate model, the analysis model never saw the images, so
        # ensure the slide text is preserved on the result regardless of what it returned.
        if ocr_text and not (result.get("image_text") or "").strip():
            result["image_text"] = ocr_text
        return result

    raw = _call(client, SYSTEM_PROMPT, user_content, config)
    try:
        return _finalize(json.loads(raw))
    except json.JSONDecodeError:
        logger.warning("Claude returned invalid JSON; retrying")
        retry_text = user_text + "\n\nIMPORTANT: Return ONLY valid JSON. No markdown fences."
        if image_blocks_for_analysis:
            retry_content: str | list = image_blocks_for_analysis + [{"type": "text", "text": retry_text}]
        else:
            retry_content = retry_text
        raw2 = _call(client, SYSTEM_PROMPT, retry_content, config)
        try:
            return _finalize(json.loads(raw2))
        except json.JSONDecodeError:
            logger.error("Claude returned invalid JSON on retry; using fallback")
            return _finalize({
                "folder_path": "SAVES/_UNSORTED",
                "filename": _safe_filename(content.title or content.url),
                "title": content.title or content.url,
                "tags": [content.platform],
                "summary": content.body_text[:200] if content.body_text else "",
                "key_takeaways": [],
                "note_type": "web_generic",
                "topics": [],
            })


def _ocr_images_sync(
    client: anthropic.Anthropic,
    content: ExtractedContent,
    image_blocks: list[dict],
    config: dict,
    ocr_model: str,
) -> str:
    """Stage 1 of two-stage analysis: a cheap vision model transcribes the image slides
    to plain text. Returns the transcribed text (may be empty if nothing to read)."""
    ocr_prompt = build_image_ocr_prompt(content)
    user_content = image_blocks + [{"type": "text", "text": ocr_prompt}]
    # Generous token budget — long carousels can produce a lot of text.
    ocr_cfg = {
        **config,
        "ai": {**config.get("ai", {}), "model": ocr_model, "max_tokens": 8192},
    }
    return _call(client, IMAGE_OCR_SYSTEM_PROMPT, user_content, ocr_cfg).strip()


async def nl_edit(current_state: dict, instruction: str, config: dict) -> dict:
    import asyncio
    return await asyncio.to_thread(_nl_edit_sync, current_state, instruction, config)


def _nl_edit_sync(current_state: dict, instruction: str, config: dict) -> dict:
    client = _make_client(config)
    user_prompt = build_nl_edit_prompt(current_state, instruction)
    raw = _call(client, NL_EDIT_SYSTEM_PROMPT, user_prompt, config)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"action": "cancel"}


async def fact_check(
    content: ExtractedContent,
    ai_result: dict,
    config: dict,
    image_blocks: list[dict] | None = None,
) -> dict | None:
    fc_cfg = config.get("fact_checking", {})
    if not fc_cfg.get("enabled", True):
        return None
    checkable = set(fc_cfg.get("topics", ["health", "political", "finance"]))
    topics = ai_result.get("topics", [])
    if not any(t in checkable for t in topics):
        return None
    import asyncio
    return await asyncio.to_thread(
        _fact_check_sync, content, ai_result, config, image_blocks
    )


def _fact_check_sync(
    content: ExtractedContent,
    ai_result: dict,
    config: dict,
    image_blocks: list[dict] | None = None,
) -> dict | None:
    client = _make_client(config)
    fc_cfg = config.get("fact_checking", {})
    jurisdiction = fc_cfg.get("jurisdiction")

    # Allow a cheaper model for fact-checking (e.g. claude-haiku-4-5) while
    # the main analysis uses a more capable model. Override ai.model locally.
    fc_model = fc_cfg.get("model")
    if fc_model:
        config = {**config, "ai": {**config.get("ai", {}), "model": fc_model}}
    user_prompt = build_fact_check_prompt(content, ai_result, jurisdiction)

    # When OCR already extracted image content as text (image_text populated), sending the
    # raw pixels to the fact-checker doubles the same visual content — once as OCR text in the
    # prompt, once as image tokens. Skip images if OCR already ran; still allow them when
    # include_images is explicitly enabled AND no OCR text is available (e.g. photo-only posts).
    ocr_extracted = bool((ai_result.get("image_text") or "").strip())
    imgs = image_blocks if (image_blocks and fc_cfg.get("include_images", True) and not ocr_extracted) else None

    # Web search is only valuable for topics that need active claim verification.
    # For political/travel/cross-cutting the content itself + comments are sufficient.
    use_web_search = fc_cfg.get("web_search", True)
    if use_web_search:
        web_search_topics = set(fc_cfg.get("web_search_topics", []))
        if web_search_topics:
            post_topics = set(ai_result.get("topics", []))
            if not post_topics.intersection(web_search_topics):
                use_web_search = False
                logger.debug("Web search skipped — no topics in web_search_topics")

    # Recipes/food content frequently trip the health topic on macro/nutrition mentions
    # ("52g protein"), but web-searching those claims is slow (minutes) and low-value. Still
    # run the fact-check pass (so genuine dosage/safety issues — raw egg, undercooked meat,
    # unsafe substitutions — can still surface) but skip the web-search loop for them.
    if use_web_search:
        note_type = ai_result.get("note_type", "")
        is_recipe = (
            note_type in ("web_recipe", "recipe")
            or bool(ai_result.get("recipe_ingredients") or ai_result.get("recipe_instructions"))
            or "cooking" in (ai_result.get("topics") or [])
        )
        if is_recipe:
            use_web_search = False
            logger.info("  Fact-check: recipe/food content — skipping web search (quick local pass only)")

    if use_web_search:
        try:
            raw, harvested = _factcheck_with_web_search(client, user_prompt, config, imgs)
        except Exception as e:
            logger.warning("Web-search fact-check failed (%s); falling back to no-search", e)
            raw = _call(client, FACT_CHECK_SYSTEM_PROMPT, _with_images(user_prompt, imgs), config)
            harvested = []
    else:
        raw = _call(client, FACT_CHECK_SYSTEM_PROMPT, _with_images(user_prompt, imgs), config)
        harvested = []

    parsed = _loads_lenient(raw)
    if parsed is None:
        logger.warning("Fact-check returned non-JSON; could not parse result")
        return None

    # Ensure any URLs Claude actually visited are present in the sources list, even if
    # it forgot to copy them into the JSON.
    if harvested:
        existing = set(parsed.get("sources") or [])
        merged = list(parsed.get("sources") or [])
        for url in harvested:
            if url not in existing:
                merged.append(url)
                existing.add(url)
        parsed["sources"] = merged
    return parsed


def _with_images(user_prompt: str, image_blocks: list[dict] | None):
    """Build a message-content value: images first, then the text prompt (or bare text)."""
    if image_blocks:
        return image_blocks + [{"type": "text", "text": user_prompt}]
    return user_prompt


def _factcheck_with_web_search(
    client: anthropic.Anthropic,
    user_prompt: str,
    config: dict,
    image_blocks: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """Run the fact-check with the server-side web_search tool enabled.

    Claude issues searches on Anthropic's infrastructure; we run the request, follow any
    `pause_turn` continuations, accumulate the text it produces, and harvest the URLs of
    every search result it saw so they can back-fill the sources list. Any image_blocks are
    attached so Claude can assess media authenticity. Returns (concatenated_text,
    harvested_urls)."""
    ai_cfg = config.get("ai", {})
    fc_cfg = config.get("fact_checking", {})
    model = ai_cfg.get("model", "claude-opus-4-8")
    max_tokens = fc_cfg.get("max_tokens", ai_cfg.get("max_tokens", 6000))
    max_searches = fc_cfg.get("max_searches", 5)

    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]

    # Prompt caching: the system prompt + the post's images + the (static) user prompt
    # are identical on every pause_turn continuation. Without caching, each loop iteration
    # re-bills all of it at full price — and the images alone are ~1.7K tokens each. We mark
    # the system prompt and the tail of the first user message as cache breakpoints so every
    # continuation reads that prefix from cache (~90% cheaper) instead of re-billing it.
    system_param = [
        {"type": "text", "text": FACT_CHECK_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
    ]
    first_content = _with_images(user_prompt, image_blocks)
    if isinstance(first_content, list):
        # Copy blocks so we never mutate the shared image_blocks list, then put the cache
        # breakpoint on the final block (caches system + all images + prompt text).
        first_content = [dict(b) for b in first_content]
        first_content[-1] = {**first_content[-1], "cache_control": {"type": "ephemeral"}}
    else:
        first_content = [
            {"type": "text", "text": first_content, "cache_control": {"type": "ephemeral"}}
        ]
    messages: list = [{"role": "user", "content": first_content}]
    text_parts: list[str] = []
    harvested: list[str] = []
    seen_urls: set[str] = set()

    # Follow pause_turn continuations (server-side tool loop hit its iteration cap).
    max_rounds = 6
    for i in range(max_rounds):
        logger.info(
            "  Fact-check: web-search round %d/%d (up to %d searches; this pass is slow "
            "and silent while Claude searches)%s",
            i + 1, max_rounds, max_searches,
            f" — {len(harvested)} source(s) so far" if harvested else "",
        )
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_param,
            tools=tools,
            messages=messages,
        )
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "web_search_tool_result":
                for result in (getattr(block, "content", None) or []):
                    url = getattr(result, "url", None)
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        harvested.append(url)
        if msg.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": msg.content})
            continue
        break

    return "\n".join(text_parts), harvested


def _loads_lenient(raw: str):
    """Parse JSON that may be wrapped in ```json fences or surrounded by prose."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        text = text.lstrip("json").lstrip()
    # Fall back to the first {...} object in the text
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _safe_filename(text: str) -> str:
    import re
    s = re.sub(r'[^\w\s-]', '', text.lower())
    s = re.sub(r'[\s_]+', '-', s).strip('-')
    return s[:60] or "untitled"
