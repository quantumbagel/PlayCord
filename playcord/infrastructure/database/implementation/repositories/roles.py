"""Role assignment persistence and retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playcord.infrastructure.database.implementation.database import Database


class RoleRepository:
    """Database operations for role assignments."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def save_role_assignments(
        self,
        match_id: int,
        assignments: list[tuple[int, str, int]],
    ) -> None:
        """Save role assignments for a match.

        Args:
            match_id: Match ID
            assignments: List of (player_id, role_id, seat_index) tuples

        """
        if not assignments:
            return

        with self.database.transaction() as cur:
            for player_id, role_id, seat_index in assignments:
                cur.execute(
                    """
                    INSERT INTO match_role_assignments
                        (match_id, player_id, role_id, seat_index)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (match_id, player_id)
                    DO UPDATE SET role_id = EXCLUDED.role_id,
                                  seat_index = EXCLUDED.seat_index;
                    """,
                    (match_id, player_id, role_id, seat_index),
                )

    def get_role_assignments(
        self,
        match_id: int,
    ) -> dict[int, tuple[str, int]]:
        """Retrieve role assignments for a match.

        Args:
            match_id: Match ID

        Returns:
            Dict mapping player_id to (role_id, seat_index) tuple

        """
        with self.database.transaction() as cur:
            cur.execute(
                """
                SELECT player_id, role_id, seat_index
                FROM match_role_assignments
                WHERE match_id = %s
                ORDER BY seat_index;
                """,
                (match_id,),
            )
            result = {}
            for row in cur.fetchall():
                player_id = row["player_id"]
                role_id = row["role_id"]
                seat_index = row["seat_index"]
                result[int(player_id)] = (str(role_id), int(seat_index))
            return result

    def get_player_role(
        self,
        match_id: int,
        player_id: int,
    ) -> str | None:
        """Get role for a specific player in a match.

        Args:
            match_id: Match ID
            player_id: Player ID

        Returns:
            Role ID or None if not assigned

        """
        with self.database.transaction() as cur:
            cur.execute(
                """
                SELECT role_id
                FROM match_role_assignments
                WHERE match_id = %s AND player_id = %s;
                """,
                (match_id, player_id),
            )
            row = cur.fetchone()
            return str(row["role_id"]) if row else None
