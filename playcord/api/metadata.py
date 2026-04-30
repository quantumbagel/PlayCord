"""Game metadata and lobby-time configuration types."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sized
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from playcord.core.errors import ConfigurationError
from playcord.core.rating import (
    DEFAULT_TRUESKILL_PARAMETERS,
)

if TYPE_CHECKING:
    from playcord.api.bot import BotDefinition
    from playcord.api.handlers import HandlerSpec
    from playcord.api.match_options import MatchOptionSpec


class ParameterKind(StrEnum):
    """Types of parameters for slash command moves.

    Attributes:
        string: Text input parameter.
        integer: Numeric input parameter.
        dropdown: Selection from predefined choices.

    """

    string = "string"
    integer = "integer"
    dropdown = "dropdown"


@dataclass(frozen=True, slots=True)
class MoveParameter:
    """Static slash-command parameter metadata.

    Attributes:
        name: Parameter name (shown in Discord slash commands).
        description: Human-readable description of what to input.
        kind: Type of parameter (string, integer, or dropdown).
        optional: Whether this parameter can be omitted.
        autocomplete: Optional handler for autocomplete suggestions.
        force_reload: If True, reload game state before executing autocomplete.
        choices: Predefined choices for dropdown parameters.
        min_value: Minimum value for integer parameters.
        max_value: Maximum value for integer parameters.

    """

    name: str
    description: str
    kind: ParameterKind
    optional: bool = False
    autocomplete: HandlerSpec = None
    force_reload: bool = False
    choices: tuple[tuple[str, str], ...] | None = None
    min_value: int | None = None
    max_value: int | None = None


@dataclass(frozen=True, slots=True)
class Move:
    """A statically registered command that may satisfy a runtime input request.

    Moves are Discord slash commands registered for the game. They can be invoked
    by players to provide input during gameplay (e.g., /move position 5).

    Attributes:
        name: Command name (e.g., "move", "draw_card").
        description: Human-readable description of what the command does.
        options: Tuple of parameter definitions for the command.

    """

    name: str
    description: str
    options: tuple[MoveParameter, ...] = ()


class PlayerOrder(StrEnum):
    """How to order players in the game.

    Attributes:
        random: Randomize player order.
        preserve: Keep the order they joined in.
        creator_first: Put match creator first.
        reverse: Reverse the current order.

    """

    random = "random"
    preserve = "preserve"
    creator_first = "creator_first"
    reverse = "reverse"


class RoleMode(StrEnum):
    """How roles are assigned to players.

    Attributes:
        none: No roles - all players have identical roles.
        random: Roles assigned randomly.
        chosen: Players select their preferred roles.
        secret: Roles assigned randomly but hidden until reveal.

    """

    none = "none"
    random = "random"
    chosen = "chosen"
    secret = "secret"


class RoleFlow(StrEnum):
    """Role selection flow for the lobby.

    Determines how and when players choose roles:

    Attributes:
        none: No role selection UI in lobby.
        selectable: Players manually select roles.
        random: Roles assigned automatically (random or secret).
        selectable_random: Players can choose a role or use random.

    """

    none = "none"
    selectable = "selectable"
    random = "random"
    selectable_random = "selectable_random"


@dataclass(frozen=True, slots=True)
class Role:
    """Definition of a role available in the game.

    Attributes:
        id: Unique identifier for the role (e.g., "mafia", "town").
        name: Display name of the role.
        description: Explanation of the role's objectives and abilities.

    """

    id: str
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class RoleSelection:
    """Player's role selection input during lobby.

    Attributes:
        player_id: ID of the player selecting a role.
        role_id: ID of the role they selected.

    """

    player_id: int
    role_id: str


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    """Final role assignment for a player after role phase.

    Attributes:
        player_id: ID of the player.
        role_id: ID of the role assigned.
        seat_index: Position in the seating order.

    """

    player_id: int
    role_id: str
    seat_index: int


@dataclass(frozen=True, slots=True)
class GameMetadata:
    """Static metadata and configuration for a game plugin.

    This class defines all game properties that are known at plugin registration time,
    including display information, rules, supported features, and player constraints.

    Attributes:
        key: Unique identifier for the game (e.g., "tictactoe", "secret_hitler").
        name: Display name of the game.
        summary: Short one-line summary of the game.
        description: Longer description explaining how to play.
        move_group_description: Description of command group for Discord (used in slash commands).
        player_count: Minimum/maximum players. Can be int (exact), tuple (min, max), or None (any).
        author: Name of the game developer.
        version: Semantic version of the game plugin.
        author_link: URL to author's website or social profile.
        source_link: URL to game source code repository.
        time: Estimated playtime (e.g., "30 minutes").
        difficulty: Game difficulty level (e.g., "easy", "medium", "hard").
        bots: Dict of bot implementations keyed by bot name.
        moves: Tuple of Move definitions (slash commands available in the game).
        peek_callback: Optional handler to show game state to a specific player.
        player_order: How to order players (random, preserve, creator_first, reverse).
        role_mode: Role assignment mode (none, random, chosen, secret).
        player_roles: Tuple of role IDs if using roles, None otherwise.
        role_flow: Role selection flow (none, selectable, random, selectable_random).
        trueskill_parameters: TrueSkill rating system parameters (sigma, beta, tau, draw_margin).
        customizable_options: Tuple of MatchOptionSpec for match-level customization.

    """

    key: str
    name: str
    summary: str
    description: str
    move_group_description: str
    player_count: int | tuple[int, ...]
    author: str
    version: str
    author_link: str
    source_link: str
    time: str
    difficulty: str
    bots: dict[str, BotDefinition] = field(default_factory=dict)
    moves: tuple[Move, ...] = ()
    peek_callback: HandlerSpec = None
    player_order: PlayerOrder = PlayerOrder.random
    role_mode: RoleMode = RoleMode.none
    player_roles: tuple[str, ...] | None = None
    role_flow: RoleFlow = RoleFlow.none
    trueskill_parameters: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_TRUESKILL_PARAMETERS),
    )
    customizable_options: tuple[MatchOptionSpec, ...] = ()


def validate_role_selection(
    *,
    roles: Iterable[str] | Sized | None,
    selections: dict[int, str],
) -> bool | str:
    if not roles:
        return True
    if (
        not isinstance(selections, dict)
        or not isinstance(roles, Iterable)
        or not isinstance(roles, Sized)
        or len(selections) != len(roles)
    ):
        return "Each player must choose a role."
    expected = Counter(list(roles))
    chosen = Counter(selections.values())
    if expected != chosen:
        return "Each role must be picked exactly once."
    return True


def ensure_valid_player_count(game: type[object], count: int) -> None:
    """Validate a player count against a runtime game's metadata."""
    metadata = getattr(game, "metadata", None)
    allowed = getattr(metadata, "player_count", None)
    if isinstance(allowed, int):
        if count != allowed:
            msg = f"{metadata.key} requires exactly {allowed} players"
            raise ConfigurationError(msg)
        return
    if isinstance(allowed, tuple) and count not in allowed:
        values = ", ".join(str(value) for value in allowed)
        msg = f"{metadata.key} requires one of these player counts: {values}"
        raise ConfigurationError(msg)
