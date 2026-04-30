"""Matchmaking orchestration service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from playcord.api.plugin import RoleMode, resolve_player_count
from playcord.core.generators import generate_bot_name
from playcord.core.player import Player
from playcord.infrastructure.locale import fmt, get

if TYPE_CHECKING:
    from collections.abc import Callable

    from playcord.infrastructure.state.user_games import SessionRegistry


@dataclass(slots=True)
class LobbyRoster:
    """Lobby roster state (humans via whitelist/queue; bots; ready flags)."""

    whitelist: set[Any]
    blacklist: set[Any]
    bots: list[Any]
    ready_players: set[int]

    @classmethod
    def initial(cls, initial_whitelist: set[Any]) -> LobbyRoster:
        return cls(
            whitelist=set(initial_whitelist),
            blacklist=set(),
            bots=[],
            ready_players=set(),
        )


def lobby_base_start_conditions_met(
        *,
        bots: list[Any],
        game: Any,
        metadata: Any,
        queued_players: Any,
        role_selections: dict[int, str],
        specs: tuple[Any, ...],
) -> bool:
    total_players = len(queued_players) + len(bots)
    player_count = resolve_player_count(game)
    if isinstance(player_count, list):
        if total_players not in player_count:
            return False
    elif isinstance(player_count, int) and total_players != player_count:
        return False

    role_mode = getattr(metadata, "role_mode", RoleMode.none)
    if role_mode == RoleMode.chosen:
        if bots:
            return False
        if len(specs) + total_players > 4:
            return False
        pr = getattr(metadata, "player_roles", None)
        if not pr or len(pr) != total_players:
            return False
        for p in queued_players:
            if p.id not in role_selections:
                return False
        return game.validate_role_selection(role_selections) is True
    return True


def lobby_add_bot(
        roster: LobbyRoster,
        difficulty: str,
        *,
        game: Any,
        metadata: Any,
        human_queue_size: int,
        number: int = 1,
) -> str | None:
    available_bots = getattr(metadata, "bots", {})
    if not available_bots:
        return get("queue.bot_not_supported")
    if difficulty not in available_bots:
        return fmt("queue.bot_invalid_difficulty", difficulty=difficulty)

    used_names: set[str] = {
        name for name in (getattr(p, "name", None) for p in roster.bots)
        if name is not None
    }

    for _ in range(number):
        bot_name = generate_bot_name(used_names)
        used_names.add(bot_name)
        bot_player = Player.create_bot(
            name=bot_name,
            difficulty=difficulty,
            bot_index=len(roster.bots),
        )
        roster.bots.append(bot_player)

    return None


def lobby_remove_bot(roster: LobbyRoster, bot_name: str) -> str | None:
    """Remove a bot from the roster by name.
    
    Returns an error message if the bot is not found, or None on success.
    """
    for i, bot in enumerate(roster.bots):
        bot_display = getattr(bot, "display_name", None)
        bot_name_attr = getattr(bot, "name", None)
        if bot_display == bot_name or bot_name_attr == bot_name:
            roster.bots.pop(i)
            return None
    return fmt("queue.bot_not_found", name=bot_name)


@dataclass(slots=True)
class KickPhaseResult:
    kicked_from_queue: bool
    lobby_empty: bool


def lobby_kick_phase(
        *,
        user_id: int,
        remove_queued_player: Callable[[int], Any | None],
        rotate_creator_if_needed: Callable[[int], None],
        queued_count: Callable[[], int],
) -> KickPhaseResult:
    kicked = remove_queued_player(user_id) is not None
    if queued_count() == 0:
        return KickPhaseResult(kicked_from_queue=kicked, lobby_empty=True)
    rotate_creator_if_needed(user_id)
    return KickPhaseResult(kicked_from_queue=kicked, lobby_empty=False)


@dataclass(slots=True)
class BanPhaseResult:
    kicked_from_queue: bool
    lobby_empty: bool
    whitelist_error: str | None


def lobby_ban_phase(
        roster: LobbyRoster,
        *,
        private: bool,
        new_player: Any,
        target_user_id: int,
        remove_queued_player: Callable[[int], Any | None],
        rotate_creator_if_needed: Callable[[int], None],
        queued_count: Callable[[], int],
        discard_from_whitelist: Callable[[int], Any | None],
) -> BanPhaseResult:
    kicked = remove_queued_player(new_player.id) is not None
    if queued_count() == 0:
        return BanPhaseResult(
            kicked_from_queue=kicked,
            lobby_empty=True,
            whitelist_error=None,
        )
    rotate_creator_if_needed(target_user_id)
    if private:
        if discard_from_whitelist(new_player.id) is None:
            return BanPhaseResult(
                kicked_from_queue=kicked,
                lobby_empty=False,
                whitelist_error=get("queue.cant_ban_not_whitelisted"),
            )
    else:
        roster.blacklist.add(new_player)
    return BanPhaseResult(
        kicked_from_queue=kicked,
        lobby_empty=False,
        whitelist_error=None,
    )


@dataclass(slots=True)
class Matchmaker:
    """Tracks active matchmaking sessions."""

    registry: SessionRegistry

    def register(self, message_id: int, lobby: Any) -> None:
        self.registry.matchmaking_by_message_id[message_id] = lobby
        for player in getattr(lobby, "players", []) or []:
            player_id = getattr(player, "id", None)
            if player_id is not None:
                self.registry.user_to_matchmaking[int(player_id)] = lobby

    def unregister(self, message_id: int) -> None:
        lobby = self.registry.matchmaking_by_message_id.pop(message_id, None)
        if lobby is None:
            return
        for player in getattr(lobby, "players", []) or []:
            player_id = getattr(player, "id", None)
            if player_id is not None:
                self.registry.user_to_matchmaking.pop(int(player_id), None)

    def by_user_id(self, user_id: int) -> Any | None:
        return self.registry.user_to_matchmaking.get(user_id)
