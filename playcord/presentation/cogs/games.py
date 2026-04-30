from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

import discord
from discord import app_commands
from discord.ext import commands

from playcord.api import HandlerRef
from playcord.application.runtime_context import get_container
from playcord.application.services import replay_viewer
from playcord.core.errors import ConfigurationError
from playcord.infrastructure.constants import (
    BUTTON_PREFIX_GAME_MOVE,
    BUTTON_PREFIX_GAME_SELECT,
    BUTTON_PREFIX_PAGINATION_FIRST,
    BUTTON_PREFIX_PAGINATION_LAST,
    BUTTON_PREFIX_PAGINATION_NEXT,
    BUTTON_PREFIX_PAGINATION_PREV,
    BUTTON_PREFIX_PEEK,
    BUTTON_PREFIX_REMATCH,
    BUTTON_PREFIX_REPLAY_NAV,
    BUTTON_PREFIX_SPECTATE,
    EPHEMERAL_DELETE_AFTER,
)
from playcord.infrastructure.db_thread import run_in_thread
from playcord.infrastructure.locale import fmt, get
from playcord.infrastructure.logging import get_logger
from playcord.infrastructure.state.matchmaking_registry import matchmaking_by_user_id
from playcord.infrastructure.state.user_games import user_in_active_game
from playcord.presentation.interactions.error import ErrorSurface, report
from playcord.presentation.interactions.helpers import (
    decode_discord_arguments,
    discord_user_db_label,
    followup_send,
    format_user_error_message,
    response_send_message,
)
from playcord.presentation.interactions.matchmaking_lobby import MatchmakingInterface
from playcord.presentation.ui.containers import LoadingContainer, container_send_kwargs
from playcord.presentation.ui.replay_views import ReplayViewerView

if TYPE_CHECKING:
    from discord.app_commands import Choice

    from playcord.presentation.bot import PlayCordBot

log = get_logger()


async def _send_game_ended_error(ctx: discord.Interaction) -> None:
    log.getChild("interaction.game_error").debug(
        "Sending game_ended error to user=%s",
        getattr(ctx.user, "id", None),
    )
    await followup_send(
        ctx,
        content=format_user_error_message("game_ended"),
        ephemeral=True,
        delete_after=EPHEMERAL_DELETE_AFTER,
    )


def _autocomplete_sort_key(label: str, current: str) -> tuple:
    lo, cu = label.lower(), current.lower()
    try:
        return lo.index(cu), lo
    except ValueError:
        return 0, lo


_PAGINATION_PREFIXES = (
    BUTTON_PREFIX_PAGINATION_FIRST,
    BUTTON_PREFIX_PAGINATION_PREV,
    BUTTON_PREFIX_PAGINATION_NEXT,
    BUTTON_PREFIX_PAGINATION_LAST,
)


async def _pagination_unhandled_fallback(
    interaction: discord.Interaction,
    custom_id: str,
) -> None:
    """If no registered PaginationView handled the click (e.g. after restart).

    Reply ephemerally.
    """
    await asyncio.sleep(0)
    if interaction.response.is_done():
        return
    rest = custom_id
    for prefix in _PAGINATION_PREFIXES:
        if custom_id.startswith(prefix):
            rest = custom_id[len(prefix) :]
            break
    msg = get("interactions.pagination_outdated")
    parts = rest.split("/")
    if len(parts) == 2:
        try:
            gid, uid = int(parts[0]), int(parts[1])
        except ValueError:
            pass
        else:
            if interaction.user.id != uid or (
                interaction.guild_id is not None and gid != interaction.guild_id
            ):
                msg = get("interactions.pagination_not_yours")
    with contextlib.suppress(discord.HTTPException):
        await response_send_message(
            interaction,
            msg,
            ephemeral=True,
            delete_after=EPHEMERAL_DELETE_AFTER,
        )


def _parse_component_id(custom_id: str, prefix: str) -> tuple[int, str]:
    tail = custom_id[len(prefix) :]
    resource_raw, payload = ([*tail.split("/", 1), ""])[:2]
    return int(resource_raw), payload


