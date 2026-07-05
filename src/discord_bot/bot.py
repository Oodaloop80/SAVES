import asyncio
import logging
import re

import discord
from discord.ext import tasks

from src.discord_bot.approval import PendingApproval, PendingApprovalsStore
from src.discord_bot.notifications import (
    _get_channel,
    build_approval_embed,
    send_alert,
    send_approval_request,
    send_cookie_warning,
    send_log,
)
from src.notes.file_manager import write_note
from src.notes.formatter import format_note
from src.utils.cookie_checker import check_all_cookies
from src.utils.file_io import remove_url_from_inbox
from src.utils.preferences import PreferencesStore
from src.utils.tag_index import get_tag_index
from src.utils.url_parser import normalize_url
from src.utils.vault_scanner import clean_folder_path

logger = logging.getLogger(__name__)

# Maps channel_id → pending item ID for active NL edit sessions
_nl_edit_sessions: dict[int, str] = {}


class ApprovalView(discord.ui.View):
    def __init__(self, bot: "SAVESBot", pending_id: str, show_deep_button: bool = True):
        super().__init__(timeout=None)
        self.bot = bot
        self.pending_id = pending_id
        # The deep (web-searched) fact-check button only makes sense for posts with checkable
        # topics that haven't already had it run — drop it otherwise so it isn't dead weight.
        if not show_deep_button:
            for item in list(self.children):
                if getattr(item, "custom_id", None) == "deep_fact_check":
                    self.remove_item(item)

    # Button order (Bora, 2026-07-05): Approve, Add Tags, Remove Tags, Change Path, NL Edit.
    # Decorator definition order = display order. custom_id "edit_tags" is kept on Add Tags
    # so approval cards posted before the rename still route their clicks here after restart
    # (persistent views match components by custom_id, not label).

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        pending = self.bot.store.get_by_id(self.pending_id)
        if not pending:
            await interaction.followup.send("This item has already been processed.", ephemeral=True)
            return
        await self.bot._finalize(pending, interaction, include_warnings=False)

    @discord.ui.button(label="🏷️ Add Tags", style=discord.ButtonStyle.secondary, custom_id="edit_tags")
    async def add_tags(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddTagsModal(self.bot, self.pending_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑️ Remove Tags", style=discord.ButtonStyle.secondary, custom_id="remove_tags")
    async def remove_tags(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = self.bot.store.get_by_id(self.pending_id)
        if not pending:
            await interaction.response.send_message("Already processed.", ephemeral=True)
            return
        tags = pending.ai_result.get("tags") or []
        if not tags:
            await interaction.response.send_message("No tags to remove.", ephemeral=True)
            return
        view = TagRemoveView(self.bot, self.pending_id, tags)
        await interaction.response.send_message(
            "Tap a tag to remove it:",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="📁 Change Path", style=discord.ButtonStyle.secondary, custom_id="change_path")
    async def change_path(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PathModal(self.bot, self.pending_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="✏️ NL Edit", style=discord.ButtonStyle.secondary, custom_id="nl_edit")
    async def nl_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        _nl_edit_sessions[interaction.channel_id] = self.pending_id
        await interaction.response.send_message(
            "NL Edit mode active. Type your instruction naturally.\n"
            "Examples: \"move to travel Caribbean\", \"add tags: points-miles trip-planning\", "
            "\"rename it to American Airlines Card Tips\"",
            ephemeral=True,
        )

    @discord.ui.button(
        label="🔍 Deep fact-check", style=discord.ButtonStyle.primary,
        custom_id="deep_fact_check", row=1,
    )
    async def deep_fact_check(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = self.bot.store.get_by_id(self.pending_id)
        if not pending:
            await interaction.response.send_message("This item has already been processed.", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.edit_original_response(
            content="🔍 Running deep fact-check (web search — this takes ~1–3 min)…"
        )
        try:
            await self.bot._run_deep_fact_check(pending)
        except Exception as e:
            logger.warning("Deep fact-check failed for %s: %s", pending.url, e)
            await interaction.edit_original_response(
                content="⚠️ Deep fact-check failed — you can still approve the note."
            )
            return
        # Re-render with the web-searched results: new embed + refreshed view (warning variant
        # now that flags are present, and the deep-check button dropped since it's done).
        embed = build_approval_embed(pending)
        new_view = self.bot._build_view(pending)
        await interaction.edit_original_response(
            content="✅ Deep fact-check complete — review the findings below.",
            embed=embed, view=new_view,
        )


class TagRemoveView(discord.ui.View):
    """Ephemeral view with one ✖ button per tag — tap a tag to remove it instantly.

    Replaced the earlier multi-select dropdown: one tap per unwanted tag beats
    open-dropdown → tick → confirm, especially on mobile. The view re-renders with the
    removed button gone after each tap. Discord caps a view at 25 components (5 rows × 5),
    so 24 tag buttons + Done; generated tags stay far below that."""

    def __init__(self, bot: "SAVESBot", pending_id: str, tags: list[str]):
        super().__init__(timeout=300)
        self.bot = bot
        self.pending_id = pending_id
        for tag in tags[:24]:
            self.add_item(self._make_tag_button(tag))
        done = discord.ui.Button(label="Done", style=discord.ButtonStyle.primary)
        done.callback = self._on_done
        self.add_item(done)

    def _make_tag_button(self, tag: str) -> discord.ui.Button:
        btn = discord.ui.Button(label=f"✖ {tag}"[:80], style=discord.ButtonStyle.secondary)

        async def _remove(interaction: discord.Interaction, tag: str = tag):
            pending = self.bot.store.get_by_id(self.pending_id)
            if not pending:
                await interaction.response.edit_message(content="Already processed.", view=None)
                return
            remaining = [t for t in (pending.ai_result.get("tags") or []) if t != tag]
            pending.ai_result["tags"] = remaining
            self.bot.store.update(pending)
            if remaining:
                await interaction.response.edit_message(
                    content=f"Removed `{tag}` — tap more to remove, or **Done**.",
                    view=TagRemoveView(self.bot, self.pending_id, remaining),
                )
            else:
                await interaction.response.edit_message(
                    content=f"Removed `{tag}`. No tags left.\nUse ✅ Approve when ready.",
                    view=None,
                )
            await self.bot._refresh_card(pending)

        btn.callback = _remove
        return btn

    async def _on_done(self, interaction: discord.Interaction):
        pending = self.bot.store.get_by_id(self.pending_id)
        tags = (pending.ai_result.get("tags") or []) if pending else []
        remaining = "  ".join(f"`{t}`" for t in tags)
        await interaction.response.edit_message(
            content=f"**Tags:** {remaining or '*(none)*'}\nUse ✅ Approve when ready.",
            view=None,
        )


class TagSwapView(discord.ui.View):
    """Offered when a typed tag is a near-duplicate of an existing vault tag
    (airfryer vs air-fryer) — one tap swaps to the established tag so the
    vault taxonomy doesn't fork."""

    def __init__(self, bot: "SAVESBot", pending_id: str, pairs: list[tuple[str, str]]):
        super().__init__(timeout=300)
        self.bot = bot
        self.pending_id = pending_id
        self.pairs = pairs[:5]
        for typed, existing in self.pairs:
            self.add_item(self._make_swap_button(typed, existing))

    def _make_swap_button(self, typed: str, existing: str) -> discord.ui.Button:
        btn = discord.ui.Button(
            label=f"Use {existing} (not {typed})"[:80],
            style=discord.ButtonStyle.secondary,
        )

        async def _swap(interaction: discord.Interaction, typed: str = typed, existing: str = existing):
            pending = self.bot.store.get_by_id(self.pending_id)
            if not pending:
                await interaction.response.edit_message(content="Already processed.", view=None)
                return
            tags, seen = [], set()
            for t in pending.ai_result.get("tags") or []:
                t = existing if t == typed else t
                if t not in seen:
                    seen.add(t)
                    tags.append(t)
            pending.ai_result["tags"] = tags
            self.bot.store.update(pending)
            rest = [p for p in self.pairs if p != (typed, existing)]
            preview = "  ".join(f"`{t}`" for t in tags)
            await interaction.response.edit_message(
                content=f"✅ Swapped `{typed}` → `{existing}`.\n**Tags:** {preview}",
                view=TagSwapView(self.bot, self.pending_id, rest) if rest else None,
            )
            await self.bot._refresh_card(pending)

        btn.callback = _swap
        return btn


class ApprovalViewWithWarning(ApprovalView):
    """Shown when fact-check or location flags are present — adds ⚠️ Include Warning button."""

    @discord.ui.button(
        label="⚠️ Approve + Include Warning",
        style=discord.ButtonStyle.danger,
        custom_id="approve_with_warning",
    )
    async def approve_with_warning(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        pending = self.bot.store.get_by_id(self.pending_id)
        if not pending:
            await interaction.followup.send("This item has already been processed.", ephemeral=True)
            return
        await self.bot._finalize(pending, interaction, include_warnings=True)


class PathModal(discord.ui.Modal, title="Change Path"):
    new_path = discord.ui.TextInput(
        label="New folder path (auto-uppercased)",
        placeholder="SAVES/COOKING/SMOKING",
        style=discord.TextStyle.short,
    )

    def __init__(self, bot: "SAVESBot", pending_id: str):
        super().__init__()
        self.bot = bot
        self.pending_id = pending_id
        # Prepopulate with the current path so a tweak is an edit, not a full retype.
        pending = bot.store.get_by_id(pending_id)
        if pending:
            self.new_path.default = pending.ai_result.get("folder_path") or ""

    async def on_submit(self, interaction: discord.Interaction):
        pending = self.bot.store.get_by_id(self.pending_id)
        if not pending:
            await interaction.response.send_message("Item already processed.", ephemeral=True)
            return
        pending.ai_result["folder_path"] = clean_folder_path(self.new_path.value)
        self.bot.store.update(pending)
        await interaction.response.send_message(
            f"✅ Path updated to `{pending.ai_result['folder_path']}` — card updated.",
            ephemeral=True,
        )
        await self.bot._refresh_card(pending)


def _parse_tags_to_add(raw: str) -> tuple[list[str], list[str]]:
    """Parse the Add-Tags input: space/comma-separated tags, no prefix needed
    (a leading + or # from old habit is tolerated and stripped). Returns
    (tags_to_add, skipped_removal_tokens) — `-tag` tokens are NOT removals here
    (that moved to the 🗑️ Remove Tags button) and are skipped so a stray old-syntax
    removal can't be silently added as a literal `-tag`."""
    to_add: list[str] = []
    skipped: list[str] = []
    for token in re.split(r"[,\s]+", raw or ""):
        token = token.strip()
        if not token:
            continue
        if token.startswith("-"):
            skipped.append(token)
            continue
        t = token.lstrip("+#").strip()
        if t and t not in to_add:
            to_add.append(t)
    return to_add, skipped


def _compute_swap_pairs(idx, added: list[str]) -> list[tuple[str, str]]:
    """For each just-added tag, the single closest existing vault tag (if any) — the
    near-duplicate swap suggestions offered after an Add-Tags submit. Calls
    `close_matches`, which may trigger a full-vault rescan, so this must run in a worker
    thread (asyncio.to_thread) off the event loop, not inline in the modal handler."""
    pairs: list[tuple[str, str]] = []
    for t in added:
        for cand in idx.close_matches(t):
            pairs.append((t, cand))
            break  # top suggestion per typed tag is enough
    return pairs


class AddTagsModal(discord.ui.Modal, title="Add Tags"):
    new_tags = discord.ui.TextInput(
        label="Tags to add (space or comma separated)",
        placeholder="bbq weekend-project — tip: /tag add autocompletes existing tags",
        style=discord.TextStyle.short,
    )

    def __init__(self, bot: "SAVESBot", pending_id: str):
        super().__init__()
        self.bot = bot
        self.pending_id = pending_id

    async def on_submit(self, interaction: discord.Interaction):
        pending = self.bot.store.get_by_id(self.pending_id)
        if not pending:
            await interaction.response.send_message("Item already processed.", ephemeral=True)
            return
        tags = list(pending.ai_result.get("tags") or [])
        to_add, skipped_removals = _parse_tags_to_add(self.new_tags.value)
        added = [t for t in to_add if t not in tags]
        tags.extend(added)
        pending.ai_result["tags"] = tags
        self.bot.store.update(pending)

        # Guard against taxonomy forks: if a typed tag is a near-duplicate of an existing
        # vault tag (airfryer vs air-fryer), offer a one-tap swap to the established one.
        # close_matches() can trigger a full-vault rescan, so run the whole check in a worker
        # thread — otherwise a large vault would stall the modal response on the event loop.
        swap_pairs: list[tuple[str, str]] = []
        try:
            swap_pairs = await asyncio.to_thread(
                _compute_swap_pairs, get_tag_index(self.bot.config), added
            )
        except Exception as e:
            logger.debug("Tag near-duplicate check skipped: %s", e)

        preview = " ".join(f"#{t}" for t in tags)
        msg = f"✅ Tags: {preview}\nCard updated — use ✅ Approve when ready."
        if skipped_removals:
            skipped = " ".join(f"`{t}`" for t in skipped_removals)
            msg += f"\nℹ️ Ignored {skipped} — removing tags moved to the 🗑️ Remove Tags button."
        if swap_pairs:
            hints = "\n".join(
                f"⚠️ `{typed}` is close to existing `{cand}` "
                f"({get_tag_index(self.bot.config).count(cand)} notes use it)"
                for typed, cand in swap_pairs
            )
            await interaction.response.send_message(
                f"{msg}\n\n{hints}",
                view=TagSwapView(self.bot, self.pending_id, swap_pairs),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(msg, ephemeral=True)
        await self.bot._refresh_card(pending)


class SAVESBot(discord.Client):
    def __init__(self, config: dict, prefs: PreferencesStore, state=None):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.prefs = prefs
        self.state = state
        self.tree = discord.app_commands.CommandTree(self)
        paths = config.get("paths", {})
        self.store = PendingApprovalsStore(paths.get("pending_approvals_file", "pending_approvals.json"))
        self._discord_cfg = config.get("discord", {})
        self._commands_synced = False
        # Wired by main.py after construction; lets /forget clear the session-local
        # enqueue-dedup sets so a forgotten URL re-pasted in the same process re-queues.
        self.queue_manager = None
        self._register_commands()

    # ---- /tag slash commands -------------------------------------------------------

    def _newest_pending(self) -> PendingApproval | None:
        items = self.store.get_all()
        return max(items, key=lambda p: p.created_at) if items else None

    async def _tag_choices(self, current: str) -> list[discord.app_commands.Choice[str]]:
        """Autocomplete choices for /tag add: existing vault tags matching what's typed,
        most-used first, with usage counts. Empty input → the top tags.

        `search()` can trigger a full-vault rescan (TTL expiry), which on a large vault would
        freeze the whole bot if run on the event loop — so it's dispatched to a worker thread.
        Discord gives autocomplete a 3s deadline; threading keeps the loop responsive within it."""
        try:
            matches = await asyncio.to_thread(get_tag_index(self.config).search, current, 25)
        except Exception as e:
            logger.warning("Tag autocomplete failed: %s", e)
            return []
        return [
            discord.app_commands.Choice(name=f"{t} ({c})"[:100], value=t[:100])
            for t, c in matches
        ]

    def _pending_choices(self, current: str) -> list[discord.app_commands.Choice[str]]:
        """Autocomplete choices for /tag add's optional item param: pending saves by
        title, newest first."""
        items = sorted(self.store.get_all(), key=lambda p: p.created_at, reverse=True)
        cur = (current or "").lower()
        out: list[discord.app_commands.Choice[str]] = []
        for p in items:
            title = p.ai_result.get("title") or p.url
            if cur and cur not in title.lower():
                continue
            out.append(discord.app_commands.Choice(name=title[:100], value=p.id))
            if len(out) == 25:
                break
        return out

    async def _tag_add_impl(self, interaction: discord.Interaction, tag: str, item: str | None):
        pending = self.store.get_by_id(item) if item else self._newest_pending()
        if not pending:
            await interaction.response.send_message("No pending saves.", ephemeral=True)
            return
        t = tag.strip().lstrip("#")
        if not t:
            await interaction.response.send_message("Empty tag.", ephemeral=True)
            return
        tags = list(pending.ai_result.get("tags") or [])
        changed = t not in tags
        if changed:
            tags.append(t)
            pending.ai_result["tags"] = tags
            self.store.update(pending)
        preview = " ".join(f"#{x}" for x in tags)
        title = (pending.ai_result.get("title") or pending.url)[:80]
        await interaction.response.send_message(
            f"✅ Added `{t}` to **{title}**\nTags: {preview}\n"
            f"Card updated — use ✅ Approve when ready.",
            ephemeral=True,
        )
        if changed:
            await self._refresh_card(pending)

    def _forget_choices(self, current: str) -> list[discord.app_commands.Choice[str]]:
        """Autocomplete for /forget: URLs from the processing state, newest first. Offers
        'done' (the duplicate-notice case) and 'failed_permanent' (retry a dead extract)
        entries; other statuses re-run by themselves. URLs longer than Discord's 100-char
        Choice value limit are skipped — paste those in full instead."""
        if self.state is None:
            return []
        cur = (current or "").lower()
        entries = sorted(
            self.state.entries().items(),
            key=lambda kv: kv[1].get("timestamp", 0),
            reverse=True,
        )
        out: list[discord.app_commands.Choice[str]] = []
        for url, entry in entries:
            status = entry.get("status")
            if status not in ("done", "failed_permanent") or len(url) > 100:
                continue
            if cur and cur not in url.lower():
                continue
            name = url if status == "done" else f"[failed] {url}"
            out.append(discord.app_commands.Choice(name=name[:100], value=url))
            if len(out) == 25:
                break
        return out

    async def _forget_impl(self, interaction: discord.Interaction, url: str):
        if self.state is None:
            await interaction.response.send_message(
                "State tracking isn't available in this process.", ephemeral=True
            )
            return
        raw = url.strip()
        target = normalize_url(raw)
        # State keys are normalized URLs, but tolerate an entry stored under the raw
        # form (e.g. from an older run before enqueue normalization).
        existed = self.state.forget(target)
        if not existed and raw != target:
            existed = self.state.forget(raw)
        if self.queue_manager is not None:
            self.queue_manager.forget(target)
        if existed:
            await interaction.response.send_message(
                f"🧹 Forgot `{target}` — paste it into the inbox to reprocess it.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"Nothing in the saved history matches `{target}`.", ephemeral=True
            )

    def _register_commands(self) -> None:
        """Slash commands. /tag add — the only Discord surface with real search-as-you-type:
        slash-command option autocomplete. (Modal text inputs cannot autocomplete — a Discord
        platform limitation — so the Add-Tags modal gets a submit-time near-duplicate check
        instead.) /forget — drop a URL from the processing state so it can be saved again;
        deleting the note in Obsidian alone does NOT do that (state is the authority)."""
        bot = self
        tag_group = discord.app_commands.Group(
            name="tag", description="Tag tools for pending saves"
        )

        @tag_group.command(
            name="add",
            description="Add a tag to a pending save — searches your existing vault tags as you type",
        )
        @discord.app_commands.describe(
            tag="Tag to add (suggestions are existing vault tags with usage counts)",
            item="Which pending save (default: newest)",
        )
        async def tag_add(interaction: discord.Interaction, tag: str, item: str | None = None):
            await bot._tag_add_impl(interaction, tag, item)

        @tag_add.autocomplete("tag")
        async def _tag_ac(interaction: discord.Interaction, current: str):
            return await bot._tag_choices(current)

        @tag_add.autocomplete("item")
        async def _item_ac(interaction: discord.Interaction, current: str):
            return bot._pending_choices(current)

        self.tree.add_command(tag_group)

        @discord.app_commands.command(
            name="forget",
            description="Forget a saved URL so it can be reprocessed (deleting its note in Obsidian isn't enough)",
        )
        @discord.app_commands.describe(
            url="The URL to forget — suggestions come from your saved history"
        )
        async def forget_cmd(interaction: discord.Interaction, url: str):
            await bot._forget_impl(interaction, url)

        @forget_cmd.autocomplete("url")
        async def _forget_ac(interaction: discord.Interaction, current: str):
            return bot._forget_choices(current)

        self.tree.add_command(forget_cmd)

    def _build_view(self, pending: PendingApproval) -> ApprovalView:
        """Pick the approval-view variant for an item: the warning variant when fact-check or
        location flags are present, else the standard one. Both carry the item's *real*
        pending ID, so button clicks resolve to the correct item — including after a restart.
        The deep (web-searched) fact-check button is shown only for posts with checkable
        topics that haven't already had the deep check run."""
        ai = pending.ai_result
        has_flags = bool(ai.get("_fact_check") or ai.get("_location_check"))
        checkable = set(
            self.config.get("fact_checking", {}).get("topics", ["health", "political", "finance"])
        )
        topics = ai.get("topics") or []
        show_deep = any(t in checkable for t in topics) and not ai.get("_deep_fact_check_done")
        cls = ApprovalViewWithWarning if has_flags else ApprovalView
        return cls(self, pending.id, show_deep_button=show_deep)

    async def _refresh_card(self, pending: PendingApproval) -> None:
        """Re-render the item's ORIGINAL approval message (embed + buttons) with its current
        state. Called after every mutation — Add Tags, Remove Tags, tag swap, /tag add,
        Change Path, NL edit — so the card always shows what will actually be approved.
        Before this existed, edits lived only in the store and the stale card made every
        approval look like approving unedited content (Bora, 2026-07-05). Best-effort: a
        missing card (deleted message, renamed channel) must never block the edit itself."""
        if pending.discord_message_id is None:
            return
        channel_name = self._discord_cfg.get("channel_approvals", "SAVES-approvals")
        channel = _get_channel(self, channel_name)
        if channel is None:
            logger.warning("Card refresh skipped: channel #%s not found", channel_name)
            return
        try:
            msg = await channel.fetch_message(pending.discord_message_id)
            await msg.edit(embed=build_approval_embed(pending), view=self._build_view(pending))
        except discord.HTTPException as e:
            logger.warning("Could not refresh approval card for %s: %s", pending.url, e)

    async def _run_deep_fact_check(self, pending: PendingApproval):
        """Run the on-demand web-searched fact-check for a pending item, store the result on
        it, and mark the deep check done. Reconstructs a lightweight ExtractedContent from the
        stored content_summary — images aren't needed (OCR text already lives in ai_result)."""
        from src.ai.claude_client import fact_check
        from src.extractors.base import ExtractedContent
        cs = pending.content_summary
        content = ExtractedContent(
            url=pending.url,
            platform=pending.platform,
            title=cs.get("title", ""),
            author=cs.get("author"),
            body_text=cs.get("body_text", ""),
            captions=cs.get("captions"),
            metadata=cs.get("metadata", {}),
            chapters=cs.get("chapters"),
            top_comments=cs.get("top_comments"),
        )
        result = await fact_check(
            content, pending.ai_result, self.config,
            image_blocks=None, allow_web_search=True,
        )
        if result:
            pending.ai_result["_fact_check"] = result
        pending.ai_result["_deep_fact_check_done"] = True
        self.store.update(pending)
        return result

    async def setup_hook(self):
        # Re-register a persistent view for every already-sent approval, bound to its specific
        # Discord message. discord.py routes a button click to the view registered for that
        # message id (falling back to a message-agnostic view only when none is found), so each
        # restored view carries the item's real pending ID instead of a shared placeholder that
        # would resolve to None → "already processed" and strand the item after a restart.
        # Items still awaiting their first send (discord_message_id is None) are (re)sent by
        # _restore_pending in on_ready, and channel.send() registers that view automatically.
        for item in self.store.get_all():
            if item.discord_message_id is not None:
                self.add_view(self._build_view(item), message_id=item.discord_message_id)
        self.cookie_check_loop.start()

    async def on_ready(self):
        logger.info(f"Discord bot ready: {self.user}")
        # Sync slash commands (/tag add) per-guild: guild-scoped sync is visible instantly,
        # while a global sync can take up to an hour to propagate. Once per process; a sync
        # failure (e.g. missing applications.commands scope) must not block approvals.
        if not self._commands_synced:
            try:
                for guild in self.guilds:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                self._commands_synced = True
                logger.info("Slash commands synced to %d guild(s)", len(self.guilds))
            except discord.HTTPException as e:
                logger.warning("Slash-command sync failed (approvals unaffected): %s", e)
        await self._restore_pending()

    async def _restore_pending(self):
        for item in self.store.get_all():
            if item.discord_message_id is None:
                await self.send_for_approval(item)

    async def send_for_approval(self, pending: PendingApproval) -> None:
        view = self._build_view(pending)
        channel_name = self._discord_cfg.get("channel_approvals", "SAVES-approvals")
        msg_id = await send_approval_request(self, channel_name, pending, view)
        if msg_id:
            pending.discord_message_id = msg_id
            self.store.update(pending)

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        pending_id = _nl_edit_sessions.get(message.channel.id)
        if pending_id:
            pending = self.store.get_by_id(pending_id)
            if pending:
                await self._handle_nl_edit(message, pending)

    async def _handle_nl_edit(self, message: discord.Message, pending: PendingApproval):
        from src.ai.claude_client import nl_edit
        try:
            result = await nl_edit(pending.ai_result, message.content, self.config)
        except Exception as e:
            # An unguarded API failure here left the NL-edit session open with NO reply —
            # the bot appeared to silently ignore the user. Reply with the error and keep
            # the session active (deliberate: retrying is then just typing again).
            logger.warning("NL edit failed for %s: %s", pending.url, e)
            await message.reply(
                f"⚠️ NL edit failed ({e}) — the item is unchanged.\n"
                "Type your instruction again to retry, or use the buttons instead."
            )
            return
        # One instruction may map to several actions ("move to X and add tag Y"), so the
        # model returns {"actions": [...]}; a bare single-action object (older prompt
        # form, or the cancel case) is normalized into the same list.
        actions = result.get("actions")
        if not isinstance(actions, list):
            actions = [result]

        if any(a.get("action") == "cancel" for a in actions if isinstance(a, dict)):
            _nl_edit_sessions.pop(message.channel.id, None)
            reason = next(
                (a.get("reason") for a in actions if isinstance(a, dict) and a.get("reason")),
                None,
            )
            await message.reply(f"NL Edit cancelled — {reason}" if reason else "NL Edit cancelled.")
            return

        applied = 0
        for act in actions:
            if not isinstance(act, dict):
                continue
            action = act.get("action")
            value = act.get("value")
            if action == "change_path" and value:
                pending.ai_result["folder_path"] = clean_folder_path(value)
                applied += 1
            elif action == "add_tags" and value:
                tags = list(pending.ai_result.get("tags") or [])
                for t in value:
                    if t not in tags:
                        tags.append(t)
                pending.ai_result["tags"] = tags
                applied += 1
            elif action == "remove_tags" and value:
                pending.ai_result["tags"] = [
                    t for t in (pending.ai_result.get("tags") or []) if t not in value
                ]
                applied += 1
            elif action == "rename_title" and value:
                pending.ai_result["title"] = value
                applied += 1

        if not applied:
            # Nothing recognized — say so and keep the session open so a rephrase works,
            # instead of replying "Applied" with an unchanged card.
            await message.reply(
                "⚠️ I couldn't map that instruction to an edit — nothing was changed.\n"
                "Try rephrasing (e.g. \"add tags: espresso cold-brew\", "
                "\"move to SAVES/COOKING\"), or use the buttons."
            )
            return

        self.store.update(pending)
        _nl_edit_sessions.pop(message.channel.id, None)
        # Re-render the approval card FIRST — it is the thing the user approves from, so it
        # must show the edit. (The old flow updated only the store and replied with a
        # preview capped at 8 tags: NL-added tags append at the END of the list, so "add
        # tags for each AI tool" looked like a no-op even though it applied.)
        await self._refresh_card(pending)

        preview = (
            f"✅ Applied — the approval card is updated:\n"
            f"**Title:** {pending.ai_result.get('title')}\n"
            f"**Path:** {pending.ai_result.get('folder_path')}\n"
            f"**Tags:** {' '.join('#' + t for t in (pending.ai_result.get('tags') or []))}\n\n"
            f"Use ✅ Approve on the card to finalize."
        )
        await message.reply(preview)

    async def _finalize(
        self, pending: PendingApproval,
        interaction: discord.Interaction,
        include_warnings: bool = False,
    ):
        from src.extractors.base import ExtractedContent
        paths = self.config.get("paths", {})

        # Idempotency guard — processing_state.json is the source of truth. If this URL is
        # already marked done (double-click, or a button whose message was restored after a
        # restart while the note had already been written), do NOT write a second note.
        # write_note never overwrites, so a re-run would create a "-2" duplicate. Clean up
        # the stale pending entry and tell the user where it already lives.
        if self.state is not None and self.state.is_done(pending.url):
            existing = self.state.path_for(pending.url) or "vault"
            self.store.remove(pending.id)
            try:
                await interaction.edit_original_response(
                    content=f"✅ Already saved to `{existing}`", embed=None, view=None
                )
            except discord.HTTPException:
                pass
            return

        cs = pending.content_summary
        content = ExtractedContent(
            url=pending.url,
            platform=pending.platform,
            title=cs.get("title", ""),
            author=cs.get("author"),
            body_text=cs.get("body_text", ""),
            captions=cs.get("captions"),
            metadata=cs.get("metadata", {}),
            chapters=cs.get("chapters"),
            top_comments=cs.get("top_comments"),
        )

        alert_channel = self._discord_cfg.get("channel_alerts", "SAVES-alerts")
        try:
            note_md = format_note(
                pending.ai_result, content,
                pending.media_paths, pending.transcript,
                self.config,
                fact_check_result=pending.ai_result.get("_fact_check"),
                location_check_result=pending.ai_result.get("_location_check"),
                include_warnings=include_warnings,
            )

            note_path = write_note(
                vault_root=paths.get("vault_root", "/vault"),
                folder_path=pending.ai_result["folder_path"],
                filename=pending.ai_result.get("title") or pending.ai_result.get("filename", "untitled"),
                content=note_md,
            )
        except Exception as e:
            logger.exception("_finalize failed for %s: %s", pending.url, e)
            await send_alert(self, alert_channel,
                             f"❌ Save failed — item still pending, click Approve to retry.\n"
                             f"URL: {pending.url}\nError: {e}")
            try:
                await interaction.edit_original_response(
                    content=f"❌ Save failed: `{e}`\nItem is still pending — click Approve to retry."
                )
            except discord.HTTPException:
                pass
            return

        # Record completion in the state file immediately after the note is on disk — before
        # the slower preference/inbox/Discord cleanup — so a crash mid-cleanup can't cause a
        # duplicate note on re-approval (the guard at the top short-circuits on state=done).
        if self.state is not None:
            self.state.mark_done(pending.url, note_path)

        # Save learned preference: source → final folder path
        source_key = pending.ai_result.get("_source_key")
        final_path = pending.ai_result["folder_path"]
        self.prefs.set(source_key, final_path)

        # Bump the tag index so this note's tags are immediately searchable in /tag add
        # autocomplete (no wait for the TTL rescan). Non-fatal — the note is already saved.
        try:
            get_tag_index(self.config).add(pending.ai_result.get("tags") or [])
        except Exception as e:
            logger.debug("Tag index bump skipped: %s", e)

        remove_url_from_inbox(paths.get("inbox_file", ""), pending.url)
        self.store.remove(pending.id)

        log_channel = self._discord_cfg.get("channel_log", "SAVES-logs")
        await send_log(self, log_channel, f"✅ Note created: `{note_path}`")

        await interaction.edit_original_response(
            content=f"✅ Saved to `{note_path}`", embed=None, view=None
        )

    @tasks.loop(hours=24)
    async def cookie_check_loop(self):
        paths = self.config.get("paths", {})
        cookies_dir = paths.get("cookies_dir", "cookies")
        warnings = check_all_cookies(self.config, cookies_dir)
        alert_channel = self._discord_cfg.get("channel_alerts", "SAVES-alerts")
        for w in warnings:
            await send_cookie_warning(self, alert_channel, w)

    @cookie_check_loop.before_loop
    async def before_cookie_check(self):
        await self.wait_until_ready()
