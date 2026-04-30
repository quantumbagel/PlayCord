"""Analytics module for the bot
Tracks events like game starts, game completions, command usage, etc.
"""

import json
import time
from typing import Any

from playcord.infrastructure.config import get_settings
from playcord.infrastructure.constants import VERSION
from playcord.infrastructure.database.models import EventType
from playcord.infrastructure.logging import get_logger

logger = get_logger("analytics")


def _record_via_container(
    et: str,
    meta: dict[str, Any],
    *,
    user_id: int | None,
    guild_id: int | None,
    game_type: str | None,
    match_id: int | None,
) -> bool:
    try:
        from playcord.application.runtime_context import try_get_container

        c = try_get_container()
        if c is None:
            return False
        payload = dict(meta)
        payload.setdefault("user_id", user_id)
        payload.setdefault("guild_id", guild_id)
        payload.setdefault("game_type", game_type)
        payload.setdefault("match_id", match_id)
        c.analytics.record(et, payload)
        return True
    except Exception:
        return False


# Fallback buffer when DB write fails (retry on flush)
_event_buffer: list[dict] = []


def register_event(
    event_type: EventType | str,
    metadata: dict[str, Any] | None = None,
    user_id: int | None = None,
    guild_id: int | None = None,
    game_type: str | None = None,
    match_id: int | None = None,
    command_name: str | None = None,
    latency_ms: float | None = None,
    outcome: str | None = None,
) -> None:
    """Register an analytics event (written to the database immediately when connected)."""
    et = event_type.value if isinstance(event_type, EventType) else str(event_type)
    meta = dict(metadata or {})
    meta.setdefault("bot_version", VERSION)
    if command_name is not None:
        meta.setdefault("command_name", command_name)
    if latency_ms is not None:
        meta.setdefault("latency_ms", round(float(latency_ms), 2))
    if outcome is not None:
        meta.setdefault("outcome", str(outcome))

    if _record_via_container(
        et,
        meta,
        user_id=user_id,
        guild_id=guild_id,
        game_type=game_type,
        match_id=match_id,
    ):
        logger.debug("Recorded analytics event: %s", et)
        return

    global _event_buffer
    _event_buffer.append(
        {
            "event_type": et,
            "user_id": user_id,
            "guild_id": guild_id,
            "game_type": game_type,
            "match_id": match_id,
            "metadata": meta,
        },
    )
    if len(_event_buffer) >= 20:
        flush_events()


def flush_events() -> int:
    """Flush buffered events (after failed writes) to storage.

    :return: Number of events flushed
    """
    global _event_buffer

    if not _event_buffer:
        return 0

    count = len(_event_buffer)
    events_to_flush = _event_buffer.copy()

    from playcord.application.runtime_context import try_get_container

    c = try_get_container()
    if c is None:
        logger.debug(
            "Application container not bound; keeping %s buffered analytics events.",
            count,
        )
        return 0

    flushed = 0
    try:
        try:
            c.guilds_repository.cleanup_old_analytics(days=get_settings().analytics_retention_days)
        except Exception:
            logger.debug("Analytics cleanup skipped during flush", exc_info=True)
        repo = c.analytics_repository
        for event in events_to_flush:
            payload: dict[str, Any] = dict(event.get("metadata") or {})
            payload.setdefault("user_id", event.get("user_id"))
            payload.setdefault("guild_id", event.get("guild_id"))
            payload.setdefault("game_type", event.get("game_type"))
            payload.setdefault("match_id", event.get("match_id"))
            repo.record_event(event["event_type"], payload)
            flushed += 1
        _event_buffer = []
        if flushed:
            logger.info("Flushed %s buffered analytics events.", flushed)
    except Exception as e:
        logger.exception("Failed to flush analytics events: %s", e)
        if len(_event_buffer) > 500:
            _event_buffer = _event_buffer[-250:]

    return flushed


def get_event_stats() -> dict[str, int]:
    """Get statistics on buffered events.

    :return: Dictionary of event type counts
    """
    stats: dict[str, int] = {}
    for event in _event_buffer:
        t = event["event_type"]
        stats[t] = stats.get(t, 0) + 1
    return stats


class Timer:
    """Timer utility for measuring execution time."""

    def __init__(self) -> None:
        self._start_time = None

    @property
    def current_time(self):
        """Get the current elapsed time in milliseconds."""
        if self._start_time is not None:
            return round((time.perf_counter() - self._start_time) * 1000, 4)
        return 0

    def start(self):
        """Start a new timer."""
        self._start_time = time.perf_counter()
        return self

    def stop(self, use_ms=True, round_digits=4):
        """Stop the timer, and report the elapsed time."""
        if self._start_time is None:
            return None

        elapsed_time = time.perf_counter() - self._start_time
        self._start_time = None
        if use_ms:
            return round(elapsed_time * 1000, round_digits)
        return round(elapsed_time, round_digits)


def format_ascii_bar_chart(
    rows: list[dict[str, Any]],
    *,
    value_key: str = "cnt",
    label_key: str = "event_type",
    width: int = 22,
) -> list[str]:
    """Turn count rows into simple Unicode bar lines for Discord (monospace-friendly)."""
    if not rows:
        return []
    mx = max(int(r[value_key]) for r in rows) or 1
    lines: list[str] = []
    for r in rows:
        label = str(r.get(label_key) or "?")
        v = int(r[value_key])
        filled = max(1, round(width * v / mx)) if mx else width
        bar = "█" * filled + "░" * (width - filled)
        lines.append(f"`{label}` {bar} {v}")
    return lines


def format_recent_event_row(row: dict[str, Any]) -> str:
    """One line for owner-facing analytics dump (Discord-safe length)."""
    meta = row.get("metadata")
    if meta is not None and not isinstance(meta, dict):
        try:
            meta = dict(meta)
        except Exception:
            meta = {"_raw": str(meta)[:80]}
    meta_s = ""
    if meta:
        try:
            meta_s = json.dumps(meta, separators=(",", ":"), ensure_ascii=False)
        except TypeError:
            meta_s = str(meta)
        if len(meta_s) > 120:
            meta_s = meta_s[:117] + "..."
    ts = row.get("created_at") or row.get("timestamp")
    ts_s = ts.isoformat()[:19] if hasattr(ts, "isoformat") else str(ts)[:19]
    return (
        f"`{row.get('event_id')}` **{row.get('event_type')}** {ts_s} "
        f"u={row.get('user_id')} g={row.get('guild_id')} "
        f"match={row.get('match_id')} {meta_s}"
    )


def render_analytics_markdown_summary(
    event_counts: list[dict[str, Any]],
    game_counts: list[dict[str, Any]],
    recent: list[dict[str, Any]],
    hours: int,
) -> list[str]:
    lines = [f"Window: last {hours} hour(s)"]
    lines.append("Events by type:")
    lines.extend(format_ascii_bar_chart(event_counts) or ["_(none)_"])
    lines.append("Games:")
    lines.extend(
        format_ascii_bar_chart(game_counts, label_key="game_type") or ["_(none)_"],
    )
    lines.append("Recent events:")
    lines.extend([format_recent_event_row(row) for row in recent[:12]] or ["_(none)_"])
    return lines
