import asyncio
import logging
import os

logger = logging.getLogger(__name__)


async def check_travel_location(content, ai_result: dict, config: dict) -> dict | None:
    """
    Returns None if not applicable or disabled, else a location check result dict.
    Only called when 'travel' is in ai_result['topics'] or note_type contains 'travel'.
    """
    tv_cfg = config.get("travel_verification", {})
    if not tv_cfg.get("enabled", True):
        return None

    topics = ai_result.get("topics", [])
    note_type = ai_result.get("note_type", "")
    if "travel" not in topics and "travel" not in note_type:
        return None

    # Only meaningful if we have body text or comments to scan
    has_content = bool(content.body_text or content.top_comments)
    if not has_content:
        return None

    return await asyncio.to_thread(_location_check_sync, content, ai_result, config)


def _location_check_sync(content, ai_result: dict, config: dict) -> dict | None:
    import anthropic

    from src.ai.prompts import TRAVEL_LOCATION_SYSTEM_PROMPT, build_travel_location_prompt

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    ai_cfg = config.get("ai", {})

    try:
        user_prompt = build_travel_location_prompt(content)
        # NB: do NOT pass `temperature` — opus-4-8 rejects it (400), which previously
        # made every travel check fail silently and return None.
        msg = client.messages.create(
            model=ai_cfg.get("model", "claude-opus-4-8"),
            max_tokens=1024,
            system=TRAVEL_LOCATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        # First block isn't guaranteed to be text (thinking block / empty refusal content);
        # pick the first text block. An empty raw falls through to json.loads → caught by
        # the except below → clean non-fatal None, matching this check's design.
        raw = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), "")
        # Lenient: tolerate ```json fences / surrounding prose — a raw json.loads here
        # failed with "Expecting value: line 1 column 1" whenever the model fenced its
        # answer, silently skipping the location check.
        from src.ai.claude_client import _loads_lenient

        result = _loads_lenient(raw)
        if result is None:
            raise ValueError(f"unparseable location-check response: {raw[:200]!r}")
        # Surface the result when there's a location dispute OR any advisory worth showing.
        if result.get("location_disputed") or result.get("advisories"):
            return result
        return None
    except Exception as e:
        logger.warning(f"Travel location check failed (non-fatal): {e}")
        return None
