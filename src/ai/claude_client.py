import json
import logging
import os

import anthropic

from src.ai.prompts import (
    SYSTEM_PROMPT, build_user_prompt,
    NL_EDIT_SYSTEM_PROMPT, build_nl_edit_prompt,
    FACT_CHECK_SYSTEM_PROMPT, build_fact_check_prompt,
)
from src.extractors.base import ExtractedContent

logger = logging.getLogger(__name__)


def _make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


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
    params = {
        "model": ai_cfg.get("model", "claude-opus-4-8"),
        "max_tokens": ai_cfg.get("max_tokens", 4096),
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    temperature = ai_cfg.get("temperature")
    if temperature is not None:
        params["temperature"] = temperature

    try:
        msg = client.messages.create(**params)
    except anthropic.BadRequestError as e:
        if "temperature" in str(e) and "temperature" in params:
            logger.info("Model rejects `temperature`; retrying without it")
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
    client = _make_client()
    user_text = build_user_prompt(content, transcript, preferences_hint, existing_folders)

    if image_blocks:
        n = len(image_blocks)
        vision_note = (
            f"\n\nI am also providing {n} image(s)/video frame(s) from this content. "
            f"Please read and incorporate any visible text, on-screen captions, titles, "
            f"labels, or other relevant visual information in your analysis."
        )
        user_content: str | list = image_blocks + [{"type": "text", "text": user_text + vision_note}]
    else:
        user_content = user_text

    raw = _call(client, SYSTEM_PROMPT, user_content, config)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Claude returned invalid JSON; retrying")
        retry_text = user_text + "\n\nIMPORTANT: Return ONLY valid JSON. No markdown fences."
        if image_blocks:
            retry_content: str | list = image_blocks + [{"type": "text", "text": retry_text}]
        else:
            retry_content = retry_text
        raw2 = _call(client, SYSTEM_PROMPT, retry_content, config)
        try:
            return json.loads(raw2)
        except json.JSONDecodeError:
            logger.error("Claude returned invalid JSON on retry; using fallback")
            return {
                "folder_path": "SAVES/_UNSORTED",
                "filename": _safe_filename(content.title or content.url),
                "title": content.title or content.url,
                "tags": [content.platform],
                "summary": content.body_text[:200] if content.body_text else "",
                "key_takeaways": [],
                "note_type": "web_generic",
                "topics": [],
            }


async def nl_edit(current_state: dict, instruction: str, config: dict) -> dict:
    import asyncio
    return await asyncio.to_thread(_nl_edit_sync, current_state, instruction, config)


def _nl_edit_sync(current_state: dict, instruction: str, config: dict) -> dict:
    client = _make_client()
    user_prompt = build_nl_edit_prompt(current_state, instruction)
    raw = _call(client, NL_EDIT_SYSTEM_PROMPT, user_prompt, config)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"action": "cancel"}


async def fact_check(content: ExtractedContent, ai_result: dict, config: dict) -> dict | None:
    fc_cfg = config.get("fact_checking", {})
    if not fc_cfg.get("enabled", True):
        return None
    checkable = set(fc_cfg.get("topics", ["health", "political", "finance"]))
    topics = ai_result.get("topics", [])
    if not any(t in checkable for t in topics):
        return None
    import asyncio
    return await asyncio.to_thread(_fact_check_sync, content, ai_result, config)


def _fact_check_sync(content: ExtractedContent, ai_result: dict, config: dict) -> dict | None:
    client = _make_client()
    user_prompt = build_fact_check_prompt(content, ai_result)
    raw = _call(client, FACT_CHECK_SYSTEM_PROMPT, user_prompt, config)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _safe_filename(text: str) -> str:
    import re
    s = re.sub(r'[^\w\s-]', '', text.lower())
    s = re.sub(r'[\s_]+', '-', s).strip('-')
    return s[:60] or "untitled"
