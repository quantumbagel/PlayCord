"""Runtime game API used by PlayCord games."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, overload

from playcord.api.bot import BotDefinition
from playcord.api.handlers import HandlerRef, HandlerSpec, handler
from playcord.api.metadata import (
    GameMetadata,
    Move,
    MoveParameter,
    ParameterKind,
    PlayerOrder,
    Role,
    RoleAssignment,
    RoleFlow,
    RoleMode,
    RoleSelection,
    ensure_valid_player_count,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from playcord.api.match_options import MatchOptionSpec
    from playcord.core.player import Player

ButtonStyle = Literal["primary", "secondary", "success", "danger"]
MessageTarget = Literal["thread", "overview", "ephemeral"]
MessagePurpose = Literal["board", "announcement", "ephemeral", "custom", "overview"]
OutcomeKind = Literal["winner", "draw", "interrupted"]
InputSource = Literal["command", "button", "select", "bot"]
InputMode = Literal["first", "all"]


class MessageId:
    """Standard message ID constants for use with update_message() and
    request_input().
    """

    BOARD = "board"
    OVERVIEW = "overview"


@dataclass(frozen=True, slots=True)
class SelectChoice:
    """Option in a SelectInput dropdown.

    Attributes:
        label: Display text for this choice.
        value: Internal value when selected.
        default: If True, this choice is selected by default.

    """

    label: str
    value: str
    default: bool = False


@dataclass(frozen=True, slots=True)
class ButtonInput:
    """Interactive button component for a message.

    Attributes:
        id: Unique identifier for this button within its message.
        label: Display text on the button.
        arguments: Data to include in the response when this button is clicked.
        style: Button styling ("primary", "secondary", "success", "danger").
        emoji: Optional emoji to display on the button.
        disabled: If True, button cannot be clicked.

    """

    id: str
    label: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    style: ButtonStyle = "secondary"
    emoji: str | None = None
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class SelectInput:
    """Dropdown select component for a message.

    Attributes:
        id: Unique identifier for this select within its message.
        options: Tuple of choices available in the dropdown.
        placeholder: Placeholder text shown when nothing is selected.
        min_values: Minimum options that must be selected.
        max_values: Maximum options that can be selected.
        disabled: If True, select cannot be used.

    """

    id: str
    options: tuple[SelectChoice, ...]
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class CommandInput:
    """Slash command input (not typically used in request_input).

    Attributes:
        id: Command identifier.
        command_name: Name of the Discord slash command (e.g., "move").
        argument_names: Expected parameter names for this command.

    """

    id: str
    command_name: str
    argument_names: tuple[str, ...] | None = None


GameInputSpec = ButtonInput | SelectInput | CommandInput


@dataclass(frozen=True, slots=True)
class BinaryAsset:
    """Binary file attachment (image, etc.) for a message.

    Attributes:
        filename: Name of the file (used for display and Discord).
        data: Binary file content.
        description: Optional description/alt text for accessibility.

    """

    filename: str
    data: bytes
    description: str | None = None


@dataclass(frozen=True, slots=True)
class MessageLayout:
    """Layout specification for a Discord message.

    Describes content, interactive components, and attachments to send as a message.

    Attributes:
        content: Main message text content.
        buttons: Interactive buttons to display.
        selects: Dropdown selects to display.
        attachments: Binary files to attach (typically images).
        button_row_width: How many buttons per row (None = auto).

    """

    content: str | None = None
    buttons: tuple[ButtonInput, ...] = ()
    selects: tuple[SelectInput, ...] = ()
    attachments: tuple[BinaryAsset, ...] = ()
    button_row_width: int | None = None


@dataclass(frozen=True, slots=True)
class ChannelAction:
    """Base class for actions on a Discord channel.

    Attributes:
        target: Where to send the action ("thread", "overview", "ephemeral").

    """

    target: MessageTarget


@dataclass(frozen=True, slots=True)
class UpsertMessage(ChannelAction):
    """Create or update a message in a channel.

    Attributes:
        key: Unique identifier for this message (update if key already exists).
        layout: MessageLayout describing content and components.
        purpose: Category for the message (used for tracking).

    """

    key: str
    layout: MessageLayout
    purpose: MessagePurpose = "custom"


@dataclass(frozen=True, slots=True)
class DeleteMessage(ChannelAction):
    """Delete a previously sent message.

    Attributes:
        key: Identifier of the message to delete (must match key used in UpsertMessage).

    """

    key: str


@dataclass(frozen=True, slots=True)
class Outcome:
    """Result of a completed game.

    Attributes:
        kind: Type of outcome ("winner" for games with winners, "draw" for ties,
              "interrupted" for games that were interrupted before completion).
        placements: List of player groups representing final standings.
                   For "winner" outcomes, first group is winners, second is losers, etc.
                   For "draw" outcomes, typically all players in a single group.
                   For "interrupted" outcomes, contains forfeited players.
        reason: Optional explanation for the outcome (e.g., "forfeit", "interrupted").

    """

    kind: OutcomeKind
    placements: list[list[Player]] = field(default_factory=list)
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayState:
    """State snapshot for replaying game events.

    Used by ReplayableGame implementations to reconstruct game state frame-by-frame.

    Attributes:
        game_key: Identifier of the game being replayed.
        players: List of players in the game.
        match_options: Match-level configuration.
        move_index: Current position in the replay (which move we're on).
        state: Game-specific state object (structure defined by the game).

    """

    game_key: str
    players: list[Player]
    match_options: dict[str, Any]
    move_index: int
    state: Any


@dataclass(frozen=True, slots=True)
class OwnedMessage:
    """Message sent to Discord that we're tracking.

    Allows later updates or deletions to the message.

    Attributes:
        key: Unique identifier for this message.
        purpose: Category of message (used for querying).
        discord_message_id: Discord's message ID (for updates).
        channel_id: Discord channel ID.
        metadata: Custom metadata to attach to the message.

    """

    key: str
    purpose: str
    discord_message_id: int
    channel_id: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GameContext:
    """Runtime context for a game instance.

    Provides access to current game state, players, configuration, and messages.

    Attributes:
        match_id: Unique identifier for this match.
        game_key: Identifier of the game being played.
        players: List of players participating.
        match_options: Match-level options (from customizable_options).
        owned_messages: Messages we've sent to Discord (tracked for updates).
        latest_overview: Latest match overview message content.
        roles: Mapping of player IDs to their assigned role IDs.

    """

    match_id: int
    game_key: str
    players: list[Player]
    match_options: dict[str, Any]
    owned_messages: list[OwnedMessage] = field(default_factory=list)
    latest_overview: str | None = None
    roles: dict[int, str] = field(default_factory=dict)

    def get_message(self, discord_message_id: int) -> OwnedMessage | None:
        for message in self.owned_messages:
            if message.discord_message_id == discord_message_id:
                return message
        return None

    def list_owned_messages(self, *, purpose: str | None = None) -> list[OwnedMessage]:
        if purpose is None:
            return list(self.owned_messages)
        return [
            message for message in self.owned_messages if message.purpose == purpose
        ]


@dataclass(frozen=True, slots=True)
class GameInput:
    """Player input received in response to a request_input() call.

    Attributes:
        request_id: Unique identifier for the input request.
        input_id: Identifier of the specific input component that was activated.
        actor: Player who provided the input.
        source: Channel through which input was received (button, select, command, or bot).
        arguments: Dictionary of input values from ButtonInput or CommandInput.
                 Populated when source is "button" or "command".
        values: Tuple of string values selected from SelectInput.
               Populated when source is "select".
        ctx: Game context at the time of input (if available).

    """

    request_id: str
    input_id: str
    actor: Player
    source: InputSource
    arguments: dict[str, Any]
    values: tuple[str, ...] = ()
    ctx: GameContext | None = None


@dataclass(frozen=True, slots=True)
class InputTimeout:
    """Represents a timeout event for an input request.

    Attributes:
        request_id: Identifier for the input request that timed out.
        players: All players who were asked for input.
        missing_players: Players who did not respond before timeout.
        responses: Dictionary mapping player IDs to responses received before timeout.

    """

    request_id: str
    players: tuple[Player, ...]
    missing_players: tuple[Player, ...]
    responses: dict[int, GameInput]


class AutoForfeit(Exception):
    """Raised internally when a request times out without a timeout handler."""

    def __init__(self, players: Sequence[Player]) -> None:
        self.players = tuple(players)
        super().__init__("Input request timed out")


class RuntimeGame(ABC):
    """Stateful game instance managed by GameManager.

    This is the main class game developers subclass to implement their game logic.
    It manages:
    - Game state and player interactions
    - Sending messages and requesting player input
    - Recording moves and game events
    - Handling role assignments
    - Returning final game outcomes

    Attributes:
        metadata: Static GameMetadata describing the game.
        players: List of Player objects participating in this game instance.
        match_options: Dictionary of match-level options (from player selections).

    Examples:
        ```python
        class MyGame(RuntimeGame):
            metadata = GameMetadata(
                key="my_game",
                name="My Game",
                summary="A game I built",
            )

            async def main(self) -> Outcome:
                # Game loop here
                layout = MessageLayout(content="Game board", buttons=(...))
                await self.update_message(MessageId.BOARD, layout)

                result = await self.request_input(
                    self.players,
                    [ButtonInput(id="move_1", label="Move 1")],
                    timeout=30,
                    mode="first",
                )
                if isinstance(result, InputTimeout):
                    return Outcome(kind="interrupted", reason="timeout")

                await self.record_move(
                    actor=result.actor,
                    name="make_move",
                    arguments=result.arguments,
                    source=result.source,
                )

                # ...determine winner
                return Outcome(
                    kind="winner",
                    placements=[winners, losers],
                )
        ```

    """

    metadata: GameMetadata

    def __init__(
        self,
        players: list[Player],
        *,
        match_options: dict[str, Any] | None = None,
    ) -> None:
        self.players = players
        self.match_options = dict(match_options or {})
        self._runtime: Any | None = None

    def _bind_runtime(self, runtime: Any) -> None:
        self._runtime = runtime

    @property
    def runtime(self) -> Any:
        if self._runtime is None:
            msg = "Game runtime is not bound yet"
            raise RuntimeError(msg)
        return self._runtime

    @property
    def context(self) -> GameContext:
        return self.runtime.build_context()

    @classmethod
    def option_specs(cls) -> tuple[MatchOptionSpec, ...]:
        return tuple(getattr(cls.metadata, "customizable_options", ()) or ())

    @abstractmethod
    async def main(self) -> Outcome:
        """Run the game until it returns a final outcome."""

    async def update_message(
        self,
        message_id: str,
        layout: MessageLayout,
        *,
        target: MessageTarget = "thread",
        purpose: MessagePurpose = "board",
    ) -> None:
        """Update or create a message with game content.

        If message_id already exists, updates the existing message.
        Otherwise, creates a new message and stores it with the given key.

        Args:
            message_id: Unique key for this message (e.g., MessageId.BOARD or "my_custom_message").
            layout: MessageLayout describing content, buttons, and attachments.
            target: Where to send message ("thread" for game thread, "overview" for match overview, "ephemeral" for visible only to sender).
            purpose: Category for the message ("board", "announcement", "custom", etc) for tracking.

        Example:
            ```python
            board = MessageLayout(
                content="Current Board State",
                buttons=(
                    ButtonInput(id="move_a", label="Move A"),
                    ButtonInput(id="move_b", label="Move B"),
                ),
            )
            await self.update_message(MessageId.BOARD, board)
            ```

        """
        await self.runtime.update_message(
            message_id,
            layout,
            target=target,
            purpose=purpose,
        )

    async def delete_message(
        self,
        message_id: str,
        *,
        target: MessageTarget = "thread",
    ) -> None:
        """Delete a previously sent message.

        Args:
            message_id: Key of the message to delete (must match the key used in update_message).
            target: Which channel to delete from (must match the target used when creating the message).

        """
        await self.runtime.delete_message(message_id, target=target)

    @overload
    async def request_input(
        self,
        players: Sequence[Player],
        inputs: Sequence[GameInputSpec],
        *,
        timeout: float,
        mode: Literal["first"] = "first",
        min_responses: int | None = None,
        on_timeout: Callable[[InputTimeout], Any] | None = None,
        message_id: str | None = None,
        layout: MessageLayout | None = None,
        target: MessageTarget = "thread",
        purpose: MessagePurpose = "board",
        auto_remove_on_timeout: bool = False,
        send_timeout_warning: bool = True,
    ) -> GameInput | InputTimeout: ...

    @overload
    async def request_input(
        self,
        players: Sequence[Player],
        inputs: Sequence[GameInputSpec],
        *,
        timeout: float,
        mode: Literal["all"],
        min_responses: int | None = None,
        on_timeout: Callable[[InputTimeout], Any] | None = None,
        message_id: str | None = None,
        layout: MessageLayout | None = None,
        target: MessageTarget = "thread",
        purpose: MessagePurpose = "board",
        auto_remove_on_timeout: bool = False,
        send_timeout_warning: bool = True,
    ) -> list[GameInput] | InputTimeout: ...

    async def request_input(
        self,
        players: Sequence[Player],
        inputs: Sequence[GameInputSpec],
        *,
        timeout: float,
        mode: InputMode = "first",
        min_responses: int | None = None,
        on_timeout: Callable[[InputTimeout], Any] | None = None,
        message_id: str | None = None,
        layout: MessageLayout | None = None,
        target: MessageTarget = "thread",
        purpose: MessagePurpose = "board",
        auto_remove_on_timeout: bool = False,
        send_timeout_warning: bool = True,
    ) -> GameInput | list[GameInput] | InputTimeout:
        """Request input from one or more players.

        This method sends interactive UI components to players and waits for their responses.
        The return type depends on the mode parameter:
        - mode="first": Returns first single GameInput received or InputTimeout if all time out
        - mode="all": Returns list of all GameInputs received or InputTimeout if incomplete

        Args:
            players: Players to request input from.
            inputs: Interactive components (buttons, selects, etc.) to display.
            timeout: Seconds to wait for responses.
            mode: "first" for first responder, "all" for all players. Defaults to "first".
            min_responses: Minimum responses needed to stop waiting (default: all players if mode="all").
            on_timeout: Optional callback when timeout occurs. Can return Outcome to end game,
                       or InputTimeout to propagate the timeout. Supports async functions.
            message_id: Optional key to identify/update this message later.
            layout: Optional message layout to display with the input request.
            target: Where to send the message ("thread", "overview", or "ephemeral").
            purpose: Message purpose for tracking ("board", "announcement", etc).
            auto_remove_on_timeout: If True, remove message when timeout occurs.
            send_timeout_warning: If True, notify players when time is running out.

        Returns:
            GameInput (single response) when mode="first"
            list[GameInput] (multiple responses) when mode="all"
            InputTimeout if timeout occurs and no on_timeout handler is provided

        Raises:
            AutoForfeit: If timeout occurs with no on_timeout handler (unless auto_remove_on_timeout=True).

        """
        result = await self.runtime.request_input(
            players=tuple(players),
            inputs=tuple(inputs),
            timeout=timeout,
            mode=mode,
            min_responses=min_responses,
            message_id=message_id,
            layout=layout,
            target=target,
            purpose=purpose,
            auto_remove_on_timeout=auto_remove_on_timeout,
            send_timeout_warning=send_timeout_warning,
        )
        if isinstance(result, InputTimeout):
            if auto_remove_on_timeout:
                return result
            if on_timeout is None:
                raise AutoForfeit(result.missing_players)
            handled = on_timeout(result)
            if inspect.isawaitable(handled):
                return await handled
            return handled
        return result

    async def record_move(
        self,
        actor: Player,
        name: str,
        arguments: dict[str, Any],
        *,
        source: InputSource,
        input_id: str | None = None,
    ) -> None:
        """Record a player's move to the game's replay history.

        This logs the move in a structured way that can be replayed later.

        Args:
            actor: Player who made the move.
            name: Name of the move (e.g., "place_piece", "draw_card").
            arguments: Dictionary of move parameters (extracted from GameInput.arguments or GameInput.values).
            source: Where the move came from ("button", "select", "command", "bot").
            input_id: Optional reference to the UI component that generated this move.

        Example:
            ```python
            result = await self.request_input(...)
            if isinstance(result, GameInput):
                await self.record_move(
                    actor=result.actor,
                    name="place_piece",
                    arguments={"x": 2, "y": 3},
                    source=result.source,
                    input_id=result.input_id,
                )
            ```

        """
        await self.runtime.record_move(
            actor,
            name,
            arguments,
            source=source,
            input_id=input_id,
        )

    def log_replay_event(self, event_type: str, **payload: Any) -> None:
        """Log a custom event to the replay history.

        Args:
            event_type: Type of event (e.g., "special_rule_triggered", "score_updated").
            **payload: Event-specific data to include in the replay log.

        """
        self.runtime.log_replay_event(event_type, **payload)

    async def forfeit_player(
        self,
        player: Player,
        *,
        reason: str = "forfeit",
    ) -> Outcome:
        """Handle a player forfeiting and return the final outcome.

        Args:
            player: Player who forfeited.
            reason: Reason for forfeit ("forfeit", "timeout", "disconnect", etc).

        Returns:
            Outcome with remaining players as winners and forfeited player as loser.

        """
        return await self.runtime.forfeit_player(player, reason=reason)

    def outcome_for_forfeit(
        self,
        players: Sequence[Player],
        *,
        reason: str = "forfeit",
    ) -> Outcome:
        forfeited = {int(player.id) for player in players}
        winners = [player for player in self.players if int(player.id) not in forfeited]
        losers = [player for player in self.players if int(player.id) in forfeited]
        if winners:
            return Outcome(kind="winner", placements=[winners, losers], reason=reason)
        return Outcome(kind="interrupted", placements=[losers], reason=reason)

    def get_roles(self) -> tuple[Role, ...]:
        return ()

    def validate_roles(self, selections: dict[int, str]) -> bool | str:
        return True

    def role_selection_options(
        self,
        player_ids: list[int],
    ) -> dict[int, tuple[Role, ...]]:
        return {}

    def assign_roles(
        self,
        selections: dict[int, str] | None = None,
    ) -> list[RoleAssignment]:
        return []

    def match_global_summary(self, outcome: Outcome) -> str | None:
        return None

    def match_summary(self, outcome: Outcome) -> dict[int, str] | None:
        return None

    def initial_replay_state(self, ctx: GameContext) -> ReplayState | None:
        return None

    def apply_replay_event(
        self,
        state: ReplayState,
        event: dict[str, Any],
    ) -> ReplayState | None:
        return None

    def render_replay(self, state: ReplayState) -> MessageLayout | None:
        return None


class ReplayableGame(RuntimeGame, ABC):
    """Explicit replay capability for games that support frame reconstruction."""

    @abstractmethod
    def initial_replay_state(self, ctx: GameContext) -> ReplayState | None:
        raise NotImplementedError

    @abstractmethod
    def apply_replay_event(
        self,
        state: ReplayState,
        event: dict[str, Any],
    ) -> ReplayState | None:
        raise NotImplementedError

    @abstractmethod
    def render_replay(self, state: ReplayState) -> MessageLayout | None:
        raise NotImplementedError


__all__ = [
    "AutoForfeit",
    "BinaryAsset",
    "BotDefinition",
    "ButtonInput",
    "ButtonStyle",
    "ChannelAction",
    "CommandInput",
    "DeleteMessage",
    "GameContext",
    "GameInput",
    "GameInputSpec",
    "GameMetadata",
    "HandlerRef",
    "HandlerSpec",
    "InputMode",
    "InputSource",
    "InputTimeout",
    "MessageId",
    "MessageLayout",
    "MessagePurpose",
    "MessageTarget",
    "Move",
    "MoveParameter",
    "Outcome",
    "OutcomeKind",
    "OwnedMessage",
    "ParameterKind",
    "PlayerOrder",
    "ReplayState",
    "ReplayableGame",
    "Role",
    "RoleAssignment",
    "RoleFlow",
    "RoleMode",
    "RoleSelection",
    "RuntimeGame",
    "SelectChoice",
    "SelectInput",
    "UpsertMessage",
    "ensure_valid_player_count",
    "handler",
]
