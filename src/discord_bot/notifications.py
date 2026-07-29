import asyncio
import logging

import discord

from src.notes.file_manager import retire_note_to_bak
from src.utils.url_parser import normalize_url

logger = logging.getLogger(__name__)


def _get_channel(bot: discord.Client, channel_name: str) -> discord.TextChannel | None:
    # Case-insensitive: Discord force-lowercases text channel names ("SAVES-approvals"
    # becomes "saves-approvals"), so an exact match against the configured name never hits.
    wanted = channel_name.lower()
    for guild in bot.guilds:
        for ch in guild.text_channels:
            if ch.name.lower() == wanted:
                return ch
    return None


def _chunk_tags(tags: list[str], limit: int = 1024) -> list[str]:
    """Join `#tag` tokens into space-separated strings, each <= limit chars (Discord's field
    value cap), so a long tag list (every recipe ingredient) is shown in full across multiple
    fields rather than truncated. A single token longer than limit is hard-truncated."""
    chunks: list[str] = []
    cur = ""
    for tag in tags:
        if not cur:
            cur = tag[:limit]
        elif len(cur) + 1 + len(tag) <= limit:
            cur += " " + tag
        else:
            chunks.append(cur)
            cur = tag[:limit]
    if cur:
        chunks.append(cur)
    return chunks


def build_approval_embed(pending) -> discord.Embed:
    """Build the approval embed for a pending item. Extracted so it can be re-rendered when
    the on-demand deep fact-check completes and populates `_fact_check` with fresh results."""
    ai = pending.ai_result
    summary = ai.get("summary", "")[:300]

    embed = discord.Embed(
        title="📎 New Save Ready",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Title", value=ai.get("title", "Untitled")[:256], inline=False)
    embed.add_field(name="From", value=f"{pending.platform} — {pending.content_summary.get('author') or 'unknown'}", inline=True)
    embed.add_field(name="Path", value=ai.get("folder_path", "SAVES/")[:256], inline=True)
    embed.add_field(name="Type", value=ai.get("note_type", "?"), inline=True)
    # Show EVERY tag so the user approves the complete set (recipes carry every ingredient as a
    # tag, so the list is long). Discord caps a single field value at 1024 chars, so chunk the
    # tags across as many "Tags" fields as needed instead of truncating.
    tags = [f"#{t}" for t in (ai.get("tags") or [])]
    for i, chunk in enumerate(_chunk_tags(tags, limit=1024)):
        embed.add_field(name="Tags" if i == 0 else "Tags (cont.)", value=chunk, inline=False)
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
            # Discord's field cap is 1024 chars; the old 3×80-char slice cut findings
            # mid-sentence. Show up to 5 claims at readable length and say how many more.
            claims = fc["disputed_claims"]
            dispute_lines = [f"• {c.get('claim', '')[:180]}" for c in claims[:5]]
            if len(claims) > 5:
                dispute_lines.append(f"…and {len(claims) - 5} more — 🔍 Deep fact-check for the full list.")
            embed.add_field(
                name=f"⚠️ Disputed Claims ({len(claims)})",
                value="\n".join(dispute_lines)[:1024],
                inline=False,
            )
        # Cross-cutting flags (media authenticity, conflict of interest, scam, tax, etc.)
        warn_flags = [
            f for f in (fc.get("flags") or [])
            if isinstance(f, dict) and f.get("severity") == "warning"
        ]
        if warn_flags:
            flag_lines = [
                f"• **{f.get('type', 'flag').replace('_', ' ').title()}:** {f.get('detail', '')[:180]}"
                for f in warn_flags[:5]
            ]
            if len(warn_flags) > 5:
                flag_lines.append(f"…and {len(warn_flags) - 5} more.")
            embed.add_field(
                name=f"⚠️ Flags ({len(warn_flags)})",
                value="\n".join(flag_lines)[:1024],
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
            value=f"Stated: **{stated}** → Claimed actual: **{actual}**\n{lc.get('evidence', '')[:700]}"[:1024],
            inline=False,
        )
    if lc and lc.get("advisories"):
        adv_lines = [
            f"• **{a.get('type', 'advisory').replace('_', ' ').title()}:** {a.get('detail', '')[:180]}"
            for a in lc["advisories"][:5] if isinstance(a, dict)
        ]
        if adv_lines:
            embed.add_field(
                name="⚠️ Travel Advisories",
                value="\n".join(adv_lines)[:1024],
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


class DuplicateNoticeView(discord.ui.View):
    """Buttons on the duplicate notice: 🔁 Re-save (forget the URL, retire the old note to
    `.bak`, and requeue it through the normal pipeline — new approval card and all) or
    ✖ Dismiss. In-process only: after a bot restart the buttons on old notices go dead —
    the fallback is `/forget` + re-pasting the URL, which does the same thing.

    The old note is RENAMED to `<name>.md.bak`, never deleted (zero-delete policy), so the
    fresh save can take the original filename while the previous version stays recoverable.
    """

    def __init__(self, url: str, existing_path: str | None):
        super().__init__(timeout=None)
        self.url = url
        self.existing_path = existing_path

    async def _finish(self, interaction: discord.Interaction, extra: str) -> None:
        for child in self.children:
            child.disabled = True
        content = (interaction.message.content or "") + f"\n{extra}"
        await interaction.response.edit_message(content=content[:2000], view=self)

    @discord.ui.button(label="🔁 Re-save", style=discord.ButtonStyle.primary)
    async def resave(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot = interaction.client  # SAVESBot: has .state and .queue_manager
        target = normalize_url(self.url)
        # 1. Retire the old note (rename to .bak — zero-delete) so the new save can take
        #    the original filename instead of getting a "-2" suffix.
        retired = None
        if self.existing_path:
            try:
                retired = await asyncio.to_thread(retire_note_to_bak, self.existing_path)
            except OSError as e:
                logger.warning("Could not retire old note %s: %s", self.existing_path, e)
        # 2. Forget: drop the state entry + session dedup sets (same as /forget).
        if getattr(bot, "state", None) is not None:
            if not bot.state.forget(target) and self.url != target:
                bot.state.forget(self.url)
        queued = False
        if getattr(bot, "queue_manager", None) is not None:
            bot.queue_manager.forget(target)
            # 3. Requeue directly — no need to re-paste into the inbox.
            queued = await bot.queue_manager.enqueue_url(self.url)
        parts = ["🔁 **Re-saving** — forgotten and requeued; a new approval card is coming."]
        if retired:
            parts.append(f"Old note retired to `{retired}` (renamed, not deleted).")
        if not queued:
            parts.append(
                "⚠️ Couldn't requeue automatically — paste the URL into the inbox to reprocess."
            )
        await self._finish(interaction, "\n".join(parts))

    @discord.ui.button(label="✖ Dismiss", style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, "Dismissed — kept the existing note.")


async def send_duplicate_notice(
    bot: discord.Client, channel_name: str, url: str, existing_path: str | None
) -> None:
    """Tell the user a pasted URL was already saved (skipped before any extraction/AI tokens
    were spent) and offer a 🔁 Re-save button to reprocess it anyway."""
    lines = [
        "🔁 **Duplicate — already saved**",
        f"`{url}`",
    ]
    if existing_path:
        lines.append(f"Existing note: `{existing_path}`")
    lines.append("Skipped before processing — no tokens spent. Re-save to process it again.")
    channel = _get_channel(bot, channel_name)
    if channel:
        await channel.send("\n".join(lines), view=DuplicateNoticeView(url, existing_path))
    else:
        logger.warning("Duplicate notice (channel not found): %s", url)


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
