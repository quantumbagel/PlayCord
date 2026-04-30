"""Discord matchmaking lobby interface implementation."""

import importlib
import typing
from typing import Any

import discord

from playcord.api.metadata import RoleFlow
from playcord.api.plugin import RoleMode, resolve_player_count
from playcord.application.runtime_context import get_container
from playcord.application.services.match_lifecycle import start_match_from_lobby
from playcord.application.services.matchmaker import (
    LobbyRoster,
    lobby_add_bot,
    lobby_ban_phase,
    lobby_base_start_conditions_met,
    lobby_kick_phase,
    lobby_remove_bot,
)
from playcord.application.services.role_management import has_role_support
from playcord.core.player import Player
from playcord.infrastructure.analytics_client import Timer
from playcord.infrastructure.constants import (
    BUTTON_PREFIX_JOIN,
    BUTTON_PREFIX_LEAVE,
    BUTTON_PREFIX_LOBBY_ASSIGN_ROLES,
    BUTTON_PREFIX_READY,
    EPHEMERAL_DELETE_AFTER,
    GAME_TYPES,
    LONG_SPACE_EMBED,
)
from playcord.infrastructure.database.implementation.internal_player import (
    InternalPlayer,
)
from playcord.infrastructure.db_thread import run_in_thread
from playcord.infrastructure.locale import fmt, get
from playcord.infrastructure.logging import get_logger
from playcord.infrastructure.state.user_games import (
    user_in_active_game,
    user_in_active_matchmaking,
)
from playcord.presentation.interactions.contextify import contextify
from playcord.presentation.interactions.helpers import (
    discord_user_db_label,
    followup_send,
    get_shallow_player,
)
from playcord.presentation.ui.containers import (
    CustomContainer,
    LoadingContainer,
    container_send_kwargs,
    container_to_markdown,
)
from playcord.presentation.ui.formatting import (
    column_creator,
    column_elo,
    column_names,
    player_representative,
    player_verification_function,
)
from playcord.presentation.ui.layout_discord import (
    MatchmakingView,
    RematchView,
)
from playcord.presentation.ui.matchmaking_views import MatchmakingLobbyView


class _LobbyCreatorRef:
    """Minimal creator handle after rotation when only DB-backed player rows exist."""

    __slots__ = ("id",)

    def __init__(self, user_id: int) -> None:
        self.id = int(user_id)

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


