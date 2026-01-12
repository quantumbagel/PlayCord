import random

import discord
from discord import User

from configuration.constants import LOGGING_ROOT, LONG_SPACE_EMBED
from utils.database import InternalPlayer


def column_names(players: list[InternalPlayer] | set[InternalPlayer]) -> str:
    """
    Convert a list of players into a string representing the list of players

    @player
    @player2
    """
    return "\n".join([u.mention for u in players])


def column_elo(players: list[InternalPlayer] | set[InternalPlayer], game_type: str) -> str:
    """
    Convert a list of players into a string representing the list of players

    238
    237?
    """
    return "\n".join([u.get_formatted_elo(game_type) for u in players])


def column_creator(players: list[InternalPlayer] | set[InternalPlayer], creator: InternalPlayer | User) -> str:
    """
    Convert a list of players into a string representing the list of players's creator status

    Creator
    <blank>
    """
    return "\n".join(["✅" if u.id == creator.id else LONG_SPACE_EMBED for u in players])


def column_turn(players: list[InternalPlayer] | set[InternalPlayer], turn: InternalPlayer | User) -> str:
    """
    Convert a list of players into a string representing the list of players and whose turn it is

    ✅
    <blank>
    """
    return "\n".join(["✅" if ((turn is not None) and (u.id == turn.id)) else LONG_SPACE_EMBED for u in players])


def textify(basis: dict[str, float], replacements: dict[str, str]) -> str:
    """de
    Randomly pick a message and fill variables
    :param basis: A list of messages
    :param replacements: A list of things to replace
    (ex: "The {person} rolls..." with argument {"person": "John Wick"}
    -> "The John Wick rolls..."
    :return: the randomly generated string
    """
    random_float = random.random()  # Pick a number between 0 and 1
    actually_picked_message = None

    if not len(basis.keys()):  # Make sure there is
        return f"{LOGGING_ROOT}.textify - CRITICAL - received empty input for basis"

    # Here's how this code block works
    # we have probabilities:
    # 0.3 Message 1 (0 <= random_float <= 0.3)
    # 0.3 Message 2 (0.3 < random_float <= 0.6)
    # 0.2 Message 3 (0.6 < random_float <= 0.8)
    # 0.2 Message 4 (0.8 < random_float <= 1.0)
    for possible_message in basis.keys():
        if random_float > basis[possible_message]:  # keep going
            random_float -= basis[possible_message]
            continue
        else:  # random_float falls into this probability block
            actually_picked_message = possible_message
            break

    if actually_picked_message is None:
        # This is not an error because possible_message must be defined because of the empty check
        actually_picked_message = possible_message

    # Replace the strings with their replacements (great english)
    for replacement in replacements.keys():
        actually_picked_message = actually_picked_message.replace("{" + replacement + "}", replacements[replacement])

    return actually_picked_message


def player_representative(possible_players: list[int]):
    """
    Turns a list of players into a string representing the list of possible players
    e.g. [2, 3, 4, 5] -> 2-5, [2,3,5] -> 2-3, 5
    :param possible_players:
    :return: string representing the the amount
    """
    if type(possible_players) == int:
        return str(possible_players)
    nums = sorted(set(possible_players))

    result = []
    start = nums[0]
    for i in range(1, len(nums) + 1):
        # Check if the current number is not consecutive
        if i == len(nums) or nums[i] != nums[i - 1] + 1:
            # If there's a range (start != nums[i-1]), add range, else just a single number
            if start == nums[i - 1]:
                result.append(str(start))
            else:
                result.append(f"{start}-{nums[i - 1]}")
            if i < len(nums):
                start = nums[i]

    return ", ".join(result)


def player_verification_function(possible_players: list[int] | int):
    """
    function that returns a lambda representing a function checking if an argument is in the list of possible players
     (or equal to a number)
    :param possible_players: either an integer or a list of integers representing the possible player count
    :return: a function that checks if an argument is in the list of possible player counts
    """
    if type(possible_players) == int:  # One number
        return lambda x: x == possible_players
    else:  # Many numbers
        return lambda x: x in set(possible_players)


def contextify(ctx: discord.Interaction | discord.Member):
    """
    return a string representing detailed information about the interaction
    TODO: move to analytics module
    :param ctx: discord context
    :return: a string representing detailed information about the interaction
    """

    is_guild_command = ctx.guild is not None
    guild_id = ctx.guild.id if is_guild_command else None
    guild_name = ctx.guild.name if is_guild_command else None

    if type(ctx) == discord.Interaction:
        return f"guild_id={guild_id} guild_name={guild_name!r} user_id={ctx.user.id}, user_name={ctx.user.name}, is_bot={ctx.user.bot}, data={ctx.data}, type={ctx.type!r}"
    elif type(ctx) == discord.Member:
        return f"guild_id={guild_id} guild_name={guild_name!r} user_id={ctx.user.id}, user_name={ctx.user.name}, is_bot={ctx.user.bot}, data={ctx.data}, type={ctx.type!r}"
