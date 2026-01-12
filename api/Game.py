from enum import Enum

from api.Command import Command
from api.MessageComponents import MessageComponent
from api.Player import Player


class PlayerOrder(Enum):
    """Enum for specifying player order behavior."""
    RANDOM = "random"  # Randomize player order (default)
    PRESERVE = "preserve"  # Keep the order players joined
    CREATOR_FIRST = "creator_first"  # Creator always goes first, rest randomized
    REVERSE = "reverse"  # Reverse the join order


class Game:
    """
    A generic, featureless Game object.

    Games should inherit from this class and implement the required methods.

    Class Attributes:
        begin_command_description (str): Description shown in /play command
        move_command_group_description (str): Description for move commands group
        description (str): Full description of the game
        name (str): Human-readable name of the game
        players (int | list[int]): Number of players allowed
        moves (list[Command]): List of move commands
        author (str): Game author
        version (str): Game version
        author_link (str): Link to author's page
        source_link (str): Link to source code
        time (str): Estimated game duration
        difficulty (str): Game difficulty level
        player_order (PlayerOrder): How to order players (default: RANDOM)
    """
    begin_command_description: str
    move_command_group_description: str
    description: str
    name: str
    players: int | list[int]
    moves: list[Command] = []
    author: str
    version: str
    author_link: str
    source_link: str
    time: str
    difficulty: str
    player_order: PlayerOrder = PlayerOrder.RANDOM

    def __init__(self, players: list[Player]) -> None:
        """
        Create a new Game instance.
        :param players: a list of Players representing who will play the game.
        """
        pass

    def state(self) -> list[MessageComponent]:
        """
        Return the current state of the game using MessageComponents.
        :return: a list of MessageComponents representing the game state.
        """
        pass

    def current_turn(self) -> Player:
        """
        Return the current Player whose turn it is.
        It is highly recommended to make this function O(1) runtime
        due to the relative frequency it is called
        :return: the Player whose turn it is.
        """
        pass

    def outcome(self) -> Player | list[list[Player]] | str:
        """
        Return the outcome of the game state.

        :return: one Player who has won the game
        :return: a list of lists representing the outcome of the game. Each index is a place ([first, second, third]),
         and the inner list represents the people who got that place
        :return: string representing an error
        """
        pass