class MatchmakingInterface:
    """MatchmakingInterface - the class that handles matchmaking for a game, where
    control is promptly handed off to the new GameManager via
    `start_match_from_lobby`.
    """

    def __init__(
        self,
        creator: discord.User,
        game_type: str,
        message: discord.InteractionMessage,
        rated: bool,
        private: bool,
        *,
        creator_db_player: InternalPlayer | None = None,
    ) -> None:

        # Whether the startup of the matchmaking interaction failed
        self.failed = None

        # Game type
        self.game_type = game_type

        # Creator of the game
        self.creator = creator

        # Is the game rated?
        self.rated = rated

        # Whether joining the game is open
        self.private = private

        # Allowed players for whitelist
        creator_row = (
            creator_db_player
            if creator_db_player is not None
            else get_container().players_repository.get_player(
                creator.id,
                discord_user_db_label(creator),
            )
        )
        self._lobby_roster = LobbyRoster.initial(
            {
                creator_row,
            },
        )
        self.whitelist = self._lobby_roster.whitelist
        self.blacklist = self._lobby_roster.blacklist
        self.bots = self._lobby_roster.bots
        self.ready_players = self._lobby_roster.ready_players

        # Game module
        self.module = importlib.import_module(GAME_TYPES[game_type][0])

        # Start the list of queued players with just the creator
        self.queued_players = set(self.whitelist)

        # The message context to edit when making updates
        self.message = message

        if self.queued_players == {
            None,
        }:  # Couldn't get information on the creator, so fail now
            self.failed = (
                f"{get('queue.db_connect_failed_what')} "
                f"{get('queue.db_connect_failed_reason')}"
            )
            return
        reg = get_container().registry
        reg.matchmaking_by_message_id.update({self.message.id: self})
        for player in self.queued_players:
            reg.user_to_matchmaking[MatchmakingInterface._coerce_player_id(player)] = (
                self
            )

        # Game class
        self.game = getattr(self.module, GAME_TYPES[game_type][1])
        self.metadata = self.game.metadata

        self.rated_requested = self.rated
        self._specs = tuple(getattr(self.metadata, "customizable_options", ()) or ())
        self.match_settings: dict[str, str | int] = {
            s.key: s.default for s in self._specs
        }
        self.role_selections: dict[int, str] = {}
        self._sync_rated_flag()

        # Required and maximum players for game
        # TODO: more complex requirements for start/stop

        player_count = resolve_player_count(self.game)
        if player_count is None:  # If no player count is defined, any value is "fine"
            self.player_verification_function = lambda x: True
            self.allowed_players = get("queue.any_players")
        else:
            self.player_verification_function = player_verification_function(
                player_count,
            )
            self.allowed_players = player_representative(player_count)

        self.outcome = (
            None  # Whether the matchmaking was successful (True, None, or False)
        )
        self.logger = get_logger("interfaces.matchmaking").getChild(game_type)

    @property
    def has_bots(self) -> bool:
        return bool(self.bots)

    def _match_settings_are_default(self) -> bool:
        for spec in self._specs:
            if self.match_settings.get(spec.key, spec.default) != spec.default:
                return False
        return True

    def _sync_rated_flag(self) -> None:
        if self.has_bots:
            self.rated = False
            return
        if (
            self._specs
            and getattr(
                self.game,
                "customization_forces_unrated_when_non_default",
                True,
            )
            and not self._match_settings_are_default()
        ):
            self.rated = False
            return
        self.rated = self.rated_requested

    def _reset_ready_state(self) -> None:
        self.ready_players.clear()

    def _base_start_conditions_met(self) -> bool:
        return lobby_base_start_conditions_met(
            bots=self.bots,
            game=self.game,
            metadata=self.metadata,
            queued_players=self.queued_players,
            role_selections=self.role_selections,
            specs=self._specs,
        )

    def _all_humans_ready(self) -> bool:
        human_ids = {p.id for p in self.queued_players}
        return bool(human_ids) and getattr(self, "ready_players", set()) == human_ids

    def _needed_players_display(self) -> str:
        pc = resolve_player_count(self.game)
        if pc is None:
            return get("queue.lobby_needed_any")
        if isinstance(pc, int):
            return str(pc)
        return " or ".join(str(x) for x in sorted(pc))

    def _lobby_view_summary_line(self) -> str:
        """Title line: ready counts only when the lobby can accept Ready toggles."""
        if self._base_start_conditions_met():
            return fmt(
                "queue.lobby_header_ready_phase",
                game=self.metadata.name,
                ready=len(self.ready_players),
                total=len(self.queued_players),
            )
        cur = len(self.all_players())
        needed = self._needed_players_display()
        return fmt(
            "queue.lobby_header_recruiting",
            game=self.metadata.name,
            current=cur,
            needed=needed,
        )

    async def _maybe_auto_start_from_lobby(self) -> bool:
        """When roster + ready checks pass, start immediately (no Start button)."""
        if not self._base_start_conditions_met() or not self._all_humans_ready():
            return False
        busy_players = [q for q in self.queued_players if user_in_active_game(q.id)]
        if busy_players:
            au_log = self.logger.getChild("auto_start")
            au_log.info(
                "auto-start blocked: queued players in another active game lobby=%s busy=%s",
                self.message.id,
                [p.id for p in busy_players],
            )
            self._reset_ready_state()
            return False
        self.outcome = True
        await self.message.edit(
            **container_send_kwargs(LoadingContainer().remove_footer()),
        )
        await start_match_from_lobby(
            self,
            self.game,
            rematch_view_factory=lambda match_id, summary: RematchView(
                match_id,
                summary_text=summary,
            ),
        )
        return True

    def all_players(self) -> list[InternalPlayer | Player]:
        return [*sorted(self.queued_players, key=lambda p: p.id), *self.bots]

    @staticmethod
    def _coerce_player_id(player: Any) -> int:
        player_id = getattr(player, "id", None)
        try:
            return int(player_id)
        except (TypeError, ValueError) as exc:
            msg = f"Player object must expose an int-like id, got {player!r}"
            raise TypeError(
                msg,
            ) from exc

    @staticmethod
    def _find_player_by_id(players: typing.Iterable[Any], user_id: int) -> Any | None:
        for player in players:
            if getattr(player, "id", None) == user_id:
                return player
        return None

    @staticmethod
    def _contains_player_id(players: typing.Iterable[Any], user_id: int) -> bool:
        return MatchmakingInterface._find_player_by_id(players, user_id) is not None

    @staticmethod
    def _discard_player_from_set(players: set[Any], user_id: int) -> Any | None:
        found = MatchmakingInterface._find_player_by_id(players, user_id)
        if found is not None:
            players.discard(found)
        return found

    @staticmethod
    def _pop_active_matchmaking_entry(user_id: int) -> None:
        get_container().registry.user_to_matchmaking.pop(int(user_id), None)

    def _is_queued_player(self, user_id: int) -> bool:
        queued_players = getattr(self, "queued_players", set())
        return MatchmakingInterface._contains_player_id(queued_players, user_id)

    def _add_queued_player(self, player: InternalPlayer | Player) -> None:
        player_id = MatchmakingInterface._coerce_player_id(player)
        queued_players = getattr(self, "queued_players", [])
        if isinstance(queued_players, set):
            try:
                queued_players.add(player)
            except TypeError:
                queued_list = list(queued_players)
                if not MatchmakingInterface._contains_player_id(queued_list, player_id):
                    queued_list.append(player)
                self.queued_players = queued_list
        else:
            if not isinstance(queued_players, list):
                queued_players = list(queued_players)
                self.queued_players = queued_players
            if not MatchmakingInterface._contains_player_id(queued_players, player_id):
                queued_players.append(player)
        get_container().registry.user_to_matchmaking[player_id] = self
        reset_ready = getattr(self, "_reset_ready_state", None)
        if callable(reset_ready):
            reset_ready()

    def _remove_queued_player(self, user_id: int) -> InternalPlayer | Player | None:
        found = MatchmakingInterface._find_player_by_id(self.queued_players, user_id)
        if found is None:
            return None
        if isinstance(self.queued_players, set):
            self.queued_players.discard(found)
        else:
            self.queued_players = [
                player
                for player in self.queued_players
                if getattr(player, "id", None) != user_id
            ]
        MatchmakingInterface._pop_active_matchmaking_entry(user_id)
        role_selections = getattr(self, "role_selections", None)
        if isinstance(role_selections, dict):
            role_selections.pop(user_id, None)
        reset_ready = getattr(self, "_reset_ready_state", None)
        if callable(reset_ready):
            reset_ready()
        return found

    def _rotate_creator_if_needed(self, user_id: int) -> None:
        if user_id == getattr(self.creator, "id", None) and self.queued_players:
            next_creator = next(iter(self.queued_players))
            if isinstance(next_creator, (discord.User, discord.Member)):
                self.creator = next_creator
                return
            nid = getattr(next_creator, "id", None)
            if nid is None:
                return
            guild = getattr(self.message, "guild", None)
            if guild is not None:
                member = guild.get_member(int(nid))
                if member is not None:
                    self.creator = member
                    return
            self.creator = _LobbyCreatorRef(int(nid))

    def add_bot(self, difficulty: str, number: int = 1) -> str | None:
        err = lobby_add_bot(
            self._lobby_roster,
            difficulty,
            game=self.game,
            metadata=self.metadata,
            human_queue_size=len(self.queued_players),
            number=number,
        )
        if err is not None:
            return err
        self._sync_rated_flag()
        getattr(self, "_reset_ready_state", lambda: None)()
        return None

    def remove_bot(self, bot_name: str) -> str | None:
        """Remove a bot by name.
        
        Returns an error message if not found, or None on success.
        """
        err = lobby_remove_bot(self._lobby_roster, bot_name)
        if err is not None:
            return err
        self._sync_rated_flag()
        getattr(self, "_reset_ready_state", lambda: None)()
        return None

    async def callback_lobby_option(self, ctx: discord.Interaction, key: str) -> None:
        """Handle string select for a lobby :attr:`customizable_options` key (creator only)."""
        log = self.logger.getChild("lobby_option")
        if ctx.user.id != self.creator.id:
            await followup_send(
                ctx,
                get("queue.only_creator_lobby_options"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        spec = next((s for s in self._specs if s.key == key), None)
        if spec is None:
            log.warning("unknown lobby option key=%r lobby=%s", key, self.message.id)
            await followup_send(
                ctx,
                get("matchmaking.invalid_interaction"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        raw = (ctx.data.get("values") or [""])[0]
        self.match_settings[key] = spec.coerce(raw)
        preset_values = spec.applied_preset(raw)
        if preset_values:
            for other_spec in self._specs:
                if other_spec.key in preset_values:
                    self.match_settings[other_spec.key] = other_spec.coerce(
                        str(preset_values[other_spec.key]),
                    )
        self._sync_rated_flag()
        getattr(self, "_reset_ready_state", lambda: None)()
        await self.update_embed()
        await followup_send(
            ctx,
            get("queue.lobby_option_updated"),
            ephemeral=True,
            delete_after=EPHEMERAL_DELETE_AFTER,
        )

    async def callback_role_select(
        self,
        ctx: discord.Interaction,
        player_id: int,
    ) -> None:
        """Handle per-player role string select for plugin-owned roles."""
        log = self.logger.getChild("lobby_role_select")
        if ctx.user.id != player_id:
            await followup_send(
                ctx,
                get("queue.role_select_not_yours"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        role_flow = getattr(self.metadata, "role_flow", RoleFlow.none)
        role_mode = getattr(self.metadata, "role_mode", RoleMode.none)

        # Support both new role_flow and legacy role_mode for backward compatibility
        is_selectable = (
            role_flow
            in (
                RoleFlow.selectable,
                RoleFlow.selectable_random,
            )
            or role_mode == RoleMode.chosen
        )

        if not is_selectable:
            log.warning("role select on non-selectable lobby lobby=%s", self.message.id)
            await followup_send(
                ctx,
                get("matchmaking.invalid_interaction"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        raw = (ctx.data.get("values") or [""])[0]
        self.role_selections[player_id] = str(raw)

        # Validate roles if the plugin implements validation
        if hasattr(self.game, "validate_roles"):
            try:
                validation_error = self.game.validate_roles(self.role_selections)
                if validation_error:
                    await followup_send(
                        ctx,
                        f"Invalid role selection: {validation_error}",
                        ephemeral=True,
                        delete_after=EPHEMERAL_DELETE_AFTER,
                    )
                    del self.role_selections[player_id]
                    return
            except Exception as e:
                log.exception("Error validating roles: %s", e)

        getattr(self, "_reset_ready_state", lambda: None)()
        await self.update_embed()
        await followup_send(
            ctx,
            get("queue.role_select_updated"),
            ephemeral=True,
            delete_after=EPHEMERAL_DELETE_AFTER,
        )

    async def callback_assign_roles(
        self,
        ctx: discord.Interaction,
    ) -> None:
        """Randomly assign roles for selectable_random flow."""
        log = self.logger.getChild("lobby_assign_roles")

        role_flow = getattr(self.metadata, "role_flow", RoleFlow.none)
        if role_flow != RoleFlow.selectable_random:
            log.warning(
                "assign_roles on non-selectable_random lobby lobby=%s",
                self.message.id,
            )
            await followup_send(
                ctx,
                get("matchmaking.invalid_interaction"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        if not has_role_support(self.game):
            await followup_send(
                ctx,
                "This game does not support role assignment",
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        try:
            if hasattr(self.game, "assign_roles"):
                players = list(self.queued_players)
                from playcord.application.services.role_management import (
                    assign_roles as assign_roles_svc,
                )

                assign_roles_svc(self.game, players)

                # Populate role selections from assigned roles
                for player in players:
                    # For now, store role assignments (actual assignment happens at match start)
                    pass

            await self.update_embed()
            await followup_send(
                ctx,
                "Roles have been randomly assigned!",
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
        except Exception as e:
            log.exception("Error assigning roles: %s", e)
            await followup_send(
                ctx,
                f"Error assigning roles: {e!s}",
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )

    async def update_embed(self) -> None:
        """Update the embed based on the players in self.players
        :return: Nothing.
        """
        log = self.logger.getChild("update_embed")
        update_timer = Timer().start()

        if await self._maybe_auto_start_from_lobby():
            log.debug("Matchmaking lobby auto-started in %sms.", update_timer.stop())
            return

        game_rated_text = get("queue.rated") if self.rated else get("queue.not_rated")
        private_text = (
            get("queue.private_status") if self.private else get("queue.public_status")
        )

        desc_suffix = ""
        if (
            self._specs
            and self.rated_requested
            and not self.rated
            and not self.has_bots
            and not self._match_settings_are_default()
            and getattr(
                self.game,
                "customization_forces_unrated_when_non_default",
                True,
            )
        ):
            desc_suffix = f"\n\n{get('queue.customization_unrated_note')}"

        # Parameters in embed title:
        # Time
        # Allowed players
        # Difficulty
        # Rated/Unrated
        # Public/Private

        game_metadata = {}

        for param in ["time", "difficulty", "author", "author_link", "source_link"]:
            if hasattr(self.metadata, param):
                game_metadata[param] = getattr(self.metadata, param)
            else:
                game_metadata[param] = get("help.game_info.unknown")

        container = CustomContainer(
            title=self._lobby_view_summary_line(),
            description=(
                f"⏰{game_metadata['time']}{LONG_SPACE_EMBED * 2}"
                f"👤{self.allowed_players}{LONG_SPACE_EMBED * 2}"
                f"📈{game_metadata['difficulty']}{LONG_SPACE_EMBED * 2}"
                f"📊{game_rated_text}{LONG_SPACE_EMBED * 2}"
                f"{private_text}{desc_suffix}"
            ),
        )

        all_players = self.all_players()
        table_rows = []
        for player, rating, creator_marker in zip(
            all_players,
            column_elo(all_players, self.game_type).split("\n"),
            column_creator(all_players, self.creator).split("\n"),
            strict=False,
        ):
            player_name = getattr(player, "mention", getattr(player, "name", "Unknown"))
            table_rows.append(
                f"- {player_name}: "
                f"{get('queue.field_rating')} {rating} · {get('queue.field_creator')} {creator_marker}",
            )
        table_image_url = None
        table_file = None
        if table_rows:
            container.add_field(
                name=get("queue.field_players"),
                value="\n".join(table_rows),
                inline=False,
            )

        if self._base_start_conditions_met() and self.queued_players:
            ready_lines = []
            for p in sorted(self.queued_players, key=lambda x: x.id):
                mention = (
                    getattr(p, "mention", None)
                    or getattr(p, "name", None)
                    or str(getattr(p, "id", p))
                )
                state = (
                    get("queue.ready_state_ready")
                    if p.id in self.ready_players
                    else get("queue.ready_state_waiting")
                )
                ready_lines.append(
                    fmt("queue.ready_state_line", player=mention, state=state),
                )
            container.add_field(
                name=get("queue.field_ready_state"),
                value="\n".join(ready_lines),
                inline=False,
            )

        # Add whitelist or blacklist depending on private status
        if self.private:
            container.add_field(
                name=get("queue.field_whitelist"),
                value=column_names(self.whitelist),
                inline=True,
            )
        elif len(self.blacklist):
            container.add_field(
                name=get("queue.field_blacklist"),
                value=column_names(self.blacklist),
                inline=True,
            )

        try:
            container.set_footer(text=self.metadata.description)
        except Exception:
            # Fallback: if footer cannot be set for some reason, add as normal fields
            if self.metadata.description:
                container.add_field(
                    name=get("queue.field_game_info"),
                    value=self.metadata.description,
                    inline=False,
                )
            auth = game_metadata.get("author")
            if auth:
                container.add_field(
                    name=get("queue.field_game_by"),
                    value=str(auth),
                    inline=False,
                )

        if self._specs:
            opt_lines = []
            for spec in self._specs:
                v = self.match_settings.get(spec.key, spec.default)
                opt_lines.append(f"**{spec.label}** → `{v}`")
            container.add_field(
                name=get("queue.field_match_options"),
                value="\n".join(opt_lines),
                inline=False,
            )

        role_mode = getattr(self.metadata, "role_mode", RoleMode.none)
        pr_roles = getattr(self.metadata, "player_roles", None)
        role_flow = getattr(self.metadata, "role_flow", RoleFlow.none)

        layout_ok_chosen = len(self._specs) + len(self.all_players()) <= 4

        # Legacy role_mode support
        show_role_selects_legacy = (
            role_mode == RoleMode.chosen
            and not self.has_bots
            and pr_roles is not None
            and len(pr_roles) == len(self.all_players())
            and layout_ok_chosen
        )

        # New plugin-owned role system
        show_role_selects_new = False
        available_roles: dict[int, tuple[str, ...]] | None = None

        if has_role_support(self.game) and role_flow in (
            RoleFlow.selectable,
            RoleFlow.selectable_random,
        ):
            if (
                not self.has_bots
                and len(self.queued_players) >= self.metadata.player_count[0]
            ):
                try:
                    if hasattr(self.game, "role_selection_options"):
                        available_roles = self.game.role_selection_options(
                            [p.id for p in self.queued_players],
                        )
                        if available_roles and layout_ok_chosen:
                            show_role_selects_new = True
                except Exception as e:
                    log.exception("Error getting role selection options: %s", e)

        show_role_selects = show_role_selects_legacy or show_role_selects_new

        if role_mode == RoleMode.chosen:
            if self.has_bots:
                container.add_field(
                    name=get("queue.role_picks_field"),
                    value=get("queue.role_chosen_no_bots"),
                    inline=False,
                )
            elif pr_roles and len(pr_roles) == len(self.all_players()):
                if not layout_ok_chosen:
                    container.add_field(
                        name=get("queue.role_picks_field"),
                        value=get("queue.role_chosen_ui_overflow"),
                        inline=False,
                    )
                else:
                    pick_lines = []
                    for p in sorted(self.queued_players, key=lambda x: x.id):
                        picked = self.role_selections.get(p.id)
                        label = getattr(p, "name", None) or str(p.id)
                        if picked:
                            pick_lines.append(f"**{label}** → `{picked}`")
                        else:
                            pick_lines.append(
                                f"**{label}** → {get('queue.role_picks_none')}",
                            )
                    container.add_field(
                        name=get("queue.role_picks_field"),
                        value=(
                            "\n".join(pick_lines)
                            if pick_lines
                            else get("queue.role_picks_none")
                        ),
                        inline=False,
                    )
            elif pr_roles:
                container.add_field(
                    name=get("queue.role_picks_field"),
                    value=get("queue.role_chosen_lobby_not_full"),
                    inline=False,
                )

        join_id = f"{BUTTON_PREFIX_JOIN}{self.message.id}"
        leave_id = f"{BUTTON_PREFIX_LEAVE}{self.message.id}"
        ready_id = (
            f"{BUTTON_PREFIX_READY}{self.message.id}"
            if self._base_start_conditions_met()
            else None
        )
        ready_label = get("buttons.ready_toggle")
        role_specs_list: list[tuple[int, str, tuple[str, ...]]] = []
        if show_role_selects:
            # Use new plugin-owned role system if available
            if available_roles:
                for p in sorted(self.queued_players, key=lambda x: x.id):
                    disp = getattr(p, "name", None) or str(p.id)
                    avail = available_roles.get(p.id, ())
                    if avail:
                        role_specs_list.append((p.id, disp, avail))
            # Fall back to legacy role_mode system
            elif pr_roles is not None:
                avail = tuple(pr_roles)
                for p in sorted(self.queued_players, key=lambda x: x.id):
                    disp = getattr(p, "name", None) or str(p.id)
                    role_specs_list.append((p.id, disp, avail))
        use_lobby_view = bool(self._specs) or show_role_selects

        # Determine if we should show the assign roles button
        assign_roles_button_id = None
        if (
            has_role_support(self.game)
            and role_flow == RoleFlow.selectable_random
            and not self.has_bots
        ):
            assign_roles_button_id = (
                f"{BUTTON_PREFIX_LOBBY_ASSIGN_ROLES}{self.message.id}"
            )

        if use_lobby_view:
            view = MatchmakingLobbyView(
                join_button_id=join_id,
                leave_button_id=leave_id,
                ready_button_id=ready_id,
                ready_button_label=ready_label,
                lobby_message_id=self.message.id,
                option_specs=self._specs,
                current_values=dict(self.match_settings),
                role_specs=role_specs_list,
                current_role_values=dict(self.role_selections),
                assign_roles_button_id=assign_roles_button_id,
                summary_text=container_to_markdown(container),
                table_image_url=table_image_url,
            )
        else:
            view = MatchmakingView(
                join_button_id=join_id,
                leave_button_id=leave_id,
                ready_button_id=ready_id,
                ready_button_label=ready_label,
                summary_text=container_to_markdown(container),
                table_image_url=table_image_url,
            )

        attachments = [table_file] if table_file is not None else []
        await self.message.edit(view=view, attachments=attachments)
        log.debug(f"Finished matchmaking update task in {update_timer.stop()}ms.")

    async def seed_rematch_players(
        self,
        guild: discord.Guild,
        user_ids: list[int],
    ) -> str | None:
        """Add humans from a finished match to this lobby (creator is already queued)."""
        present = {p.id for p in self.queued_players}
        for uid in user_ids:
            if uid in present:
                continue
            try:
                member = await guild.fetch_member(uid)
            except (discord.NotFound, discord.HTTPException):
                return fmt("rematch.member_missing", mention=f"<@{uid}>")
            player = await run_in_thread(
                get_container().players_repository.get_player,
                member.id,
                discord_user_db_label(member),
            )
            if player is None:
                return get("rematch.db_failed")
            MatchmakingInterface._add_queued_player(self, player)
        return None

    async def accept_invite(self, ctx: discord.Interaction) -> bool:
        """Accept a invite.
        :param ctx: discord context with information about the invite
        :return: whether the invite succeeded or failed.
        """
        player = get_shallow_player(ctx.user)

        # Get logger
        log = self.logger.getChild("accept_invite")
        log.debug(
            f"Attempting to accept invite for player {player} for matchmaker id={self.message.id}"
            f" {contextify(ctx)}",
        )

        if player is None:
            log.warning(
                f"Player.py {player} attempted to accept invite, but we couldn't connect to the database!"
                f"{contextify(ctx)}",
            )
            await followup_send(
                ctx,
                get("queue.couldnt_connect_db"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return False

        if MatchmakingInterface._is_queued_player(
            self,
            player.id,
        ):  # Can't join if you are already in
            log.debug(
                f"Player.py {player} attempted to accept invite, but they are already in the game! "
                f"{contextify(ctx)}",
            )
            await followup_send(
                ctx,
                get("queue.already_in_game"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return False
        if user_in_active_game(player.id):
            log.info(
                f"Player.py {player} attempted to accept invite while already in another active game."
                f" {contextify(ctx)}",
            )
            await followup_send(
                ctx,
                get("queue.already_in_active_game_other_server"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return False
        if user_in_active_matchmaking(player.id):
            log.info(
                f"Player.py {player} attempted to accept invite while already queued in another lobby."
                f" {contextify(ctx)}",
            )
            await followup_send(
                ctx,
                get("queue.already_in_another_queue"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return False
        # Add to whitelist or remove from blacklist, depending on private/public status
        if self.private:
            self.whitelist.add(player)
        else:
            MatchmakingInterface._discard_player_from_set(self.blacklist, player.id)

        MatchmakingInterface._add_queued_player(self, player)
        log.debug(
            f"Successfully accepted invite for {player.id} ({player.name})!"
            f"{contextify(ctx)}",
        )
        await self.update_embed()
        return True

    async def ban(self, player: discord.User, reason: str) -> str | None:
        """Ban a player from the game with reason
        :param player: the player to ban
        :param reason: the reason the player was banned
        :return: Error code or None if no error.
        """
        log = self.logger.getChild("ban")
        new_player = await run_in_thread(
            get_container().players_repository.get_player,
            player.id,
            discord_user_db_label(player),
        )
        log.debug(f"Attempting to ban player {new_player} for reason {reason!r}...")
        if new_player is None:  # Couldn't retrieve information, so don't join them
            log.error(f"Error banning {new_player}: couldn't connect to the database!")
            return get("queue.couldnt_connect_db")

        phase = lobby_ban_phase(
            self._lobby_roster,
            private=self.private,
            new_player=new_player,
            target_user_id=player.id,
            remove_queued_player=lambda uid: MatchmakingInterface._remove_queued_player(
                self,
                uid,
            ),
            rotate_creator_if_needed=lambda uid: (
                MatchmakingInterface._rotate_creator_if_needed(self, uid)
            ),
            queued_count=lambda: len(self.queued_players),
            discard_from_whitelist=lambda uid: (
                MatchmakingInterface._discard_player_from_set(self.whitelist, uid)
            ),
        )
        kicked = phase.kicked_from_queue

        if phase.lobby_empty:
            await self.message.delete()  # Remove matchmaking message
            self.outcome = False
            log.info(f"Self ban of player {new_player} caused the lobby to end.")
            return get("queue.self_ban_only_player")

        if phase.whitelist_error is not None:
            log.info(
                f"Ban of player {new_player} in private lobby failed: not on whitelist anyway.",
            )
            return phase.whitelist_error

        await self.update_embed()  # Update embed now that we have done all operations

        if kicked:
            log.info(
                f"Successfully kicked and banned {new_player}"
                f" from the game for reason {reason!r}",
            )
            return fmt("queue.kicked_and_banned", player=player.mention, reason=reason)
        log.info(
            f"Successfully banned {new_player} from the game for reason {reason!r}",
        )
        return fmt("queue.banned", player=player.mention, reason=reason)

    async def kick(self, player: discord.User, reason: str) -> str | None:
        """Kick a player from the game with reason
        :param player: the player to kick
        :param reason: reason the player was kicked
        :return: error or None if no error.
        """
        log = self.logger.getChild("kick")
        new_player = get_shallow_player(player)
        log.debug(f"Attempting to kick player {new_player} for reason {reason!r}...")
        if new_player is None:  # Couldn't retrieve information, so don't join them
            log.error(f"Error kicking {new_player}: couldn't connect to the database!")
            return get("queue.couldnt_connect_db")

        phase = lobby_kick_phase(
            user_id=new_player.id,
            remove_queued_player=lambda uid: MatchmakingInterface._remove_queued_player(
                self,
                uid,
            ),
            rotate_creator_if_needed=lambda uid: (
                MatchmakingInterface._rotate_creator_if_needed(self, uid)
            ),
            queued_count=lambda: len(self.queued_players),
        )
        if phase.kicked_from_queue:
            await self.update_embed()

        if phase.lobby_empty:
            await self.message.delete()  # Remove matchmaking message
            self.outcome = False
            log.info(f"Self kick of player {new_player} caused the lobby to end.")
            return get("queue.self_kick_only_player")

        if phase.kicked_from_queue:
            log.info(
                f"Successfully kicked {new_player} ({player.name})"
                f" from the game for reason {reason!r}",
            )
            return fmt("queue.kicked", player=player.mention, reason=reason)
        log.info(
            f"Couldn't kick {new_player} from the game: they weren't in the lobby!",
        )
        return fmt("queue.didnt_kick", player=player.mention)

    async def callback_ready_game(self, ctx: discord.Interaction) -> None:
        """Callback for the selected player to join the game
        :param ctx: discord context
        :return: Nothing.
        """
        log = self.logger.getChild("ready_game")
        new_player = get_shallow_player(ctx.user)
        log.debug(f"Attempting to join the game... {contextify(ctx)}")
        if new_player is None:
            log.warning(
                "Attempted to join but player lookup failed. %s",
                contextify(ctx),
            )
            await followup_send(
                ctx,
                get("queue.couldnt_connect_db"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        if MatchmakingInterface._is_queued_player(
            self,
            ctx.user.id,
        ):  # Can't join if you are already in
            log.info(
                f"Attempted to join player {new_player} but failed because they were already in the queue."
                f" {contextify(ctx)}",
            )
            await followup_send(
                ctx,
                get("queue.already_in_game"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
        elif user_in_active_game(new_player.id):
            log.info(
                f"Attempted to join player {new_player} but failed because they are already in another game."
                f" {contextify(ctx)}",
            )
            await followup_send(
                ctx,
                get("queue.already_in_active_game_other_server"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        elif user_in_active_matchmaking(new_player.id):
            log.info(
                f"Attempted to join player {new_player} but failed because they are already queued elsewhere."
                f" {contextify(ctx)}",
            )
            await followup_send(
                ctx,
                get("queue.already_in_another_queue"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        elif not self.private:
            if MatchmakingInterface._contains_player_id(
                self.blacklist,
                new_player.id,
            ):
                log.info(
                    f"Attempted to join player {new_player} but failed because they are banned."
                    f" {contextify(ctx)}",
                )
                await followup_send(
                    ctx,
                    fmt("queue.banned_message", creator=self.creator.mention),
                    ephemeral=True,
                    delete_after=EPHEMERAL_DELETE_AFTER,
                )
                return
            MatchmakingInterface._add_queued_player(self, new_player)
            await self.update_embed()  # Update embed on discord side
        else:
            if not MatchmakingInterface._contains_player_id(
                self.whitelist,
                new_player.id,
            ):
                log.info(
                    f"Attempted to join player {new_player} to private game but failed because"
                    f" they were not on the whitelist."
                    f" {contextify(ctx)}",
                )
                await followup_send(
                    ctx,
                    get("queue.not_on_whitelist"),
                    ephemeral=True,
                    delete_after=EPHEMERAL_DELETE_AFTER,
                )
                return
            MatchmakingInterface._add_queued_player(self, new_player)
            await self.update_embed()  # Update embed on discord side

    async def callback_toggle_ready(self, ctx: discord.Interaction) -> None:
        player = get_shallow_player(ctx.user)
        if player is None:
            await followup_send(
                ctx,
                get("queue.couldnt_connect_db"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        if not MatchmakingInterface._is_queued_player(self, player.id):
            await followup_send(
                ctx,
                get("queue.not_in_game"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        if not self._base_start_conditions_met():
            await followup_send(
                ctx,
                get("queue.ready_requirements_not_met"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return
        ready_players = getattr(self, "ready_players", set())
        if player.id in ready_players:
            ready_players.remove(player.id)
            notice = get("queue.unready_confirmed")
        else:
            ready_players.add(player.id)
            notice = get("queue.ready_confirmed")
        await self.update_embed()
        await followup_send(
            ctx,
            notice,
            ephemeral=True,
            delete_after=EPHEMERAL_DELETE_AFTER,
        )

    async def callback_leave_game(self, ctx: discord.Interaction) -> None:
        """Callback for the selected player to leave the matchmaking session
        :param ctx: discord context
        :return: None.
        """
        log = self.logger.getChild("leave_game")
        log.debug(f"Attempting to leave the game... {contextify(ctx)}")
        player = get_shallow_player(ctx.user)
        if player is None:
            await followup_send(
                ctx,
                get("queue.couldnt_connect_db"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
            return

        if not MatchmakingInterface._is_queued_player(
            self,
            player.id,
        ):  # Can't leave if you weren't even there
            log.info(
                f"Attempted to remove player {player} but failed because they weren't in the queue to begin with."
                f" {contextify(ctx)}",
            )
            await followup_send(
                ctx,
                get("queue.not_in_game"),
                ephemeral=True,
                delete_after=EPHEMERAL_DELETE_AFTER,
            )
        else:
            MatchmakingInterface._remove_queued_player(self, player.id)
            # Nobody is left lol
            if not len(self.queued_players):
                log.info(
                    f"Call to leave_game left no players in lobby, so ending game. {contextify(ctx)}",
                )
                await followup_send(
                    ctx,
                    get("queue.game_cancelled_last_player"),
                    ephemeral=True,
                    delete_after=EPHEMERAL_DELETE_AFTER,
                )
                await self.message.delete()  # Remove matchmaking message
                self.outcome = False
                return

            if (
                player.id == self.creator.id
            ):  # Update creator if the person leaving was the creator.
                MatchmakingInterface._rotate_creator_if_needed(self, player.id)
                log.debug(
                    "Successful leave_game removed creator=%s and reassigned creator=%s. %s",
                    player,
                    self.creator,
                    contextify(ctx),
                )

            await self.update_embed()  # Update embed again
        return
