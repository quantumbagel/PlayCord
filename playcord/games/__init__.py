"""Canonical game registry exports."""

from playcord.api.plugin import get_registered_game, iter_registered_games
from playcord.games import secret_hitler as _secret_hitler  # noqa: F401
from playcord.games import tictactoe as _tictactoe  # noqa: F401

GAMES = list(iter_registered_games())
GAME_BY_KEY = {game.key: game for game in GAMES}

__all__ = ["GAMES", "GAME_BY_KEY", "get_registered_game", "iter_registered_games"]
