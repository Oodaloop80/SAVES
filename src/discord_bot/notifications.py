import logging

import discord

logger = logging.getLogger(__name__)


def _get_channel(bot: discord.Client, channel_name: str) -> discord.TextChannel | None:
    for guild in bot.guilds:
        for ch in guild.text_channels:
            if ch.name == channel_name:
                return ch
    return None


def build_approval_embed(pending) -> discord.Embed:
    """Build the approval embed for a pending item. Extracted so it can be re-rendered when
    the on-demand deep fact-check completes and populates `_fact_check` with fresh results."""
    ai = pending.ai_result
    tags_preview = " ".join(f"#{t}" for t in (ai.get("tags") or [])[:8])
    summary = ai.get("summary", "")[:300]

    embed = discord.Embed(
        title="📎 New Save Ready",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Title", value=ai.get("title", "Untitled")[:256], inline=False)
    embed.add_field(name="From", value=f"{pending.platform} — {pending.content_summary.get('author') or 'unknown'}", inline=True)
    embed.add_field(name="Path", value=ai.get("folder_path", "SAVES/")[:256], inline=True)
    embed.add_field(name="Type", value=ai.get("note_type", "?"), inline=True)
    if tags_preview:
        embed.add_field(name="Tags", value=tags_preview[:512], inline=False)
    if summary:
        embed.add_field(name="Summary", value=summary, inline=False)

    # Fact-check flags
    fc = ai.get("_fact_check")
    if fc:
        if fc.get("opinion_only"):
            embed.add_field(
                name="ℹ️ Fact-Check",
                value="Opinion/analysis — no factual claims to verify",
                inline=False,
            )
        elif fc.get("disputed_claims"):
            dispute_lines = []
            for claim in fc["disputed_claims"][:3]:
                dispute_lines.append(f"• {claim.get('claim', '')[:80]}")
            embed.add_field(
                name=f"⚠️ Disputed Claims ({len(fc['disputed_claims'])})",
                value="\n".join(dispute_lines)[:512],
                inline=False,
            )
        # Cross-cutting flags (media authenticity, conflict of interest, scam, tax, etc.)
        warn_flags = [
            f for f in (fc.get("flags") or [])
            if isinstance(f, dict) and f.get("severity") == "warning"
        ]
        if warn_flags:
            flag_lines = [
                f"• **{f.get('type', 'flag').replace('_', ' ').title()}:** {f.get('detail', '')[:80]}"
                for f in warn_flags[:4]
            ]
            embed.add_field(
                name=f"⚠️ Flags ({len(warn_flags)})",
                value="\n".join(flag_lines)[:512],
                inline=False,
            )

    # Location check flags
    lc = ai.get("_location_check")
    if lc and lc.get("location_disputed"):
        stated = lc.get("stated_location", "?")
        actual = lc.get("claimed_actual_location", "?")
        confidence = lc.get("confidence", "?")
        embed.add_field(
            name=f"⚠️ Location Disputed ({confidence} confidence)",
            value=f"Stated: **{stated}** → Claimed actual: **{actual}**\n{lc.get('evidence', '')[:200]}",
            inline=False,
        )
    if lc and lc.get("advisories"):
        adv_lines = [
            f"• **{a.get('type', 'advisory').replace('_', ' ').title()}:** {a.get('detail', '')[:80]}"
            for a in lc["advisories"][:4] if isinstance(a, dict)
        ]
        if adv_lines:
            embed.add_field(
                name="⚠️ Travel Advisories",
                value="\n".join(adv_lines)[:512],
                inline=False,
            )

    # Signal that what's shown is the cheap local pass and web verification is one click away.
    if ai.get("_fact_check") and not ai.get("_deep_fact_check_done"):
        embed.add_field(
            name="🔍 Deep fact-check available",
            value=(
                "The flags above are the quick **local** pass (no web search). Press "
                "**🔍 Deep fact-check** to run web-searched claim verification with sources."
            ),
            inline=False,
        )

    embed.set_footer(text=f"ID: {pending.id[:8]} | {pending.url[:80]}")
    return embed


async def send_approval_request(
    bot: discord.Client,
    channel_name: str,
    pending,  # PendingApproval
    view: discord.ui.View,
) -> int | None:
    channel = _get_channel(bot, channel_name)
    if channel is None:
        logger.error(f"Discord channel #{channel_name} not found")
        return None

    embed = build_approval_embed(pending)
    msg = await channel.send(embed=embed, view=view)
    return msg.id


async def send_log(bot: discord.Client, channel_name: str, message: str) -> None:
    channel = _get_channel(bot, channel_name)
    if channel:
        await channel.send(message)


async def send_alert(bot: discord.Client, channel_name: str, message: str) -> None:
    channel = _get_channel(bot, channel_name)
    if channel:
        await channel.send(f"⚠️ {message}")
    else:
        logger.warning(f"ALERT (channel not found): {message}")


async def send_cookie_warning(
    bot: discord.Client, channel_name: str, warning: dict
) -> None:
    if warning.get("missing"):
        msg = (
            f"⚠️ **Cookie File Missing**\n"
            f"Platform: {warning['platform']}\n"
            f"Expected at: `{warning['cookie_path']}`\n"
            f"Run: `python scripts/refresh_cookies.py {warning['platform']}`"
        )
    else:
        msg = (
            f"⚠️ **Cookie Expiry Warning**\n"
            f"{warning['platform'].title()} cookies last exported: {warning['days_old']} days ago\n"
            f"Expected expiry: ~{warning['expiry_days']} days ({warning['days_remaining']} days remaining)\n"
            f"Run: `python scripts/refresh_cookies.py {warning['platform']}`"
        )
    await send_alert(bot, channel_name, msg)
