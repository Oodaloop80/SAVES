import logging

import discord
from discord.ext import tasks

from src.discord_bot.approval import PendingApproval, PendingApprovalsStore
from src.discord_bot.notifications import send_approval_request, send_cookie_warning, send_log
from src.notes.file_manager import write_note
from src.notes.formatter import format_note
from src.utils.cookie_checker import check_all_cookies
from src.utils.file_io import remove_url_from_inbox
from src.utils.preferences import PreferencesStore

logger = logging.getLogger(__name__)

# Maps channel_id → pending item ID for active NL edit sessions
_nl_edit_sessions: dict[int, str] = {}


class ApprovalView(discord.ui.View):
    def __init__(self, bot: "SAVESBot", pending_id: str):
        super().__init__(timeout=None)
        self.bot = bot
        self.pending_id = pending_id

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success, custom_id="approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        pending = self.bot.store.get_by_id(self.pending_id)
        if not pending:
            await interaction.followup.send("This item has already been processed.", ephemeral=True)
            return
        await self.bot._finalize(pending, interaction, include_warnings=False)

    @discord.ui.button(label="📁 Change Path", style=discord.ButtonStyle.secondary, custom_id="change_path")
    async def change_path(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PathModal(self.bot, self.pending_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🏷️ Edit Tags", style=discord.ButtonStyle.secondary, custom_id="edit_tags")
    async def edit_tags(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = TagsModal(self.bot, self.pending_id)
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
        tag_list = "  ".join(f"`{t}`" for t in tags)
        await interaction.response.send_message(
            f"**Current tags:**\n{tag_list}\n\nSelect tags to remove:",
            view=view,
            ephemeral=True,
        )


class TagRemoveView(discord.ui.View):
    """Ephemeral view with a multi-select dropdown to remove individual tags."""

    def __init__(self, bot: "SAVESBot", pending_id: str, tags: list[str]):
        super().__init__(timeout=120)
        self.bot = bot
        self.pending_id = pending_id
        options = [discord.SelectOption(label=t, value=t) for t in tags[:25]]
        select = discord.ui.Select(
            placeholder="Pick tags to remove (multi-select)…",
            min_values=0,
            max_values=len(options),
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        to_remove = set(interaction.data["values"])
        pending = self.bot.store.get_by_id(self.pending_id)
        if not pending:
            await interaction.response.edit_message(content="Already processed.", view=None)
            return
        pending.ai_result["tags"] = [
            t for t in (pending.ai_result.get("tags") or []) if t not in to_remove
        ]
        self.bot.store.update(pending)
        remaining = "  ".join(f"`{t}`" for t in pending.ai_result["tags"])
        msg = (
            f"Removed **{len(to_remove)}** tag(s).\n"
            f"**Remaining:** {remaining or '*(none)*'}\n\n"
            f"Use ✅ Approve when ready."
        )
        await interaction.response.edit_message(content=msg, view=None)


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
        label="New folder path",
        placeholder="SAVES/COOKING/SMOKING",
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
        pending.ai_result["folder_path"] = self.new_path.value.strip()
        self.bot.store.update(pending)
        await interaction.response.send_message(
            f"✅ Path updated to `{pending.ai_result['folder_path']}`\n"
            f"Use ✅ Approve when ready.", ephemeral=True
        )


class TagsModal(discord.ui.Modal, title="Edit Tags"):
    tag_edits = discord.ui.TextInput(
        label="Add or remove tags",
        placeholder="+weekend-project +bbq -oldtag",
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
        for token in self.tag_edits.value.split():
            if token.startswith("+"):
                t = token[1:].strip()
                if t and t not in tags:
                    tags.append(t)
            elif token.startswith("-"):
                t = token[1:].strip()
                tags = [x for x in tags if x != t]
        pending.ai_result["tags"] = tags
        self.bot.store.update(pending)
        preview = " ".join(f"#{t}" for t in tags)
        await interaction.response.send_message(
            f"✅ Tags updated: {preview}\nUse ✅ Approve when ready.", ephemeral=True
        )


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

    def _build_view(self, pending: PendingApproval) -> ApprovalView:
        """Pick the approval-view variant for an item: the warning variant when fact-check or
        location flags are present, else the standard one. Both carry the item's *real*
        pending ID, so button clicks resolve to the correct item — including after a restart."""
        has_flags = bool(
            pending.ai_result.get("_fact_check") or pending.ai_result.get("_location_check")
        )
        return ApprovalViewWithWarning(self, pending.id) if has_flags else ApprovalView(self, pending.id)

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
        result = await nl_edit(pending.ai_result, message.content, self.config)
        action = result.get("action")
        value = result.get("value")

        if action == "change_path" and value:
            pending.ai_result["folder_path"] = value
        elif action == "add_tags" and value:
            tags = list(pending.ai_result.get("tags") or [])
            for t in value:
                if t not in tags:
                    tags.append(t)
            pending.ai_result["tags"] = tags
        elif action == "remove_tags" and value:
            pending.ai_result["tags"] = [
                t for t in (pending.ai_result.get("tags") or []) if t not in value
            ]
        elif action == "rename_title" and value:
            pending.ai_result["title"] = value
        elif action == "cancel":
            _nl_edit_sessions.pop(message.channel.id, None)
            await message.reply("NL Edit cancelled.")
            return

        self.store.update(pending)
        _nl_edit_sessions.pop(message.channel.id, None)

        preview = (
            f"Updated preview:\n"
            f"**Title:** {pending.ai_result.get('title')}\n"
            f"**Path:** {pending.ai_result.get('folder_path')}\n"
            f"**Tags:** {' '.join('#'+t for t in (pending.ai_result.get('tags') or [])[:8])}\n\n"
            f"Use ✅ Approve button to finalize."
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

        # Record completion in the state file immediately after the note is on disk — before
        # the slower preference/inbox/Discord cleanup — so a crash mid-cleanup can't cause a
        # duplicate note on re-approval (the guard at the top short-circuits on state=done).
        if self.state is not None:
            self.state.mark_done(pending.url, note_path)

        # Save learned preference: source → final folder path
        source_key = pending.ai_result.get("_source_key")
        final_path = pending.ai_result["folder_path"]
        self.prefs.set(source_key, final_path)

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