class GamesCog(commands.Cog):
    def __init__(self, bot: PlayCordBot) -> None:
        self.bot = bot

    @property
    def _translator(self):
        return getattr(getattr(self.bot, "container", None), "translator", None)

    @property
    def _replay_source(self) -> replay_viewer.ReplayDataSource | None:
        return replay_viewer.ReplayDataSource(
            matches_repository=self.bot.container.matches_repository,
            games_repository=self.bot.container.games_repository,
            players_repository=self.bot.container.players_repository,
            replays_repository=self.bot.container.replays_repository,
        )

    @property
    def _reg(self):
        return self.bot.container.registry

    @property
    def _active_games(self):
        return self._reg.games_by_thread_id

    @commands.Cog.listener()
    async def on_interaction(self, ctx: discord.Interaction) -> None:
        data = ctx.data if ctx.data is not None else {}
        custom_id = data.get("custom_id")
        if custom_id is None:
            return

        if ctx.type is discord.InteractionType.component and any(
            custom_id.startswith(p) for p in _PAGINATION_PREFIXES
        ):
            asyncio.create_task(_pagination_unhandled_fallback(ctx, custom_id))
            return

        try:
            if custom_id.startswith(BUTTON_PREFIX_GAME_MOVE):
                resource_id, payload = _parse_component_id(
                    custom_id,
                    BUTTON_PREFIX_GAME_MOVE,
                )
                await self._route_runtime_move(
                    ctx,
                    resource_id=resource_id,
                    payload=payload,
                )
            elif custom_id.startswith(BUTTON_PREFIX_GAME_SELECT):
                resource_id, payload = _parse_component_id(
                    custom_id,
                    BUTTON_PREFIX_GAME_SELECT,
                )
                await self._route_runtime_select(
                    ctx,
                    resource_id=resource_id,
                    payload=payload,
                )
            elif custom_id.startswith(BUTTON_PREFIX_REPLAY_NAV):
                resource_id, payload = _parse_component_id(
                    custom_id,
                    BUTTON_PREFIX_REPLAY_NAV,
                )
                await self._route_replay_nav(
                    ctx,
                    resource_id=resource_id,
                    payload=payload,
                )
            elif custom_id.startswith(BUTTON_PREFIX_SPECTATE):
                await self.spectate_callback(ctx)
            elif custom_id.startswith(BUTTON_PREFIX_PEEK):
                await self.peek_callback(ctx)
            elif custom_id.startswith(BUTTON_PREFIX_REMATCH):
                await self.rematch_button_callback(ctx)
        except Exception as exc:
            await report(
                ctx,
                exc,
                surface=ErrorSurface.COMPONENT,
                translator=self._translator,
            )

    async def _route_replay_nav(
        self,
        ctx: discord.Interaction,
        resource_id: int,
        payload: str,
    ) -> None:
        await ctx.response.defer()
        payload_data = parse_qs(payload)
        owner_raw = payload_data.get("owner", [""])[0]
        try:
            owner_id = int(owner_raw)
        except (TypeError, ValueError):
            owner_id = 0
        if owner_id != ctx.user.id:
            await followup_send(
                ctx,
                get("interactions.pagination_not_yours"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        target = payload_data.get("frame", ["0"])[0]
        if isinstance(ctx.data, dict):
            values = ctx.data.get("values")
            if isinstance(values, list) and values:
                target = str(values[0])
        try:
            requested_frame = int(target)
        except (TypeError, ValueError):
            requested_frame = 0

        source = self._replay_source
        if source is None:
            await followup_send(
                ctx,
                content=format_user_error_message("replay_not_found"),
                ephemeral=True,
            )
            return

        replay_ctx = replay_viewer.load_replay_context(
            resource_id,
            source=source,
        )
        if replay_ctx is None or not replay_ctx.events:
            await followup_send(
                ctx,
                content=format_user_error_message("replay_not_found"),
                ephemeral=True,
            )
            return
        if not replay_viewer.supports_replay_api(replay_ctx.plugin_class):
            await followup_send(
                ctx,
                content=get("commands.replay.unavailable"),
                ephemeral=True,
            )
            return

        plugin_class = replay_ctx.plugin_class
        if plugin_class is None:
            msg = f"Replay plugin for match {replay_ctx.match_id} is not available"
            raise ConfigurationError(
                msg,
            )

        total_frames = replay_viewer.replay_frame_count(replay_ctx.events)
        frame = max(0, min(requested_frame, total_frames - 1))
        if total_frames <= replay_viewer.PRECOMPUTE_FRAME_LIMIT:
            frames = replay_viewer.get_precomputed_frames(replay_ctx.match_id)
            if not frames:
                frames = replay_viewer.build_frames(
                    plugin_class,
                    replay_ctx.events,
                    replay_ctx.players,
                    replay_ctx.match_options,
                    game_key=replay_ctx.game_key or plugin_class.metadata.key,
                )
                replay_viewer.cache_precomputed_frames(replay_ctx.match_id, frames)
            if frames:
                total_frames = len(frames)
                frame = min(frame, total_frames - 1)
                frame_layout = frames[frame]
            else:
                frame_layout = None
        else:
            frame_layout = replay_viewer.frame_for_index(
                match_id=replay_ctx.match_id,
                frame_index=frame,
                plugin_class=plugin_class,
                events=replay_ctx.events,
                players=replay_ctx.players,
                match_options=replay_ctx.match_options,
                game_key=replay_ctx.game_key or plugin_class.metadata.key,
            )
        if frame_layout is None:
            await followup_send(
                ctx,
                content=get("commands.replay.unavailable"),
                ephemeral=True,
            )
            return

        title = fmt(
            "commands.replay.title",
            id=replay_ctx.replay_display,
            game=replay_ctx.game_label,
        )
        view = ReplayViewerView(
            match_id=replay_ctx.match_id,
            owner_id=ctx.user.id,
            frame_index=frame,
            total_frames=total_frames,
            title=title,
            global_summary=replay_ctx.global_summary,
            frame_layout=frame_layout,
        )
        await ctx.edit_original_response(view=view)

    async def _route_runtime_move(
        self,
        ctx: discord.Interaction,
        resource_id: int,
        payload: str,
    ) -> None:
        await ctx.response.defer()
        runtime = self._active_games.get(resource_id)
        if runtime is None:
            await _send_game_ended_error(ctx)
            return
        await runtime.submit_component_input(
            ctx,
            payload=payload,
            source="button",
        )

    async def _route_runtime_select(
        self,
        ctx: discord.Interaction,
        resource_id: int,
        payload: str,
    ) -> None:
        await ctx.response.defer()
        runtime = self._active_games.get(resource_id)
        if runtime is None:
            await _send_game_ended_error(ctx)
            return
        await runtime.submit_component_input(
            ctx,
            payload=payload,
            source="select",
        )

    async def spectate_callback(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer()
        f_log = log.getChild("interaction.spectate")
        f_log.debug(
            "spectate_callback called by user=%s custom_id=%r",
            getattr(ctx.user, "id", None),
            ctx.data.get("custom_id"),
        )
        try:
            game_id = int(ctx.data["custom_id"].replace(BUTTON_PREFIX_SPECTATE, ""))
        except (KeyError, ValueError):
            f_log.warning(
                "Malformed spectate custom_id from user=%s: %r",
                getattr(ctx.user, "id", None),
                ctx.data.get("custom_id"),
            )
            await _send_game_ended_error(ctx)
            return
        if game_id not in self._active_games:
            f_log.info(
                "Spectate referenced non-active game_id=%s from user=%s",
                game_id,
                getattr(ctx.user, "id", None),
            )
            await _send_game_ended_error(ctx)
            return

        game = self._active_games[game_id]
        participant_ids = {p.id for p in game.players}
        if ctx.user.id in participant_ids:
            await followup_send(
                ctx,
                get("success.already_participant"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        await game.handle_spectate(ctx)

    async def peek_callback(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer()
        f_log = log.getChild("interaction.peek")
        f_log.debug(
            "peek_callback called by user=%s custom_id=%r",
            getattr(ctx.user, "id", None),
            ctx.data.get("custom_id"),
        )
        try:
            data = ctx.data["custom_id"].replace(BUTTON_PREFIX_PEEK, "").split("/")
            game_id = int(data[0])
        except (KeyError, IndexError, ValueError):
            f_log.warning(
                "Malformed peek custom_id from user=%s: %r",
                getattr(ctx.user, "id", None),
                ctx.data.get("custom_id"),
            )
            await _send_game_ended_error(ctx)
            return
        if game_id in self._active_games:
            await self._active_games[game_id].handle_peek(ctx)
        else:
            f_log.info(
                "Peek referenced non-active game_id=%s from user=%s",
                game_id,
                getattr(ctx.user, "id", None),
            )
            await _send_game_ended_error(ctx)

    async def rematch_button_callback(self, ctx: discord.Interaction) -> None:
        await ctx.response.defer(ephemeral=True)
        from playcord.infrastructure.database.models import MatchStatus

        f_log = log.getChild("interaction.rematch")
        f_log.debug(
            "rematch_button_callback called by user=%s custom_id=%r",
            getattr(ctx.user, "id", None),
            ctx.data.get("custom_id"),
        )

        tail = ctx.data["custom_id"].replace(BUTTON_PREFIX_REMATCH, "", 1)
        try:
            mid = int(tail)
        except ValueError:
            f_log.warning(
                "Malformed rematch id from user=%s: %r",
                getattr(ctx.user, "id", None),
                tail,
            )
            await followup_send(
                ctx,
                content=format_user_error_message("rematch_invalid"),
                ephemeral=True,
            )
            return
        match = self.bot.container.matches.get(mid)
        if not match or match.status != MatchStatus.COMPLETED:
            f_log.info(
                "Rematch requested for mid=%s but not available or not "
                "completed (match=%r)",
                mid,
                match,
            )
            await followup_send(
                ctx,
                content=format_user_error_message("rematch_unavailable"),
                ephemeral=True,
            )
            return
        human_ids = await run_in_thread(
            self.bot.container.matches_repository.get_match_human_user_ids_ordered,
            mid,
        )
        if ctx.user.id not in human_ids:
            f_log.warning(
                "User %s attempted rematch for mid=%s but is not participant",
                ctx.user.id,
                mid,
            )
            await followup_send(
                ctx,
                content=get("rematch.not_participant"),
                ephemeral=True,
            )
            return
        for uid in human_ids:
            if user_in_active_game(uid):
                f_log.info(
                    "Cannot rematch mid=%s because user %s is busy in another game",
                    mid,
                    uid,
                )
                await followup_send(
                    ctx,
                    content=get("rematch.someone_busy"),
                    ephemeral=True,
                )
                return
        g = ctx.guild
        if g is None or not isinstance(ctx.channel, discord.TextChannel):
            f_log.warning(
                "Rematch attempted in invalid channel by user=%s",
                ctx.user.id,
            )
            await followup_send(ctx, content=get("rematch.bad_channel"), ephemeral=True)
            return
        game_row = await run_in_thread(
            self.bot.container.games_repository.get_by_id,
            match.game_id,
        )
        if not game_row:
            f_log.error(
                "Rematch: game_row not found for game_id=%s (match=%s)",
                match.game_id,
                mid,
            )
            await followup_send(
                ctx,
                content=get("rematch.unknown_game"),
                ephemeral=True,
            )
            return
        game_type = game_row.game_name
        loading = await ctx.channel.send(
            **container_send_kwargs(LoadingContainer().remove_footer()),
        )
        creator_row = await run_in_thread(
            get_container().players_repository.get_player,
            ctx.user.id,
            discord_user_db_label(ctx.user),
        )
        mm = MatchmakingInterface(
            ctx.user,
            game_type,
            loading,
            rated=match.is_rated,
            private=False,
            creator_db_player=creator_row,
        )
        if mm.failed is not None:
            f_log.error(
                "MatchmakingInterface failed during rematch seed: %s",
                mm.failed,
            )
            await loading.edit(content=str(mm.failed), view=None, attachments=[])
            await followup_send(ctx, content=get("rematch.failed"), ephemeral=True)
            return
        err = await mm.seed_rematch_players(g, human_ids)
        if err:
            with contextlib.suppress(discord.HTTPException):
                await loading.delete()
            f_log.error("Failed to seed rematch players for mid=%s: %s", mid, err)
            await followup_send(ctx, content=err, ephemeral=True)
            return
        await mm.update_embed()
        f_log.info("Rematch lobby created for mid=%s by user=%s", mid, ctx.user.id)
        await followup_send(ctx, content=get("rematch.created"), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GamesCog(bot))


async def begin_game(
    ctx: discord.Interaction,
    game_type: str,
    rated: bool = True,
    private: bool = False,
) -> MatchmakingInterface | None:
    f_log = log.getChild("command.matchmaking")
    f_log.debug(
        "begin_game called by user=%s game_type=%r rated=%s private=%s",
        getattr(ctx.user, "id", None),
        game_type,
        rated,
        private,
    )
    if user_in_active_game(ctx.user.id):
        await response_send_message(
            ctx,
            content=get("begin_game.already_in_game_description"),
            ephemeral=True,
            delete_after=EPHEMERAL_DELETE_AFTER,
        )
        return None
    me = ctx.guild.me
    channel_perms = ctx.channel.permissions_for(me)
    if not (channel_perms.create_private_threads and channel_perms.send_messages):
        await response_send_message(
            ctx,
            content=format_user_error_message("missing_permissions"),
            ephemeral=True,
        )
        return None
    if ctx.channel.type in [
        discord.ChannelType.public_thread,
        discord.ChannelType.private_thread,
    ]:
        await response_send_message(
            ctx,
            content=format_user_error_message("invalid_channel"),
            ephemeral=True,
        )
        return None

    if ctx.guild is not None:
        pc = await run_in_thread(
            get_container().guilds_repository.get_playcord_channel_id,
            ctx.guild.id,
        )
        if pc is not None and ctx.channel.id != pc:
            await response_send_message(
                ctx,
                content=fmt("playcord.wrong_channel", channel=f"<#{pc}>"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return None

    await response_send_message(
        ctx,
        **container_send_kwargs(LoadingContainer().remove_footer()),
    )
    game_overview_message = await ctx.original_response()
    try:
        creator_row = await run_in_thread(
            get_container().players_repository.get_player,
            ctx.user.id,
            discord_user_db_label(ctx.user),
        )
        interface = MatchmakingInterface(
            ctx.user,
            game_type,
            game_overview_message,
            rated=rated,
            private=private,
            creator_db_player=creator_row,
        )
        if interface.failed is not None:
            await game_overview_message.edit(
                content=str(interface.failed),
                view=None,
                attachments=[],
            )
            return None
        await interface.update_embed()
        from playcord.infrastructure.analytics_client import EventType, register_event

        register_event(
            EventType.MATCHMAKING_STARTED,
            user_id=ctx.user.id,
            guild_id=ctx.guild.id if ctx.guild else None,
            game_type=game_type,
            metadata={"lobby_message_id": game_overview_message.id},
        )
        return interface
    except Exception as exc:
        f_log.exception("begin_game failed for game_type=%r", game_type)
        await report(
            ctx,
            exc,
            surface=ErrorSurface.SLASH,
            translator=(
                getattr(getattr(ctx, "client", None), "container", None).translator
                if getattr(getattr(ctx, "client", None), "container", None) is not None
                else None
            ),
            status_message=game_overview_message,
        )
        return None


async def add_matchmaking_bot(ctx: discord.Interaction, difficulty: str) -> bool:
    f_log = log.getChild("command.add_matchmaking_bot")
    f_log.debug(
        "add_matchmaking_bot called by user=%s difficulty=%r",
        getattr(ctx.user, "id", None),
        difficulty,
    )

    async def _send(message: str) -> None:
        if ctx.response.is_done():
            await followup_send(ctx, message, ephemeral=True)
        else:
            await response_send_message(ctx, message, ephemeral=True)

    mm_by_user = matchmaking_by_user_id()
    if ctx.user.id not in mm_by_user:
        await _send(get("settings.not_in_matchmaking"))
        return False

    matchmaker: MatchmakingInterface = mm_by_user[ctx.user.id]
    if matchmaker.creator.id != ctx.user.id:
        await _send(get("settings.only_creator"))
        return False

    is_matchmaker_rated = matchmaker.rated
    result = matchmaker.add_bot(difficulty)
    if result is not None:
        f_log.warning(
            "add_matchmaking_bot failed for user=%s difficulty=%r result=%r",
            ctx.user.id,
            difficulty,
            result,
        )
        await _send(result)
        return False

    await matchmaker.update_embed()
    if is_matchmaker_rated:  # Only send warning if it actually changed something
        await _send(get("queue.bot_rated_forced"))
    f_log.info(
        "add_matchmaking_bot succeeded for user=%s difficulty=%r",
        ctx.user.id,
        difficulty,
    )
    return True


async def handle_move(
    ctx: discord.Interaction,
    name,
    arguments,
) -> None:
    from playcord.infrastructure.analytics_client import EventType, register_event

    f_log = log.getChild("command.move")
    f_log.debug(
        "handle_move called by user=%s name=%r args=%r",
        getattr(ctx.user, "id", None),
        name,
        arguments,
    )

    requested_group = getattr(
        getattr(getattr(ctx, "command", None), "parent", None),
        "name",
        None,
    )

    def _track_move_rejected(
        reason: str,
        *,
        game_type: str | None = None,
        match_id: int | None = None,
    ) -> None:
        register_event(
            EventType.MOVE_REJECTED,
            user_id=getattr(getattr(ctx, "user", None), "id", None),
            guild_id=ctx.guild.id if getattr(ctx, "guild", None) else None,
            game_type=game_type or requested_group,
            match_id=match_id,
            metadata={
                "reason": reason,
                "move_name": name,
                "command_group": requested_group,
                "source": "handle_move",
            },
        )

    if ctx.channel.type != discord.ChannelType.private_thread:
        _track_move_rejected("wrong_channel")
        await followup_send(
            ctx,
            content=(
                f"{get('move.invalid_context_title')}. "
                f"{get('move.invalid_context_description')}"
            ),
            ephemeral=True,
            delete_after=EPHEMERAL_DELETE_AFTER,
        )
        return
    reg = get_container().registry
    if ctx.channel.id not in reg.games_by_thread_id:
        _track_move_rejected("no_active_game")
        await followup_send(
            ctx,
            content=(
                f"{get('move.invalid_context_title')}. "
                f"{get('move.no_active_game_description')}"
            ),
            ephemeral=True,
            delete_after=EPHEMERAL_DELETE_AFTER,
        )
        return
    active_game = reg.games_by_thread_id[ctx.channel.id]
    command_parent = getattr(getattr(ctx, "command", None), "parent", None)
    requested_game_type = getattr(command_parent, "name", None)
    if requested_game_type and requested_game_type != active_game.game_type:
        _track_move_rejected(
            "wrong_game_type",
            game_type=active_game.game_type,
            match_id=getattr(active_game, "game_id", None),
        )
        await followup_send(
            ctx,
            content=(
                f"{get('move.invalid_context_title')}. "
                f"{fmt('move.wrong_game_type_description', game=active_game.game_type)}"
            ),
            ephemeral=True,
            delete_after=EPHEMERAL_DELETE_AFTER,
        )
        return
    work_args = dict(arguments)
    work_args.pop("ctx", None)
    arguments = {a: await decode_discord_arguments(work_args[a]) for a in work_args}
    reg.autocomplete_cache[ctx.channel.id] = {}
    f_log.info(
        "Dispatching command input for user=%s game_id=%s name=%r args=%r",
        getattr(ctx.user, "id", None),
        ctx.channel.id,
        name,
        arguments,
    )
    await active_game.submit_command_input(
        ctx,
        command_name=name,
        arguments=arguments,
    )


async def handle_autocomplete(
    ctx: discord.Interaction,
    function,
    current: str,
    argument,
) -> list[Choice[str]]:
    try:
        reg = get_container().registry
        runtime = reg.games_by_thread_id[ctx.channel.id]
    except KeyError:
        return [
            app_commands.Choice(name=get("autocomplete.no_game_in_channel"), value=""),
        ]
    if getattr(runtime, "ending_game", False):
        return [app_commands.Choice(name=get("autocomplete.game_finished"), value="-")]
    player = await run_in_thread(
        get_container().players_repository.get_player,
        ctx.user.id,
        discord_user_db_label(ctx.user),
    )
    move = next((m for m in runtime.plugin.metadata.moves if m.name == function), None)
    if move is None:
        return [
            app_commands.Choice(name=get("autocomplete.function_missing"), value=""),
        ]
    option = next((opt for opt in move.options if opt.name == argument), None)
    if option is None or not option.autocomplete:
        return []
    autocomplete = option.autocomplete
    if isinstance(autocomplete, HandlerRef):
        callback = getattr(runtime.plugin, autocomplete.name, None)
    elif isinstance(autocomplete, str):
        callback = getattr(runtime.plugin, autocomplete, None)
    elif callable(autocomplete):
        binder = getattr(autocomplete, "__get__", None)
        callback = (
            binder(runtime.plugin, type(runtime.plugin))
            if callable(binder) and getattr(autocomplete, "__self__", None) is None
            else autocomplete
        )
    else:
        callback = None
    if not callable(callback):
        return [
            app_commands.Choice(name=get("autocomplete.function_missing"), value=""),
        ]

    player_options = callback(
        actor=player,
        current=current,
        ctx=runtime.build_context(),
    )

    valid_player_options = []
    for o in player_options:
        if not o:
            continue
        label, value = o
        if current.lower() in label.lower():
            valid_player_options.append([label, value])
    final_autocomplete = sorted(
        valid_player_options,
        key=lambda x: _autocomplete_sort_key(x[0], current),
    )
    return [
        app_commands.Choice(name=ac_option[0], value=ac_option[1])
        for ac_option in final_autocomplete
    ]
