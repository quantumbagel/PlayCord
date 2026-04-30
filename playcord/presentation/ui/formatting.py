"""Discord-oriented string formatting for embeds and lobby tables."""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from playcord.infrastructure.constants import LOGGING_ROOT, LONG_SPACE_EMBED
from playcord.infrastructure.locale import get

if TYPE_CHECKING:
    from discord import User

    from playcord.infrastructure.database.implementation.internal_player import (
        InternalPlayer,
    )


def discord_user_mention(user_id: int | None) -> str:
    """Discord user mention from a numeric id (no discord.py object required)."""
    if user_id is None:
        return "@?"
    return f"<@{user_id}>"


def player_display_label(user: Any) -> str:
    """Discord mention for humans; bot name and difficulty for AI players."""
    if getattr(user, "is_bot", False):
        base = getattr(user, "display_name", None) or getattr(user, "name", None) or "Bot"
        diff = getattr(user, "bot_difficulty", None)
        if diff:
            return f"{base} ({diff})"
        return str(base)
    return discord_user_mention(getattr(user, "id", None))


def column_names(players: list[InternalPlayer] | set[InternalPlayer]) -> str:
    """Convert a list of players into a string representing the list of players.

    @player
    @player2
    """
    rendered_names = [player_display_label(user) for user in players]
    return "\n".join(rendered_names)


def column_elo(
    players: list[InternalPlayer] | set[InternalPlayer],
    game_type: str,
) -> str:
    """Convert a list of players into a string representing the list of players.

    238
    237?
    """
    ratings = []
    for user in players:
        if getattr(user, "is_bot", False):
            ratings.append(get("queue.bot_display_rating"))
        else:
            ratings.append(user.get_formatted_elo(game_type))
    return "\n".join(ratings)


def column_creator(
    players: list[InternalPlayer] | set[InternalPlayer],
    creator: InternalPlayer | User,
) -> str:
    """Convert a list of players into a string representing the list of players's creator status.

    Creator
    <blank>
    """
    return "\n".join(
        [
            (
                "✅"
                if (not getattr(u, "is_bot", False) and u.id == creator.id)
                else LONG_SPACE_EMBED
            )
            for u in players
        ],
    )


def _turn_marker_ids(turn: Any | None) -> set[int]:
    """Normalize a single player or an iterable of players into id flags."""
    if turn is None:
        return set()
    if isinstance(turn, (tuple, list, frozenset, set)):
        return {int(getattr(p, "id")) for p in turn}
    return {int(getattr(turn, "id"))}


def column_turn(
    players: list[InternalPlayer] | set[InternalPlayer],
    turn: InternalPlayer | User | None | Iterable[InternalPlayer | User] = None,
) -> str:
    """Mark players whose turn (or eligible turn) it is: one or many.

    ``turn`` may be a single player, a sequence of eligible players, or None.
    """
    eligible_ids = _turn_marker_ids(turn)
    return "\n".join(
        [
            "✅" if int(getattr(u, "id")) in eligible_ids else LONG_SPACE_EMBED
            for u in players
        ],
    )


def textify(basis: dict[str, float], replacements: dict[str, str]) -> str:
    """Randomly pick a message and fill variables
    :param basis: A list of messages
    :param replacements: A list of things to replace
    (ex: "The {person} rolls..." with argument {"person": "John Wick"}
    -> "The John Wick rolls..."
    :return: the randomly generated string.
    """
    random_float = random.random()  # Pick a number between 0 and 1
    actually_picked_message = None

    if not basis:  # Make sure there is
        return f"{LOGGING_ROOT}.textify - CRITICAL - received empty input for basis"

    # Here's how this code block works
    # we have probabilities:
    # 0.3 Message 1 (0 <= random_float <= 0.3)
    # 0.3 Message 2 (0.3 < random_float <= 0.6)
    # 0.2 Message 3 (0.6 < random_float <= 0.8)
    # 0.2 Message 4 (0.8 < random_float <= 1.0)
    for possible_message in basis:
        if random_float > basis[possible_message]:  # keep going
            random_float -= basis[possible_message]
            continue
        # random_float falls into this probability block
        actually_picked_message = possible_message
        break

    if actually_picked_message is None:
        # This is not an error because possible_message
        # must be defined because of the empty check
        actually_picked_message = possible_message

    # Replace the strings with their replacements (great english)
    for replacement in replacements:
        actually_picked_message = actually_picked_message.replace(
            "{" + replacement + "}",
            replacements[replacement],
        )

    return actually_picked_message


def player_representative(possible_players: list[int]):
    """Turns a list of players into a string representing the list of possible players
    e.g. [2, 3, 4, 5] -> 2-5, [2,3,5] -> 2-3, 5
    :param possible_players:
    :return: string representing the the amount.
    """
    if isinstance(possible_players, int):
        return str(possible_players)
    nums = sorted(set(possible_players))

    result = []
    start = nums[0]
    for i in range(1, len(nums) + 1):
        # Check if the current number is not consecutive
        if i == len(nums) or nums[i] != nums[i - 1] + 1:
            # If there's a range (start != nums[i-1]),
            # add range, else just a single number
            if start == nums[i - 1]:
                result.append(str(start))
            else:
                result.append(f"{start}-{nums[i - 1]}")
            if i < len(nums):
                start = nums[i]

    return ", ".join(result)


def player_verification_function(possible_players: list[int] | int):
    """Function that returns a lambda representing a function checking if an argument is in the list of possible players
     (or equal to a number)
    :param possible_players: either an integer or a
    list of integers representing the possible player count    :return: a function that checks if an argument
    is in the list of possible player counts.
    """
    if isinstance(possible_players, int):  # One number
        return lambda x: x == possible_players
    # Many numbers
    return lambda x: x in set(possible_players)


def format_replay_event_line(evt: dict[str, Any]) -> str:
    """One human-readable line per replay event (Discord markdown-safe, no raw newlines)."""
    t = evt.get("type") or "?"
    if t == "move":
        mn = evt.get("move_number", "?")
        uid = evt.get("user_id")
        cmd = evt.get("command_name") or evt.get("python_callback") or "?"
        args = evt.get("arguments", {})
        if isinstance(args, dict):
            arg_s = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
        else:
            arg_s = str(args)
        if len(arg_s) > 140:
            arg_s = arg_s[:137] + "..."
        who = f"user {uid}" if uid is not None else "system"
        return f"#{mn} · {who} · `{cmd}` · {arg_s}"
    raw = json.dumps(evt, ensure_ascii=False, separators=(",", ":"))
    if len(raw) > 300:
        return raw[:297] + "..."
    return raw


def chunk_replay_lines(
    lines: list[str],
    *,
    per_page: int = 12,
    max_chars: int = 3200,
) -> list[str]:
    """Split lines into pages that fit a single embed description (with code fence)."""
    if not lines:
        return ["(no lines)"]
    pages: list[str] = []
    buf: list[str] = []
    char_count = 0
    for line in lines:
        line_len = len(line) + 1
        if buf and (len(buf) >= per_page or char_count + line_len > max_chars):
            pages.append("\n".join(buf))
            buf = []
            char_count = 0
        buf.append(line)
        char_count += line_len
    if buf:
        pages.append("\n".join(buf))
    return pages
