"""Canonical player model for PlayCord."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from playcord.core.rating import DEFAULT_MU, DEFAULT_SIGMA, Rating

BOT_ID_BASE = 9_000_000_000_000


@dataclass(slots=True)
class Player:
    """A player participating in a game or stored in the rating system."""

    id: int | str
    display_name: str | None = None
    rating: Rating = field(
        default_factory=lambda: Rating(
            mu=DEFAULT_MU,
            sigma=DEFAULT_SIGMA,
        ),
    )
    is_bot: bool = False
    bot_difficulty: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    player_data: dict[str, Any] = field(default_factory=dict)
    ranking: int | None = None

    @property
    def mention(self) -> str:
        if self.is_bot:
            base = self.display_name or "Bot"
            if self.bot_difficulty:
                return f"{base} ({self.bot_difficulty})"
            return base
        return f"<@{self.id}>"

    @property
    def name(self) -> str | None:
        return self.display_name

    @property
    def mu(self) -> float:
        return self.rating.mu

    @property
    def sigma(self) -> float:
        return self.rating.sigma

    @property
    def conservative_rating(self) -> float:
        return self.rating.conservative

    @property
    def display_rating(self) -> int:
        return round(self.rating.conservative)

    def get_formatted_elo(self, uncertainty_threshold: float = 0.20) -> str:
        return self.rating.display(uncertainty_threshold=uncertainty_threshold)

    @classmethod
    def create_bot(
        cls,
        name: str,
        difficulty: str,
        *,
        bot_index: int = 0,
        rating: Rating | None = None,
    ) -> Player:
        return cls(
            id=BOT_ID_BASE + bot_index,
            display_name=name,
            rating=rating or Rating(mu=DEFAULT_MU, sigma=DEFAULT_SIGMA),
            is_bot=True,
            bot_difficulty=difficulty,
        )

    @classmethod
    def from_legacy(cls, legacy: Any) -> Player:
        """Create a canonical player from either legacy player model."""
        display_name = getattr(legacy, "display_name", None) or getattr(
            legacy,
            "name",
            None,
        )
        rating = Rating(
            mu=float(getattr(legacy, "mu", DEFAULT_MU)),
            sigma=float(getattr(legacy, "sigma", DEFAULT_SIGMA)),
        )
        return cls(
            id=legacy.id,
            display_name=display_name,
            rating=rating,
            is_bot=bool(getattr(legacy, "is_bot", False)),
            bot_difficulty=getattr(legacy, "bot_difficulty", None),
            metadata=dict(getattr(legacy, "metadata", {}) or {}),
            player_data=dict(getattr(legacy, "player_data", {}) or {}),
            ranking=getattr(legacy, "ranking", None),
        )
