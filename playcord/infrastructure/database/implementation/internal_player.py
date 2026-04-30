"""Legacy internal player type used for Discord formatting (moved from Database)."""

from __future__ import annotations

from playcord.api.trueskill_config import get_trueskill_parameters
from playcord.core.player import Player
from playcord.core.rating import DEFAULT_MU, DEFAULT_SIGMA, STARTING_RATING
from playcord.infrastructure.constants import GAME_TYPES


class InternalPlayerRatingStatistic:
    """Rating statistic for a specific game."""

    def __init__(self, name: str, mu: float | None, sigma: float | None) -> None:
        self.name = name
        if mu is None:
            self.mu = DEFAULT_MU
            if name in GAME_TYPES:
                self.sigma = get_trueskill_parameters(name)["sigma"] * STARTING_RATING
            else:
                self.sigma = DEFAULT_SIGMA
            self.stored = False
        else:
            self.mu = mu
            self.sigma = sigma
            self.stored = True


class InternalPlayer:
    """Internal player representation with ratings (IDs and strings only)."""

    def __init__(
        self,
        ratings: dict[str, dict[str, float]],
        *,
        metadata: dict | None = None,
        user_id: int | None = None,
        username: str | None = None,
    ) -> None:
        self.name = username
        self.id = user_id

        if metadata is not None:
            self.metadata = metadata
        else:
            self.metadata = {}

        # No servers in new schema
        self.servers = []

        self.ratings = ratings
        self.player_data = {}
        self.moves_made = 0
        self.is_bot = False
        self.bot_difficulty = None

        self._update_ratings(self.ratings)

    def _update_ratings(self, ratings: dict[str, dict[str, float]]) -> None:
        """Update rating attributes from ratings dict."""
        rating_keys = set(GAME_TYPES) | set(ratings)
        for key in rating_keys:
            if key not in ratings:
                ratings[key] = {
                    "mu": DEFAULT_MU,
                    "sigma": get_trueskill_parameters(key)["sigma"] * STARTING_RATING,
                }
            setattr(
                self,
                key,
                InternalPlayerRatingStatistic(
                    key,
                    ratings[key]["mu"],
                    ratings[key]["sigma"],
                ),
            )

    @property
    def display_name(self) -> str:
        """Human-readable player name for table rendering."""
        if self.is_bot:
            base = self.name or "Bot"
            if self.bot_difficulty:
                return f"{base} ({self.bot_difficulty})"
            return base
        if self.name:
            return f"@{str(self.name).lstrip('@')}"
        return f"@{self.id}"

    @property
    def mention(self) -> str:
        """Discord mention format or bot display name."""
        if self.is_bot:
            return self.name or "Bot"
        return f"<@{self.id}>"

    def get_formatted_elo(
        self,
        game_type: str,
        include_global_rank: bool = False,
        game_id: int | None = None,
        global_rank: int | None = None,
    ) -> str:
        """Get formatted rating string with uncertainty indicator.

        :param game_type: The game type key (e.g., 'tictactoe')
        :param include_global_rank: If True, include global rank suffix for top players
        :param game_id: Required if include_global_rank is True
        :return: Formatted rating string like "1000" or "1000?".
        """
        rating = getattr(self, game_type, None)
        if rating is None or rating.mu is None:
            return "No Rating"

        conservative = float(rating.mu) - (3 * float(rating.sigma))
        if rating.sigma > 0.20 * STARTING_RATING:
            base_rating = str(round(conservative)) + "?"
        else:
            base_rating = str(round(conservative))

        # Rank decoration is supplied by the caller to avoid hidden DB I/O in the model.
        if include_global_rank and game_id is not None and global_rank is not None:
            if global_rank <= 100:  # Top 100 players
                if global_rank == 1:
                    base_rating += " 🏆 #1 Globally"
                elif global_rank <= 3:
                    base_rating += f" 🥇 Top {global_rank} Globally"
                elif global_rank <= 10:
                    base_rating += f" ⭐ Top {global_rank} Globally"
                elif global_rank <= 100:
                    base_rating += f" (Top {global_rank} Globally)"

        return base_rating

    def __eq__(self, other):
        if not isinstance(other, InternalPlayer):
            return False
        return self.id == other.id

    def __hash__(self):
        return hash(self.id)

    def __str__(self) -> str:
        return f"InternalPlayer({self.id})"

    def __repr__(self) -> str:
        return f"InternalPlayer(id={self.id}, is_bot={self.is_bot}, ratings={self.ratings})"


def internal_player_to_player(
    internal_player: InternalPlayer,
    game_type: str,
) -> Player:
    """Convert InternalPlayer to API Player object."""
    rating = getattr(internal_player, game_type)
    uid = internal_player.id
    uname = internal_player.name or (f"User {uid}" if uid is not None else "Unknown")
    return Player(
        mu=rating.mu,
        sigma=rating.sigma,
        ranking=None,
        id=uid,
        name=uname,
    )
