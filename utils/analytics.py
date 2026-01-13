"""
Analytics module for the bot
Tracks events like game starts, game completions, command usage, etc.
"""
import logging
import time
from datetime import datetime
from enum import Enum
from typing import Any

from utils.database import database as db

logger = logging.getLogger("playcord.analytics")


class EventType(Enum):
    """Types of events that can be tracked"""
    GAME_STARTED = "game_started"
    GAME_COMPLETED = "game_completed"
    GAME_ABANDONED = "game_abandoned"
    MATCHMAKING_STARTED = "matchmaking_started"
    MATCHMAKING_COMPLETED = "matchmaking_completed"
    MATCHMAKING_CANCELLED = "matchmaking_cancelled"
    PLAYER_JOINED = "player_joined"
    PLAYER_LEFT = "player_left"
    COMMAND_USED = "command_used"
    MOVE_MADE = "move_made"
    ERROR_OCCURRED = "error_occurred"
    BOT_STARTED = "bot_started"
    GUILD_JOINED = "guild_joined"
    GUILD_LEFT = "guild_left"


# In-memory event buffer (for batching writes to database)
_event_buffer: list[dict] = []
_buffer_size = 100  # Flush after this many events


def register_event(event_type: EventType, metadata: dict[str, Any] = None,
                   user_id: int = None, guild_id: int = None, game_type: str = None) -> None:
    """
    Register an analytics event.

    :param event_type: The type of event
    :param metadata: Additional metadata for the event
    :param user_id: The user who triggered the event (optional)
    :param guild_id: The guild where the event occurred (optional)
    :param game_type: The game type involved (optional)
    """
    global _event_buffer

    event = {
        "event_type": event_type.value if isinstance(event_type, EventType) else event_type,
        "timestamp": datetime.now().isoformat(),
        "user_id": user_id,
        "guild_id": guild_id,
        "game_type": game_type,
        "metadata": metadata or {}
    }

    _event_buffer.append(event)
    logger.debug(f"Registered event: {event_type.value if isinstance(event_type, EventType) else event_type}")

    # Flush buffer if it's full
    if len(_event_buffer) >= _buffer_size:
        flush_events()


def flush_events() -> int:
    """
    Flush all buffered events to storage.

    :return: Number of events flushed
    """
    global _event_buffer

    if not _event_buffer:
        return 0

    count = len(_event_buffer)

    if db:
        try:
            for event in _event_buffer:
                db.record_analytics_event(
                    event_type=event["event_type"],
                    user_id=event["user_id"],
                    guild_id=event["guild_id"],
                    game_type=event["game_type"],
                    metadata=event["metadata"]
                )
            logger.info(f"Flushed {count} analytics events to database.")
        except Exception as e:
            logger.error(f"Failed to flush analytics events to database: {e}")
            # we keep the buffer so we can try again next time?
            # for now let's just clear it to avoid memory leaks if DB is down permanently
    else:
        logger.warning("Database not connected, cannot flush analytics events.")

    _event_buffer = []
    return count


def get_event_stats() -> dict[str, int]:
    """
    Get statistics on buffered events.

    :return: Dictionary of event type counts
    """
    stats = {}
    for event in _event_buffer:
        event_type = event["event_type"]
        stats[event_type] = stats.get(event_type, 0) + 1
    return stats


class Timer:
    """Timer utility for measuring execution time."""

    def __init__(self):
        self._start_time = None

    @property
    def current_time(self):
        """Get the current elapsed time in milliseconds."""
        if self._start_time is not None:
            return round((time.perf_counter() - self._start_time) * 1000, 4)
        return 0

    def start(self):
        """Start a new timer"""

        self._start_time = time.perf_counter()
        return self

    def stop(self, use_ms=True, round_digits=4):
        """Stop the timer, and report the elapsed time"""
        if self._start_time is None:
            return None

        elapsed_time = time.perf_counter() - self._start_time
        self._start_time = None
        if use_ms:
            return round(elapsed_time * 1000, round_digits)
        else:
            return round(elapsed_time, round_digits)
