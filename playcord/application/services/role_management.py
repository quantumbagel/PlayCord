"""Role management helpers for plugin-owned role system."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from playcord.api import RoleFlow
from playcord.infrastructure.logging import get_logger

if TYPE_CHECKING:
    from playcord.api import RoleAssignment, RuntimeGame

logger = get_logger("roles.management")


def has_role_support(game: RuntimeGame) -> bool:
    """Check if a game plugin supports roles.

    A game supports roles if:
    1. role_flow != none in metadata
    2. At least one role method is implemented
    """
    if not hasattr(game, "metadata"):
        return False

    role_flow = getattr(game.metadata, "role_flow", RoleFlow.none)
    if role_flow == RoleFlow.none:
        return False

    has_get_roles = hasattr(game, "get_roles") and callable(game.get_roles)
    has_validate = hasattr(game, "validate_roles") and callable(
        game.validate_roles,
    )
    has_assign = hasattr(game, "assign_roles") and callable(
        game.assign_roles,
    )
    has_options = hasattr(game, "role_selection_options") and callable(
        game.role_selection_options,
    )

    return has_get_roles or has_validate or has_assign or has_options


def should_show_role_selectors(game: RuntimeGame) -> bool:
    """Check if role selector UI should be shown."""
    if not has_role_support(game):
        return False

    role_flow = getattr(game.metadata, "role_flow", RoleFlow.none)
    return role_flow in (RoleFlow.selectable, RoleFlow.selectable_random)


def should_show_assign_button(game: RuntimeGame) -> bool:
    """Check if 'Assign Roles' button should be shown."""
    if not has_role_support(game):
        return False

    role_flow = getattr(game.metadata, "role_flow", RoleFlow.none)
    return role_flow == RoleFlow.selectable_random


def get_role_selection_options(
    game: RuntimeGame,
    player_ids: list[int],
) -> dict[int, tuple[Any, ...]]:
    """Get available role options for each player.

    Returns a dict mapping player_id to tuple of available Role objects.
    """
    if not should_show_role_selectors(game):
        return {}

    try:
        return game.role_selection_options(player_ids)
    except Exception as e:
        logger.exception(
            "Error getting role selection options from %s: %s",
            type(game).__name__,
            e,
        )
        return {}


def validate_role_selections(
    game: RuntimeGame,
    selections: dict[int, str],
) -> tuple[bool, str | None]:
    """Validate player role selections.

    Returns (is_valid, error_message_if_invalid)
    """
    if not has_role_support(game):
        return True, None

    try:
        result = game.validate_roles(selections)
        if isinstance(result, bool):
            return result, None if result else "Invalid role configuration"
        return False, str(result)
    except Exception as e:
        logger.exception(
            "Error validating roles for %s: %s",
            type(game).__name__,
            e,
        )
        return False, f"Role validation error: {e}"


def assign_roles(
    game: RuntimeGame,
    selections: dict[int, str] | None = None,
) -> list[RoleAssignment]:
    """Get final role assignment from plugin.

    Returns list of RoleAssignment objects with player_id, role_id, and seat_index.
    """
    if not has_role_support(game):
        return []

    try:
        return game.assign_roles(selections)
    except Exception as e:
        logger.exception(
            "Error assigning roles for %s: %s",
            type(game).__name__,
            e,
        )
        return []


def role_assignments_to_db_tuples(
    assignments: list[RoleAssignment],
) -> list[tuple[int, str, int]]:
    """Convert RoleAssignment objects to database tuples.

    Returns list of (player_id, role_id, seat_index) tuples.
    """
    return [(a.player_id, a.role_id, a.seat_index) for a in assignments]


def reorder_players_by_roles(
    players: list[Any],
    assignments: list[RoleAssignment],
) -> list[Any]:
    """Reorder players according to role seat assignments.

    Returns players sorted by their seat_index from role assignments.
    """
    if not assignments:
        return players

    by_id = {getattr(p, "id", None): p for p in players}
    by_seat: dict[int, Any] = {}

    for assignment in assignments:
        player = by_id.get(assignment.player_id)
        if player is not None:
            by_seat[assignment.seat_index] = player

    result = []
    for i in sorted(by_seat.keys()):
        result.append(by_seat[i])

    for player in players:
        if player not in result:
            result.append(player)

    return result
