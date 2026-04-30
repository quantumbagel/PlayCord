"""Repository exports."""

from playcord.infrastructure.database.implementation.repositories.analytics import (
    AnalyticsRepository,
)
from playcord.infrastructure.database.implementation.repositories.game import (
    GameRepository,
)
from playcord.infrastructure.database.implementation.repositories.guild import (
    GuildRepository,
)
from playcord.infrastructure.database.implementation.repositories.history import (
    MatchRepository,
    ReplayRepository,
)
from playcord.infrastructure.database.implementation.repositories.leaderboard import (
    LeaderboardRepository,
)
from playcord.infrastructure.database.implementation.repositories.maintenance import (
    MaintenanceRepository,
)
from playcord.infrastructure.database.implementation.repositories.move import (
    MoveRepository,
)
from playcord.infrastructure.database.implementation.repositories.rating import (
    RatingRepository,
)
from playcord.infrastructure.database.implementation.repositories.roles import (
    RoleRepository,
)
from playcord.infrastructure.database.implementation.repositories.user import (
    PlayerRepository,
)

__all__ = [
    "AnalyticsRepository",
    "GameRepository",
    "GuildRepository",
    "LeaderboardRepository",
    "MaintenanceRepository",
    "MatchRepository",
    "MoveRepository",
    "PlayerRepository",
    "RatingRepository",
    "ReplayRepository",
    "RoleRepository",
]
