import asyncio
import json
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
        msg = client.messages.create(
            model=ai_cfg.get("model", "claude-opus-4-8"),
            max_tokens=512,
            temperature=0,
            system=TRAVEL_LOCATION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw = msg.content[0].text
        result = json.loads(raw)
        if result.get("location_disputed"):
            return result
        return None
    except Exception as e:
        logger.warning(f"Travel location check failed (non-fatal): {e}")
        return None
