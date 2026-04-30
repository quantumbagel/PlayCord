"""Tic-tac-toe using the main/request Game API."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

from playcord.api import (
    BotDefinition,
    ButtonInput,
    CommandInput,
    GameContext,
    GameInput,
    GameMetadata,
    MessageLayout,
    Move,
    MoveParameter,
    Outcome,
    ParameterKind,
    ReplayableGame,
    ReplayState,
    handler,
)
from playcord.api.plugin import register_game

if TYPE_CHECKING:
    from playcord.core.player import Player

Board = list[list[str]]
MoveCoord = tuple[int, int]

BOARD_SIZE = 3
EMPTY = " "
MARK_X = "X"
MARK_O = "O"
CENTER_MOVE = "11"
INPUT_PREFIX = "tile_"

_MOVE_LABELS = {
    "00": "Top Left",
    "10": "Top Mid",
    "20": "Top Right",
    "01": "Mid Left",
    "11": "Center",
    "21": "Mid Right",
    "02": "Bottom Left",
    "12": "Bottom Mid",
    "22": "Bottom Right",
}

_WIN_PATTERNS = (
    (((0, 0), (1, 0), (2, 0)), "top row"),
    (((0, 1), (1, 1), (2, 1)), "middle row"),
    (((0, 2), (1, 2), (2, 2)), "bottom row"),
    (((0, 0), (0, 1), (0, 2)), "left column"),
    (((1, 0), (1, 1), (1, 2)), "middle column"),
    (((2, 0), (2, 1), (2, 2)), "right column"),
    (((0, 0), (1, 1), (2, 2)), "main diagonal"),
    (((2, 0), (1, 1), (0, 2)), "anti-diagonal"),
)


def _new_board() -> Board:
    return [[EMPTY for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]


def _copy_board(board: Board) -> Board:
    return [list(row) for row in board]


class TicTacToeGame(ReplayableGame):
    metadata = GameMetadata(
        key="tictactoe",
        name="Tic-Tac-Toe",
        summary="The classic game of Xs and Os, brought to Discord.",
        description="Take turns placing X and O until one player gets three in a row.",
        move_group_description="Commands for TicTacToe",
        player_count=2,
        author="@quantumbagel",
        version="3.0",
        author_link="https://github.com/quantumbagel",
        source_link="https://github.com/PlayCord/bot/blob/main/playcord/games/tictactoe.py",
        time="2min",
        difficulty="Easy",
        bots={
            "easy": BotDefinition(
                description="Picks a random legal move",
                callback=handler("bot_easy"),
            ),
            "medium": BotDefinition(
                description="Tries to win or block; otherwise picks center or random",
                callback=handler("bot_medium"),
            ),
            "hard": BotDefinition(
                description="Never misses a winning move",
                callback=handler("bot_hard"),
            ),
        },
        moves=(
            Move(
                name="move",
                description="Place a piece down.",
                options=(
                    MoveParameter(
                        name="move",
                        description="Board position",
                        kind=ParameterKind.string,
                        autocomplete=handler("autocomplete_move"),
                    ),
                ),
            ),
        ),
        peek_callback=handler("peek_status"),
    )

    def __init__(
        self,
        players: list[Player],
        *,
        match_options: dict[str, object] | None = None,
    ) -> None:
        super().__init__(players, match_options=match_options)
        self.board = _new_board()
        self.turn = 0
        self.last_error: str | None = None

    async def main(self) -> Outcome:
        while True:
            outcome = self._outcome_for_board(self.board)
            if outcome is not None:
                await self.update_message(
                    "board",
                    self._layout(game_over=True),
                    purpose="board",
                )
                return outcome

            actor = self.current_player()
            available_inputs = [
                ButtonInput(
                    id=f"{INPUT_PREFIX}{move}",
                    label=self._button_label(move),
                    arguments={"move": move},
                    style="primary",
                )
                for move in self._available_moves(self.board)
            ]
            result = await self.request_input(
                [actor],
                [*available_inputs, CommandInput(id="command_move", command_name="move")],
                timeout=300,
                message_id="board",
                layout=self._layout(game_over=False),
                purpose="board",
            )
            if not isinstance(result, GameInput):
                continue
            move = self._move_from_input(result)
            if move is None or move not in self._available_moves(self.board):
                self.last_error = "Choose an open tile."
                continue

            col, row = self._parse_move(move) or (0, 0)
            self.board[row][col] = self._marker_for_player(result.actor, self.players)
            self.last_error = None
            await self.record_move(
                result.actor,
                "move",
                {"move": move},
                source=result.source,
                input_id=result.input_id,
            )
            if self._outcome_for_board(self.board) is None:
                self.turn = (self.turn + 1) % len(self.players)

    def current_player(self) -> Player:
        return self.players[self.turn % len(self.players)]

    def match_global_summary(self, outcome: Outcome) -> str | None:
        if outcome.kind == "draw":
            return "Draw"
        if outcome.kind == "winner" and outcome.placements:
            winner = outcome.placements[0][0]
            if outcome.reason:
                return f"{winner.mention} won by taking the {outcome.reason}"
            return f"{winner.mention} won"
        if outcome.kind == "interrupted":
            return "Interrupted"
        return None

    def match_summary(self, outcome: Outcome) -> dict[int, str] | None:
        if outcome.kind == "draw":
            return {int(player.id): "Draw" for player in self.players}
        if outcome.kind == "winner" and outcome.placements:
            winners = {int(player.id) for player in outcome.placements[0]}
            return {
                int(player.id): ("Win" if int(player.id) in winners else "Loss")
                for player in self.players
            }
        if outcome.kind == "interrupted":
            return {int(player.id): "Interrupted" for player in self.players}
        return None

    def autocomplete_move(
        self,
        actor: Player,
        current: str,
        *,
        ctx: GameContext,
    ) -> list[tuple[str, str]]:
        _ = actor
        _ = ctx
        query = current.lower().strip()
        values: list[tuple[str, str]] = []
        for move in self._available_moves(self.board):
            label = _MOVE_LABELS.get(move, move)
            if query and query not in label.lower() and query not in move:
                continue
            values.append((label, move))
        return values[:25]

    def bot_easy(
        self,
        player: Player,
        *,
        request: Any,
        ctx: GameContext,
    ) -> dict[str, object] | None:
        _ = request
        _ = ctx
        return self._bot_decision(player, "easy")

    def bot_medium(
        self,
        player: Player,
        *,
        request: Any,
        ctx: GameContext,
    ) -> dict[str, object] | None:
        _ = request
        _ = ctx
        return self._bot_decision(player, "medium")

    def bot_hard(
        self,
        player: Player,
        *,
        request: Any,
        ctx: GameContext,
    ) -> dict[str, object] | None:
        _ = request
        _ = ctx
        return self._bot_decision(player, "hard")

    def peek_status(self, *, ctx: GameContext) -> str | None:
        _ = ctx
        return self._status_line()

    def initial_replay_state(self, ctx: GameContext) -> ReplayState | None:
        return ReplayState(
            game_key=ctx.game_key,
            players=list(ctx.players),
            match_options=dict(ctx.match_options),
            move_index=0,
            state={"board": _new_board(), "turn": 0},
        )

    def apply_replay_event(
        self,
        state: ReplayState,
        event: dict[str, Any],
    ) -> ReplayState | None:
        if event.get("type") != "move":
            return state
        arguments = event.get("arguments")
        if not isinstance(arguments, dict):
            return state
        move = str(arguments.get("move", ""))
        parsed = self._parse_move(move)
        if parsed is None:
            return state
        raw = state.state if isinstance(state.state, dict) else {}
        board = _copy_board(raw.get("board", _new_board()))
        turn = int(raw.get("turn", 0) or 0)
        col, row = parsed
        if board[row][col] != EMPTY:
            return state
        players = list(state.players)
        if not players:
            return state
        board[row][col] = MARK_X if turn % len(players) == 0 else MARK_O
        if self._outcome_for_board(board) is None:
            turn = (turn + 1) % len(players)
        return ReplayState(
            game_key=state.game_key,
            players=players,
            match_options=dict(state.match_options),
            move_index=state.move_index + 1,
            state={"board": board, "turn": turn},
        )

    def render_replay(self, state: ReplayState) -> MessageLayout | None:
        raw = state.state if isinstance(state.state, dict) else {}
        board = _copy_board(raw.get("board", _new_board()))
        turn = int(raw.get("turn", 0) or 0)
        outcome = self._outcome_for_board(board)
        if outcome is not None:
            content = self._board_text(board)
        elif state.players:
            player = state.players[turn % len(state.players)]
            marker = MARK_X if turn % len(state.players) == 0 else MARK_O
            content = f"{self._board_text(board)}\n\nTurn: {player.mention} ({marker})"
        else:
            content = self._board_text(board)
        return MessageLayout(content=content)

    def _layout(self, *, game_over: bool) -> MessageLayout:
        content = self._status_line()
        if self.last_error:
            content = f"{content}\n\n{self.last_error}"
        return MessageLayout(
            content=f"{content}\n\n`/tictactoe move` also works.",
            buttons=self._board_buttons(game_over=game_over),
            button_row_width=BOARD_SIZE,
        )

    def _status_line(self) -> str:
        outcome = self._outcome_for_board(self.board)
        if outcome is None:
            return (
                f"{self._board_text(self.board)}\n\n"
                f"Turn: {self.current_player().mention} "
                f"({self._marker_for_player(self.current_player(), self.players)})"
            )
        if outcome.kind == "draw":
            return f"{self._board_text(self.board)}\n\nDraw."
        if outcome.placements:
            winner = outcome.placements[0][0]
            return f"{self._board_text(self.board)}\n\nWinner: {winner.mention}"
        return self._board_text(self.board)

    def _board_buttons(self, *, game_over: bool) -> tuple[ButtonInput, ...]:
        buttons: list[ButtonInput] = []
        for row in range(BOARD_SIZE):
            for col in range(BOARD_SIZE):
                move = f"{col}{row}"
                mark = self.board[row][col]
                buttons.append(
                    ButtonInput(
                        id=f"{INPUT_PREFIX}{move}",
                        label=mark if mark != EMPTY else self._button_label(move),
                        arguments={"move": move},
                        style="secondary" if mark != EMPTY else "primary",
                        disabled=game_over or mark != EMPTY,
                    ),
                )
        return tuple(buttons)

    @staticmethod
    def _button_label(move: str) -> str:
        return {
            "00": "TL",
            "10": "TM",
            "20": "TR",
            "01": "ML",
            "11": "C",
            "21": "MR",
            "02": "BL",
            "12": "BM",
            "22": "BR",
        }.get(move, move)

    def _move_from_input(self, result: GameInput) -> str | None:
        if result.source == "button":
            raw = result.arguments.get("move")
        elif result.source == "bot":
            raw = result.arguments.get("move")
        else:
            raw = result.arguments.get("move")
        return str(raw) if raw is not None else None

    def _bot_decision(self, player: Player, difficulty: str) -> dict[str, object] | None:
        move = self._bot_move_for_difficulty(player, difficulty)
        if move is None:
            return None
        return {
            "input_id": f"{INPUT_PREFIX}{move}",
            "arguments": {"move": move},
        }

    def _bot_move_for_difficulty(self, player: Player, difficulty: str) -> str | None:
        available = self._available_moves(self.board)
        if not available:
            return None
        if difficulty == "easy":
            return random.choice(available)
        own = self._marker_for_player(player, self.players)
        opponent = MARK_O if own == MARK_X else MARK_X
        winning = self._winning_move_for(own)
        if winning is not None:
            return winning
        blocking = self._winning_move_for(opponent)
        if blocking is not None:
            return blocking
        if difficulty == "hard":
            fork = self._best_minimax_move(own)
            if fork is not None:
                return fork
        if CENTER_MOVE in available:
            return CENTER_MOVE
        return random.choice(available)

    def _winning_move_for(self, marker: str) -> str | None:
        for move in self._available_moves(self.board):
            parsed = self._parse_move(move)
            if parsed is None:
                continue
            col, row = parsed
            board = _copy_board(self.board)
            board[row][col] = marker
            outcome = self._outcome_for_board(board)
            if outcome is not None and outcome.kind == "winner":
                return move
        return None

    def _best_minimax_move(self, marker: str) -> str | None:
        opponent = MARK_O if marker == MARK_X else MARK_X

        def score(board: Board, active: str) -> int:
            outcome = self._outcome_for_board(board)
            if outcome is not None:
                if outcome.kind == "draw":
                    return 0
                return -1 if active == marker else 1
            moves = self._available_moves(board)
            scores = []
            for move in moves:
                col, row = self._parse_move(move) or (0, 0)
                next_board = _copy_board(board)
                next_board[row][col] = active
                scores.append(score(next_board, opponent if active == marker else marker))
            return max(scores) if active == marker else min(scores)

        best: tuple[int, str] | None = None
        for move in self._available_moves(self.board):
            col, row = self._parse_move(move) or (0, 0)
            board = _copy_board(self.board)
            board[row][col] = marker
            value = score(board, opponent)
            if best is None or value > best[0]:
                best = (value, move)
        return best[1] if best is not None else None

    def _outcome_for_board(self, board: Board) -> Outcome | None:
        for cells, reason in _WIN_PATTERNS:
            values = [board[row][col] for col, row in cells]
            if values[0] != EMPTY and values.count(values[0]) == len(values):
                marker = values[0]
                winner = self.players[0] if marker == MARK_X else self.players[1]
                loser = self.players[1] if marker == MARK_X else self.players[0]
                return Outcome(kind="winner", placements=[[winner], [loser]], reason=reason)
        if not self._available_moves(board):
            return Outcome(kind="draw", placements=[list(self.players)], reason="board full")
        return None

    @staticmethod
    def _available_moves(board: Board) -> list[str]:
        return [
            f"{col}{row}"
            for row in range(BOARD_SIZE)
            for col in range(BOARD_SIZE)
            if board[row][col] == EMPTY
        ]

    @staticmethod
    def _parse_move(value: str) -> MoveCoord | None:
        if len(value) != 2 or not value.isdigit():
            return None
        col, row = int(value[0]), int(value[1])
        if not (0 <= col < BOARD_SIZE and 0 <= row < BOARD_SIZE):
            return None
        return col, row

    @staticmethod
    def _marker_for_player(player: Player, players: list[Player]) -> str:
        if players and int(player.id) == int(players[0].id):
            return MARK_X
        return MARK_O

    @staticmethod
    def _board_text(board: Board) -> str:
        return "\n".join(
            "`" + " | ".join(cell if cell != EMPTY else " " for cell in row) + "`"
            for row in board
        )


register_game(TicTacToeGame)
