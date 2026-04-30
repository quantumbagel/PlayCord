from typing import Any

import discord
from discord.ext import commands

from playcord.infrastructure.constants import (
    BUTTON_PREFIX_INVITE,
    BUTTON_PREFIX_JOIN,
    BUTTON_PREFIX_LEAVE,
    BUTTON_PREFIX_LOBBY_ASSIGN_ROLES,
    BUTTON_PREFIX_LOBBY_OPT,
    BUTTON_PREFIX_LOBBY_ROLE,
    BUTTON_PREFIX_READY,
    EPHEMERAL_DELETE_AFTER,
)
from playcord.infrastructure.locale import get
from playcord.infrastructure.logging import get_logger
from playcord.presentation.interactions.contextify import contextify
from playcord.presentation.interactions.helpers import followup_send

log = get_logger()


class MatchmakingCog(commands.Cog):
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot

    @property
    def _lobbies(self) -> dict[int, Any]:
        return self.bot.container.registry.matchmaking_by_message_id

    # No specific commands here yet as they are mostly subcommands of playcord or play
    # But we can store callbacks here

    @commands.Cog.listener()
    async def on_interaction(self, ctx: discord.Interaction) -> None:
        """Callback activated after every bot interaction."""
        data = ctx.data if ctx.data is not None else {}
        custom_id = data.get("custom_id")
        if custom_id is None:
            return

        log.getChild("on_interaction").debug(
            "on_interaction custom_id=%r user=%s",
            custom_id,
            getattr(ctx.user, "id", None),
        )

        if custom_id.startswith(BUTTON_PREFIX_LOBBY_OPT):
            await self.lobby_select_callback(ctx)
        elif custom_id.startswith(BUTTON_PREFIX_LOBBY_ROLE):
            await self.lobby_role_select_callback(ctx)
        elif custom_id.startswith(BUTTON_PREFIX_LOBBY_ASSIGN_ROLES):
            await self.lobby_assign_roles_callback(ctx)
        elif custom_id.startswith(
            (BUTTON_PREFIX_JOIN, BUTTON_PREFIX_LEAVE, BUTTON_PREFIX_READY),
        ):
            await self.matchmaking_button_callback(ctx)
        elif custom_id.startswith(BUTTON_PREFIX_INVITE):
            await self.invite_accept_callback(ctx)

    async def lobby_select_callback(self, ctx: discord.Interaction) -> None:
        """Lobby string-select for per-game
        match options (handled by MatchmakingInterface).
        """
        await ctx.response.defer(ephemeral=True)
        f_log = log.getChild("callback.lobby_select")
        f_log.debug(
            "lobby_select_callback called by user=%s data=%r",
            getattr(ctx.user, "id", None),
            ctx.data,
        )
        data = ctx.data if ctx.data is not None else {}
        cid = data.get("custom_id")
        if not cid or not cid.startswith(BUTTON_PREFIX_LOBBY_OPT):
            f_log.warning(
                "Invalid lobby_select interaction from user=%s cid=%r",
                getattr(ctx.user, "id", None),
                cid,
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_interaction"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        rest = cid[len(BUTTON_PREFIX_LOBBY_OPT) :]
        mid_str, _, key = rest.partition("/")
        if not mid_str or not key:
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_button"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        try:
            matchmaking_id = int(mid_str)
        except ValueError:
            f_log.warning(
                "Invalid matchmaking id in lobby_select from user=%s mid=%r",
                getattr(ctx.user, "id", None),
                mid_str,
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_button"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        if matchmaking_id not in self._lobbies:
            f_log.info(
                "Lobby select for expired matchmaking_id=%s by user=%s",
                matchmaking_id,
                getattr(ctx.user, "id", None),
            )
            await followup_send(
                ctx,
                content=get("matchmaking.session_expired"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        f_log.debug(
            "lobby option key=%r lobby=%s user=%s",
            key,
            matchmaking_id,
            ctx.user.id,
        )
        matchmaker = self._lobbies[matchmaking_id]
        await matchmaker.callback_lobby_option(ctx, key)

    async def lobby_role_select_callback(self, ctx: discord.Interaction) -> None:
        """Per-player role select for
        CHOSEN :attr:`role_mode` (handled by MatchmakingInterface).
        """
        await ctx.response.defer(ephemeral=True)
        f_log = log.getChild("callback.lobby_role_select")
        f_log.debug(
            "lobby_role_select_callback called by user=%s data=%r",
            getattr(ctx.user, "id", None),
            ctx.data,
        )
        data = ctx.data if ctx.data is not None else {}
        cid = data.get("custom_id")
        if not cid or not cid.startswith(BUTTON_PREFIX_LOBBY_ROLE):
            f_log.warning(
                "Invalid lobby_role_select interaction from user=%s cid=%r",
                getattr(ctx.user, "id", None),
                cid,
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_interaction"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        rest = cid[len(BUTTON_PREFIX_LOBBY_ROLE) :]
        mid_str, _, pid_str = rest.partition("/")
        if not mid_str or not pid_str:
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_button"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        try:
            matchmaking_id = int(mid_str)
            player_id = int(pid_str)
        except ValueError:
            f_log.warning(
                "Invalid ids in lobby_role_select from user=%s mid=%r pid=%r",
                getattr(ctx.user, "id", None),
                mid_str,
                pid_str,
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_button"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        if matchmaking_id not in self._lobbies:
            f_log.info(
                "Lobby role select for expired matchmaking_id=%s by user=%s",
                matchmaking_id,
                getattr(ctx.user, "id", None),
            )
            await followup_send(
                ctx,
                content=get("matchmaking.session_expired"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        f_log.debug(
            "lobby role pick lobby=%s player_id=%s user=%s",
            matchmaking_id,
            player_id,
            ctx.user.id,
        )
        matchmaker = self._lobbies[matchmaking_id]
        await matchmaker.callback_role_select(ctx, player_id)

    async def lobby_assign_roles_callback(self, ctx: discord.Interaction) -> None:
        """Assign roles button for selectable_random flow."""
        await ctx.response.defer(ephemeral=True)
        f_log = log.getChild("callback.lobby_assign_roles")
        f_log.debug(
            "lobby_assign_roles_callback called by user=%s data=%r",
            getattr(ctx.user, "id", None),
            ctx.data,
        )
        data = ctx.data if ctx.data is not None else {}
        cid = data.get("custom_id")
        if not cid or not cid.startswith(BUTTON_PREFIX_LOBBY_ASSIGN_ROLES):
            f_log.warning(
                "Invalid lobby_assign_roles interaction from user=%s cid=%r",
                getattr(ctx.user, "id", None),
                cid,
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_interaction"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        rest = cid[len(BUTTON_PREFIX_LOBBY_ASSIGN_ROLES) :]
        mid_str = rest.rstrip("/")
        if not mid_str:
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_button"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        try:
            matchmaking_id = int(mid_str)
        except ValueError:
            f_log.warning(
                "Invalid id in lobby_assign_roles from user=%s mid=%r",
                getattr(ctx.user, "id", None),
                mid_str,
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_button"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        if matchmaking_id not in self._lobbies:
            f_log.info(
                "Lobby assign roles for expired matchmaking_id=%s by user=%s",
                matchmaking_id,
                getattr(ctx.user, "id", None),
            )
            await followup_send(
                ctx,
                content=get("matchmaking.session_expired"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        f_log.debug(
            "lobby assign roles lobby=%s user=%s",
            matchmaking_id,
            ctx.user.id,
        )
        matchmaker = self._lobbies[matchmaking_id]
        await matchmaker.callback_assign_roles(ctx)

    async def matchmaking_button_callback(self, ctx: discord.Interaction) -> None:
        """Handle matchmaking button (Join / Leave / Ready)."""
        await ctx.response.defer()
        f_log = log.getChild("callback.matchmaking_button")

        data = ctx.data if ctx.data is not None else {}
        cid = data.get("custom_id")
        if not cid:
            f_log.warning(
                "Empty custom_id in matchmaking_button callback from user=%s",
                getattr(ctx.user, "id", None),
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_interaction"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        # Get interaction context
        interaction_context = contextify(ctx)
        f_log.info(
            f"matchmaking button pressed! ID: {cid} context: {interaction_context}",
        )
        f_log.debug(
            "matchmaking_button cid=%r user=%s",
            cid,
            getattr(ctx.user, "id", None),
        )

        # Leading ID of custom ID string
        if cid.startswith(BUTTON_PREFIX_JOIN):
            leading_str = BUTTON_PREFIX_JOIN
        elif cid.startswith(BUTTON_PREFIX_LEAVE):
            leading_str = BUTTON_PREFIX_LEAVE
        elif cid.startswith(BUTTON_PREFIX_READY):
            leading_str = BUTTON_PREFIX_READY
        else:
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_button"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        try:
            matchmaking_id = int(cid.replace(leading_str, ""))
        except ValueError:
            f_log.warning(
                "Invalid matchmaking id in button callback from user=%s cid=%r",
                getattr(ctx.user, "id", None),
                cid,
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_button"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        # Check if it exists
        if matchmaking_id not in self._lobbies:
            f_log.debug(
                "Matchmaking expired when trying to press button: %s",
                interaction_context,
            )
            await followup_send(
                ctx,
                content=get("matchmaking.session_expired"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        matchmaker = self._lobbies[matchmaking_id]

        # Call MatchmakingInterface callbacks
        if leading_str == BUTTON_PREFIX_JOIN:
            f_log.info(
                "Invoking callback_ready_game for matchmaking_id=%s user=%s",
                matchmaking_id,
                getattr(ctx.user, "id", None),
            )
            await matchmaker.callback_ready_game(ctx)
        elif leading_str == BUTTON_PREFIX_LEAVE:
            f_log.info(
                "Invoking callback_leave_game for matchmaking_id=%s user=%s",
                matchmaking_id,
                getattr(ctx.user, "id", None),
            )
            await matchmaker.callback_leave_game(ctx)
        elif leading_str == BUTTON_PREFIX_READY:
            f_log.info(
                "Invoking callback_toggle_ready for matchmaking_id=%s user=%s",
                matchmaking_id,
                getattr(ctx.user, "id", None),
            )
            await matchmaker.callback_toggle_ready(ctx)

    async def invite_accept_callback(self, ctx: discord.Interaction) -> None:
        """Invite accept button callback."""
        await ctx.response.defer()
        f_log = log.getChild("callback.invite_accept")
        f_log.debug(
            "invite_accept_callback called by user=%s data=%r",
            getattr(ctx.user, "id", None),
            ctx.data,
        )

        data = ctx.data if ctx.data is not None else {}
        cid = data.get("custom_id")
        try:
            matchmaking_id = int(cid.replace(BUTTON_PREFIX_INVITE, ""))
        except (TypeError, ValueError, AttributeError):
            f_log.warning(
                "Invalid invite custom_id from user=%s cid=%r",
                getattr(ctx.user, "id", None),
                cid,
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invalid_button"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        if matchmaking_id not in self._lobbies:
            await followup_send(
                ctx,
                content=get("matchmaking.invite_expired"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        matchmaker = self._lobbies[matchmaking_id]
        success = await matchmaker.accept_invite(ctx)

        if success:
            f_log.info(
                "Invite accepted for matchmaking_id=%s by user=%s",
                matchmaking_id,
                getattr(ctx.user, "id", None),
            )
            await followup_send(
                ctx,
                content=get("matchmaking.invite_ok"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MatchmakingCog(bot))
