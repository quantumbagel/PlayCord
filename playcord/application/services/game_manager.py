"""Main-driven game runtime."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode
from uuid import uuid4

import discord

from playcord.api import (
    AutoForfeit,
    BinaryAsset,
    ButtonInput,
    CommandInput,
    DeleteMessage,
    GameContext,
    GameInput,
    GameInputSpec,
    InputMode,
    InputSource,
    InputTimeout,
    MessageLayout,
    MessagePurpose,
    MessageTarget,
    OwnedMessage,
    RuntimeGame,
    SelectInput,
    UpsertMessage,
)
from playcord.api.handlers import HandlerRef, HandlerSpec
from playcord.application.runtime_context import get_container
from playcord.application.services import replay_viewer
from playcord.core.errors import ConfigurationError
from playcord.infrastructure.constants import (
    BUTTON_PREFIX_GAME_MOVE,
    BUTTON_PREFIX_GAME_SELECT,
    BUTTON_PREFIX_PEEK,
    BUTTON_PREFIX_SPECTATE,
    EPHEMERAL_DELETE_AFTER,
)
from playcord.infrastructure.db_thread import run_in_thread
from playcord.infrastructure.locale import get
from playcord.infrastructure.logging import get_logger
from playcord.presentation.interactions.helpers import followup_send
from playcord.presentation.ui.containers import chunk_text_display_lines

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

log = get_logger("game.runtime")


@dataclass(slots=True)
class PendingInputRequest:
    request_id: str
    players: tuple[Any, ...]
    inputs: tuple[GameInputSpec, ...]
    mode: InputMode
    min_responses: int
    future: asyncio.Future[Any]
    responses: dict[int, GameInput] = field(default_factory=dict)
    bot_tasks: list[asyncio.Task[Any]] = field(default_factory=list)

    @property
    def player_ids(self) -> set[int]:
        return {int(player.id) for player in self.players}

    @property
    def input_by_id(self) -> dict[str, GameInputSpec]:
        return {spec.id: spec for spec in self.inputs}

    @property
    def command_inputs(self) -> tuple[CommandInput, ...]:
        return tuple(spec for spec in self.inputs if isinstance(spec, CommandInput))

    def missing_players(self) -> tuple[Any, ...]:
        responded = set(self.responses)
        return tuple(
            player for player in self.players if int(player.id) not in responded
        )


def _resolve_callback(
    plugin: RuntimeGame,
    spec: HandlerSpec,
    default_attr: str | None = None,
) -> Callable[..., Any]:
    if isinstance(spec, HandlerRef):
        spec = spec.name
    if isinstance(spec, str):
        resolved = getattr(plugin, spec, None)
        if callable(resolved):
            return resolved
        msg = f"Configured callback {spec!r} was not found on {type(plugin).__name__}"
        raise ConfigurationError(msg)
    if callable(spec):
        binder = getattr(spec, "__get__", None)
        if callable(binder) and getattr(spec, "__self__", None) is None:
            return binder(plugin, type(plugin))
        return spec
    if default_attr:
        fallback = getattr(plugin, default_attr, None)
        if callable(fallback):
            return fallback
    msg = f"Missing callback configuration on {type(plugin).__name__}"
    raise ConfigurationError(msg)


class RuntimeView(discord.ui.LayoutView):
    """Game UI: components v2 (LayoutView + Container), like lobbies and help."""

    def __init__(self) -> None:
        super().__init__(timeout=None)


class GameManager:
    """Owns a live game instance, Discord messages, and pending input requests."""

    def __init__(
        self,
        *,
        game_type: str,
        plugin_class: type[RuntimeGame],
        overview_message: discord.Message,
        creator: discord.abc.User,
        players: list[Any],
        rated: bool,
        match_id: int,
        match_public_code: str,
        match_options: dict[str, Any] | None = None,
        thread: discord.Thread | None = None,
    ) -> None:
        self.game_type = game_type
        self.plugin_class = plugin_class
        self.plugin = plugin_class(
            players=list(players),
            match_options=match_options or {},
        )
        self.plugin._bind_runtime(self)
        self.game = self.plugin
        self.creator = creator
        self.players = list(players)
        self.rated = rated
        self.game_id = match_id
        self.match_public_code = match_public_code
        self.match_options = dict(match_options or {})
        self.status_message = overview_message
        self.thread = thread
        self.owned_messages: dict[str, discord.Message] = {}
        self.owned_message_purposes: dict[str, str] = {}
        self.player_roles: dict[int, Any] = {}
        self.ending_game = False
        self._interrupt_started = False
        self.forfeited_player_ids: set[int] = set()
        self.logger = log.getChild(game_type)
        self._pending: PendingInputRequest | None = None
        self._request_lock = asyncio.Lock()
        self._main_task: asyncio.Task[Any] | None = None
        self.rematch_view_factory: Any = None

    async def setup(self) -> None:
        if self.thread is None:
            thread_name = f"{self.plugin.metadata.name} - {self.match_public_code}"
            self.thread = await self.status_message.create_thread(name=thread_name)
        reg = get_container().registry
        guild = getattr(self.status_message, "guild", None)

        roles_repo = get_container().roles_repository
        self.player_roles = (
            await run_in_thread(
                roles_repo.get_role_assignments,
                self.game_id,
            )
            or {}
        )

        for player in self.players:
            player_id = getattr(player, "id", None)
            if player_id is not None:
                reg.user_to_game[int(player_id)] = self
            if (
                not getattr(player, "is_bot", False)
                and hasattr(self.thread, "add_user")
                and guild is not None
                and player_id is not None
            ):
                try:
                    member = await guild.fetch_member(int(player_id))
                    await self.thread.add_user(member)
                except Exception:
                    self.logger.debug("Failed to add player %s to thread", player_id)
        reg.games_by_thread_id[self.thread.id] = self
        await self._record_initial_replay_state_async()
        await self._show_started_overview()
        self._main_task = asyncio.create_task(self._run_main())

    async def _show_started_overview(self) -> None:
        text = f"Currently playing {self.plugin.metadata.name}"
        try:
            await self.status_message.edit(content=text, view=None, attachments=[])
        except discord.errors.HTTPException as e:
            if (
                e.code == 50035
            ):  # Invalid Form Body - likely IS_COMPONENTS_V2 flag issue
                self.logger.debug(
                    "Cannot update content on message with MessageFlags.IS_COMPONENTS_V2"
                )
            else:
                self.logger.exception("Failed to update started overview")
        except Exception:
            self.logger.exception("Failed to update started overview")

    async def _run_main(self) -> None:
        try:
            outcome = await self.plugin.main()
        except AutoForfeit as exc:
            outcome = self.plugin.outcome_for_forfeit(exc.players, reason="timeout")
        except asyncio.CancelledError:
            return
        except Exception as exc:
            from playcord.presentation.interactions.error import (
                ErrorSurface,
                report_runtime_error,
            )

            await report_runtime_error(
                exc,
                surface=ErrorSurface.RUNTIME,
                interface=self,
                logger=self.logger,
                thread=self.thread,
                status_message=self.status_message,
            )
            return
        if outcome is not None:
            await self.finish(outcome)

    def build_context(self) -> GameContext:
        owned = []
        for key, message in self.owned_messages.items():
            purpose = self.owned_message_purposes.get(
                key,
                "board" if key == "board" else "overview",
            )
            owned.append(
                OwnedMessage(
                    key=key,
                    purpose=purpose,
                    discord_message_id=message.id,
                    channel_id=message.channel.id,
                    metadata={},
                ),
            )
        roles: dict[int, str] = {}
        for player_id, role_info in self.player_roles.items():
            if isinstance(role_info, (tuple, list)) and role_info:
                roles[int(player_id)] = str(role_info[0])
            else:
                roles[int(player_id)] = str(role_info)
        return GameContext(
            match_id=self.game_id,
            game_key=self.game_type,
            players=list(self.players),
            match_options=dict(self.match_options),
            owned_messages=owned,
            latest_overview=getattr(self.status_message, "content", None),
            roles=roles,
        )

    async def update_message(
        self,
        message_id: str,
        layout: MessageLayout,
        *,
        target: MessageTarget = "thread",
        purpose: MessagePurpose = "board",
    ) -> None:
        await self._apply_actions(
            (
                UpsertMessage(
                    target=target, key=message_id, layout=layout, purpose=purpose
                ),
            ),
        )

    async def delete_message(
        self,
        message_id: str,
        *,
        target: MessageTarget = "thread",
    ) -> None:
        await self._apply_actions((DeleteMessage(target=target, key=message_id),))

    async def request_input(
        self,
        *,
        players: Sequence[Any],
        inputs: Sequence[GameInputSpec],
        timeout: float,
        mode: InputMode = "first",
        min_responses: int | None = None,
        message_id: str | None = None,
        layout: MessageLayout | None = None,
        target: MessageTarget = "thread",
        purpose: MessagePurpose = "board",
        auto_remove_on_timeout: bool = False,
        send_timeout_warning: bool = True,
    ) -> GameInput | list[GameInput] | InputTimeout:
        if not players:
            msg = "request_input requires at least one player"
            raise ValueError(msg)
        if not inputs:
            msg = "request_input requires at least one input"
            raise ValueError(msg)
        async with self._request_lock:
            if self._pending is not None:
                msg = "A game input request is already pending"
                raise RuntimeError(msg)
            loop = asyncio.get_running_loop()
            request = PendingInputRequest(
                request_id=uuid4().hex[:12],
                players=tuple(players),
                inputs=tuple(inputs),
                mode=mode,
                min_responses=(
                    min_responses
                    if min_responses is not None
                    else (1 if mode == "first" else len(players))
                ),
                future=loop.create_future(),
            )
            self._pending = request
            if layout is not None and message_id is not None:
                request_layout = self._layout_with_request_inputs(
                    layout, request.inputs
                )
                await self.update_message(
                    message_id,
                    request_layout,
                    target=target,
                    purpose=purpose,
                )
            self._schedule_bot_inputs(request)
        try:
            return await asyncio.wait_for(request.future, timeout=timeout)
        except TimeoutError:
            timeout_result = InputTimeout(
                request_id=request.request_id,
                players=tuple(request.players),
                missing_players=request.missing_players(),
                responses=dict(request.responses),
            )
            if send_timeout_warning or auto_remove_on_timeout:
                await self._handle_input_timeout(
                    timeout_result,
                    auto_remove=auto_remove_on_timeout,
                    send_warning=send_timeout_warning,
                )
            return timeout_result
        finally:
            async with self._request_lock:
                if self._pending is request:
                    self._pending = None
                for task in request.bot_tasks:
                    task.cancel()

    @staticmethod
    def _layout_with_request_inputs(
        layout: MessageLayout,
        inputs: tuple[GameInputSpec, ...],
    ) -> MessageLayout:
        buttons = layout.buttons or tuple(
            spec for spec in inputs if isinstance(spec, ButtonInput)
        )
        selects = layout.selects or tuple(
            spec for spec in inputs if isinstance(spec, SelectInput)
        )
        return MessageLayout(
            content=layout.content,
            buttons=buttons,
            selects=selects,
            attachments=layout.attachments,
            button_row_width=layout.button_row_width,
        )

    def _schedule_bot_inputs(self, request: PendingInputRequest) -> None:
        for player in request.players:
            if getattr(player, "is_bot", False):
                request.bot_tasks.append(
                    asyncio.create_task(self._submit_bot_input(player, request))
                )

    async def _submit_bot_input(
        self, player: Any, request: PendingInputRequest
    ) -> None:
        await asyncio.sleep(0.5)
        if self.ending_game:
            return
        difficulty = str(getattr(player, "bot_difficulty", None) or "easy")
        definition = self.plugin.metadata.bots.get(difficulty)
        if definition is None and self.plugin.metadata.bots:
            definition = next(iter(self.plugin.metadata.bots.values()))
        if definition is None:
            return
        callback = _resolve_callback(self.plugin, definition.callback, "bot_input")
        decision = callback(player, request=request, ctx=self.build_context())
        if inspect.isawaitable(decision):
            decision = await decision
        if not decision:
            return
        if isinstance(decision, str):
            input_id = decision
            arguments: dict[str, Any] = {}
            values: tuple[str, ...] = ()
        else:
            input_id = str(decision.get("input_id", ""))
            arguments = dict(decision.get("arguments", {}) or {})
            values = tuple(str(value) for value in decision.get("values", ()) or ())
        await self._accept_input(
            actor=player,
            request_id=request.request_id,
            input_id=input_id,
            source="bot",
            arguments=arguments,
            values=values,
            interaction=None,
        )

    async def submit_component_input(
        self,
        ctx: discord.Interaction,
        *,
        payload: str,
        source: InputSource,
    ) -> None:
        request_id, input_id, arguments = self.decode_input_payload(payload)
        values: tuple[str, ...] = ()
        if source == "select":
            values = tuple(str(value) for value in (ctx.data or {}).get("values") or ())
        actor = self._player_by_id(getattr(ctx.user, "id", None))
        await self._accept_input(
            actor=actor,
            request_id=request_id,
            input_id=input_id,
            source=source,
            arguments=arguments,
            values=values,
            interaction=ctx,
        )

    async def submit_command_input(
        self,
        ctx: discord.Interaction,
        *,
        command_name: str,
        arguments: dict[str, Any],
    ) -> None:
        async with self._request_lock:
            pending = self._pending
            command_input = None
            if pending is not None:
                command_input = next(
                    (
                        spec
                        for spec in pending.command_inputs
                        if spec.command_name == command_name
                    ),
                    None,
                )
            request_id = pending.request_id if pending is not None else ""
            input_id = command_input.id if command_input is not None else ""
        actor = self._player_by_id(getattr(ctx.user, "id", None))
        accepted = await self._accept_input(
            actor=actor,
            request_id=request_id,
            input_id=input_id,
            source="command",
            arguments=arguments,
            values=(),
            interaction=ctx,
        )
        if accepted:
            await followup_send(
                ctx,
                "Input received.",
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )

    async def _accept_input(
        self,
        *,
        actor: Any | None,
        request_id: str,
        input_id: str,
        source: InputSource,
        arguments: dict[str, Any],
        values: tuple[str, ...],
        interaction: discord.Interaction | None,
    ) -> bool:
        async with self._request_lock:
            pending = self._pending
            if pending is None or pending.request_id != request_id:
                await self._send_invalid_input(
                    interaction, "That input is not valid right now."
                )
                return False
            if actor is None or int(actor.id) not in pending.player_ids:
                await self._send_invalid_input(
                    interaction, get("permissions.not_participant")
                )
                return False
            spec = pending.input_by_id.get(input_id)
            if spec is None or not self._source_matches_spec(source, spec):
                await self._send_invalid_input(
                    interaction, "That input is not valid right now."
                )
                return False
            player_id = int(actor.id)
            game_input = GameInput(
                request_id=request_id,
                input_id=input_id,
                actor=actor,
                source=source,
                arguments=dict(arguments),
                values=values,
                ctx=self.build_context(),
            )
            pending.responses[player_id] = game_input
            if pending.future.done():
                return True
            if pending.mode == "first":
                pending.future.set_result(game_input)
            elif len(pending.responses) >= pending.min_responses:
                ordered = [
                    pending.responses[int(player.id)]
                    for player in pending.players
                    if int(player.id) in pending.responses
                ]
                pending.future.set_result(ordered)
        return True

    @staticmethod
    def _source_matches_spec(source: InputSource, spec: GameInputSpec) -> bool:
        return (
            (source == "button" and isinstance(spec, ButtonInput))
            or (source == "select" and isinstance(spec, SelectInput))
            or (source == "command" and isinstance(spec, CommandInput))
            or source == "bot"
        )

    async def _send_invalid_input(
        self,
        interaction: discord.Interaction | None,
        message: str,
    ) -> None:
        if interaction is None:
            return
        await followup_send(
            interaction,
            message,
            ephemeral=True,
            delete_after=EPHEMERAL_DELETE_AFTER,
        )

    async def record_move(
        self,
        actor: Any,
        name: str,
        arguments: dict[str, Any],
        *,
        source: InputSource,
        input_id: str | None = None,
    ) -> None:
        payload = dict(arguments)
        if input_id is not None:
            payload["input_id"] = input_id
        await self._record_move(actor, name, payload, source=source)

    def log_replay_event(self, event_type: str, **payload: Any) -> None:
        self._plugin_replay_hook(event_type, payload)

    async def _handle_input_timeout(
        self,
        timeout_result: InputTimeout,
        *,
        auto_remove: bool = False,
        send_warning: bool = True,
    ) -> None:
        """Handle input timeout by sending warnings and optionally removing players."""
        import time

        missing_players = timeout_result.missing_players
        if not missing_players:
            return

        if send_warning and self.thread is not None:
            current_time = int(time.time())
            timestamp_str = f"<t:{current_time}:R>"
            warning_msg = get(
                "timeout.warning",
                default="⏱️ Input timeout for {players} ({timestamp}).",
            ).format(
                players=", ".join(p.mention for p in missing_players),
                timestamp=timestamp_str,
            )
            try:
                await self.thread.send(warning_msg, delete_after=EPHEMERAL_DELETE_AFTER)
            except Exception:
                self.logger.exception("Failed to send timeout warning")

        if auto_remove:
            for player in missing_players:
                if int(player.id) not in self.forfeited_player_ids:
                    self.forfeited_player_ids.add(int(player.id))
            if len(self.forfeited_player_ids) >= len(self.plugin.players):
                outcome = self.plugin.outcome_for_forfeit(
                    missing_players,
                    reason="timeout",
                )
                await self.finish(outcome)

    async def forfeit_player(
        self, player_or_id: Any, *, reason: str = "forfeit"
    ) -> Any:
        player = (
            self._player_by_id(player_or_id)
            if not hasattr(player_or_id, "id")
            else player_or_id
        )
        if player is None:
            return get("forfeit.not_in_game")
        self.forfeited_player_ids.add(int(player.id))
        outcome = self.plugin.outcome_for_forfeit([player], reason=reason)
        await self.finish(outcome)
        return f"{player.mention} forfeited."

    async def handle_spectate(self, ctx: discord.Interaction) -> None:
        if self.thread is not None:
            await self.thread.add_user(ctx.user)
        await followup_send(ctx, get("success.spectating"), ephemeral=True)

    async def handle_peek(self, ctx: discord.Interaction) -> None:
        text: str | None = None
        peek_callback = getattr(self.plugin.metadata, "peek_callback", None)
        if peek_callback:
            callback = _resolve_callback(self.plugin, peek_callback)
            value = callback(ctx=self.build_context())
            if inspect.isawaitable(value):
                value = await value
            if value is not None:
                text = str(value).strip() or None
        if text is None:
            text = get("success.already_participant")
        await followup_send(ctx, text, ephemeral=True)

    async def finish(self, outcome: Any) -> None:
        if self.ending_game:
            return
        self.ending_game = True
        async with self._request_lock:
            pending = self._pending
            self._pending = None
            if pending is not None and not pending.future.done():
                pending.future.cancel()
        task = self._main_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
        from playcord.application.services.match_lifecycle import finish_match

        await finish_match(self, outcome)

    async def _record_move(
        self,
        actor: Any,
        name: str,
        arguments: dict[str, Any],
        *,
        source: InputSource,
    ) -> None:
        def _sync_record() -> None:
            try:
                matches = get_container().matches_repository
                replays = get_container().replays_repository
                next_number = matches.get_move_count(self.game_id) + 1
                matches.record_move(
                    self.game_id,
                    (
                        int(getattr(actor, "id", 0))
                        if not getattr(actor, "is_bot", False)
                        else None
                    ),
                    next_number,
                    {"name": name, "arguments": arguments, "source": source},
                    is_game_affecting=True,
                    kind="system" if source == "bot" else "move",
                )
                actor_id = getattr(actor, "id", None)
                replay_event: dict[str, Any] = {
                    "type": "move",
                    "move_number": next_number,
                    "command_name": name,
                    "arguments": dict(arguments),
                    "source": source,
                }
                if actor_id is not None:
                    replay_event["user_id"] = int(actor_id)
                replays.append_replay_dict(self.game_id, replay_event)
                replay_viewer.invalidate_match_cache(self.game_id)
            except Exception:
                self.logger.exception("Failed to record move match_id=%s", self.game_id)

        await run_in_thread(_sync_record)

    def _plugin_replay_hook(self, event_type: str, payload: dict[str, Any]) -> None:
        def _write() -> None:
            try:
                body: dict[str, Any] = {"type": event_type, **dict(payload)}
                get_container().replays_repository.append_replay_dict(
                    self.game_id,
                    body,
                )
                replay_viewer.invalidate_match_cache(self.game_id)
            except Exception:
                self.logger.exception(
                    "Failed to append plugin replay event match_id=%s type=%s",
                    self.game_id,
                    event_type,
                )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _write()
            return
        loop.create_task(run_in_thread(_write))

    async def _record_initial_replay_state_async(self) -> None:
        try:
            replay_state = self.plugin.initial_replay_state(self.build_context())
        except Exception:
            self.logger.exception(
                "initial_replay_state failed match_id=%s",
                self.game_id,
            )
            return
        if replay_state is None:
            return
        payload = {
            "game_key": replay_state.game_key,
            "match_options": dict(replay_state.match_options),
            "move_index": int(replay_state.move_index),
            "state": replay_state.state,
        }

        def _write() -> None:
            try:
                get_container().replays_repository.append_replay_dict(
                    self.game_id,
                    {"type": "replay_init", "state": payload},
                )
                replay_viewer.invalidate_match_cache(self.game_id)
            except Exception:
                self.logger.exception(
                    "Failed to record replay_init match_id=%s",
                    self.game_id,
                )

        await run_in_thread(_write)

    async def _apply_actions(self, actions: tuple[Any, ...]) -> None:
        for action in actions:
            if isinstance(action, UpsertMessage):
                await self._upsert_message(action)
            elif isinstance(action, DeleteMessage):
                await self._delete_owned_message(action.key)

    async def _upsert_message(self, action: UpsertMessage) -> None:
        if action.target == "overview":
            view = self._build_overview_view(action.layout)
            if view is not None:
                await self._safe_edit_message(
                    self.status_message,
                    view=view,
                    attachments=[],
                )
            else:
                await self._safe_edit_message(
                    self.status_message,
                    content=action.layout.content,
                    attachments=[],
                )
            return
        if self.thread is None:
            return
        existing = self.owned_messages.get(action.key)
        view = self._build_view(action.layout)
        files = [
            discord.File(fp=asset_to_file(asset), filename=asset.filename)
            for asset in action.layout.attachments
        ]
        if existing is None:
            if view is None:
                message = await self.thread.send(
                    content=action.layout.content,
                    files=files or None,
                )
            else:
                send_kw: dict[str, Any] = {"view": view}
                if files:
                    send_kw["files"] = files
                message = await self.thread.send(**send_kw)
            self.owned_messages[action.key] = message
            self.owned_message_purposes[action.key] = action.purpose
            return
        if view is None:
            await self._safe_edit_message(
                existing,
                content=action.layout.content,
                attachments=files,
            )
        else:
            await self._safe_edit_message(existing, view=view, attachments=files)
        self.owned_message_purposes[action.key] = action.purpose

    async def _delete_owned_message(self, key: str) -> None:
        message = self.owned_messages.pop(key, None)
        self.owned_message_purposes.pop(key, None)
        if message is None:
            return
        try:
            await message.delete()
        except discord.HTTPException:
            self.logger.debug("Failed to delete owned message key=%s", key)

    async def _safe_edit_message(
        self,
        message: discord.Message | None,
        /,
        **kwargs: Any,
    ) -> None:
        if message is None:
            return
        edit_kwargs = dict(kwargs)
        try:
            if (
                self._message_has_components_v2(message)
                and edit_kwargs.get("content") is not None
            ):
                self.logger.debug(
                    "Dropping content from edit because message uses components v2",
                )
                edit_kwargs.pop("content", None)
            await message.edit(**edit_kwargs)
        except Exception as exc:
            if (
                edit_kwargs.get("content") is not None
                and self._is_http_exception(exc)
                and self._is_components_v2_content_error(exc)
            ):
                retry_kwargs = dict(edit_kwargs)
                retry_kwargs.pop("content", None)
                try:
                    await message.edit(**retry_kwargs)
                    return
                except Exception:
                    self.logger.exception(
                        "Failed to edit message %s",
                        getattr(message, "id", None),
                    )
                    return
            self.logger.exception(
                "Failed to edit message %s",
                getattr(message, "id", None),
            )

    @staticmethod
    def _is_http_exception(exc: Exception) -> bool:
        http_exception = getattr(discord, "HTTPException", None)
        return isinstance(http_exception, type) and isinstance(exc, http_exception)

    @staticmethod
    def _is_components_v2_content_error(exc: Exception) -> bool:
        text = str(exc)
        return "IS_COMPONENTS_V2" in text and "content" in text.lower()

    @staticmethod
    def _message_has_components_v2(message: discord.Message) -> bool:
        flags = getattr(message, "flags", None)
        if flags is None:
            return False
        marker = getattr(flags, "is_components_v2", None)
        if marker is not None:
            return bool(marker)
        flag_const = getattr(discord.MessageFlags, "IS_COMPONENTS_V2", None)
        flags_val = getattr(flags, "value", None)
        if flag_const is None or flags_val is None:
            return False
        try:
            return (int(flags_val) & int(flag_const)) != 0
        except (TypeError, ValueError):
            return False

    def _build_interactive_view(
        self,
        layout: MessageLayout,
        trailing_buttons: tuple[discord.ui.Button, ...] = (),
    ) -> RuntimeView | None:
        has_body = bool((layout.content or "").strip())
        has_game = bool(layout.buttons or layout.selects)
        has_trail = bool(trailing_buttons)
        if not has_body and not has_game and not has_trail:
            return None

        view = RuntimeView()
        container = discord.ui.Container()
        for chunk in chunk_text_display_lines(layout.content or ""):
            container.add_item(discord.ui.TextDisplay(chunk))
        if has_body and (has_game or has_trail):
            container.add_item(discord.ui.Separator())
        width = layout.button_row_width
        if width and width > 0 and layout.buttons:
            row_buttons: list[discord.ui.Button] = []
            for button in layout.buttons:
                row_buttons.append(self._make_button(button))
                if len(row_buttons) >= width:
                    ar = discord.ui.ActionRow()
                    for item in row_buttons:
                        ar.add_item(item)
                    container.add_item(ar)
                    row_buttons = []
            if row_buttons:
                ar = discord.ui.ActionRow()
                for item in row_buttons:
                    ar.add_item(item)
                container.add_item(ar)
        else:
            for button in layout.buttons:
                ar = discord.ui.ActionRow()
                ar.add_item(self._make_button(button))
                container.add_item(ar)
        for select in layout.selects:
            ar = discord.ui.ActionRow()
            ar.add_item(self._make_select(select))
            container.add_item(ar)
        if has_trail:
            if has_game:
                container.add_item(discord.ui.Separator())
            tr = discord.ui.ActionRow()
            for btn in trailing_buttons:
                tr.add_item(btn)
            container.add_item(tr)
        view.add_item(container)
        return view

    def _build_view(self, layout: MessageLayout) -> RuntimeView | None:
        return self._build_interactive_view(layout, ())

    def _build_overview_view(self, layout: MessageLayout) -> RuntimeView | None:
        if self.thread is None:
            return self._build_interactive_view(layout, ())
        trail = (
            discord.ui.Button(
                label="Spectate",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{BUTTON_PREFIX_SPECTATE}{self.thread.id}",
            ),
            discord.ui.Button(
                label="Peek",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{BUTTON_PREFIX_PEEK}{self.thread.id}",
            ),
        )
        return self._build_interactive_view(layout, trail)

    def _make_button(self, spec: ButtonInput) -> discord.ui.Button:
        style_map = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger,
        }
        request_id = self._request_id_for_input(spec.id)
        payload = urlencode(
            {
                "request_id": request_id,
                "input_id": spec.id,
                **{f"arg_{key}": str(value) for key, value in spec.arguments.items()},
            },
        )
        resource_id = self.thread.id if self.thread is not None else self.game_id
        custom_id = f"{BUTTON_PREFIX_GAME_MOVE}{resource_id}/{payload}"
        label = spec.label if (spec.label and spec.label.strip()) else "\u200b"
        return discord.ui.Button(
            label=label,
            emoji=spec.emoji,
            style=style_map[spec.style],
            custom_id=custom_id,
            disabled=spec.disabled or not request_id,
        )

    def _make_select(self, spec: SelectInput) -> discord.ui.Select:
        request_id = self._request_id_for_input(spec.id)
        payload = urlencode({"request_id": request_id, "input_id": spec.id})
        resource_id = self.thread.id if self.thread is not None else self.game_id
        custom_id = f"{BUTTON_PREFIX_GAME_SELECT}{resource_id}/{payload}"
        options = []
        for option in spec.options:
            opt_label = (
                option.label
                if (option.label and option.label.strip())
                else option.value
            )
            options.append(
                discord.SelectOption(
                    label=opt_label,
                    value=option.value,
                    default=option.default,
                ),
            )
        return discord.ui.Select(
            custom_id=custom_id,
            placeholder=spec.placeholder,
            options=options,
            min_values=spec.min_values,
            max_values=spec.max_values,
            disabled=spec.disabled or not request_id,
        )

    def _request_id_for_input(self, input_id: str) -> str:
        pending = self._pending
        if pending is None:
            return ""
        if input_id not in pending.input_by_id:
            return ""
        return pending.request_id

    def decode_input_payload(self, payload: str) -> tuple[str, str, dict[str, Any]]:
        parsed = parse_qs(payload)
        request_id = parsed.get("request_id", [""])[0]
        input_id = parsed.get("input_id", [""])[0]
        arguments = {
            key.removeprefix("arg_"): values[0]
            for key, values in parsed.items()
            if key.startswith("arg_")
        }
        return request_id, input_id, arguments

    def _player_by_id(self, user_id: Any) -> Any | None:
        try:
            wanted = int(user_id)
        except (TypeError, ValueError):
            return None
        for player in self.players:
            try:
                if int(getattr(player, "id", 0)) == wanted:
                    return player
            except (TypeError, ValueError):
                continue
        return None


def asset_to_file(asset: BinaryAsset):
    from io import BytesIO

    fp = BytesIO(asset.data)
    fp.seek(0)
    return fp
