"""Dependency injection container for the refactored application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from playcord.application.services.analytics import AnalyticsService
from playcord.application.services.game_session import GameSessionService
from playcord.application.services.matchmaker import Matchmaker
from playcord.application.services.rating import RatingService
from playcord.application.services.replay import ReplayService
from playcord.application.services.stats import StatsService
from playcord.infrastructure.database import (
    AnalyticsRepository,
    GameRepository,
    GuildRepository,
    MaintenanceRepository,
    MatchRepository,
    MigrationRunner,
    PlayerRepository,
    PoolManager,
    RatingRepository,
    ReplayRepository,
    RoleRepository,
)
from playcord.infrastructure.database.implementation.core.migrations import (
    apply_migrations,
)
from playcord.infrastructure.state.user_games import SessionRegistry

if TYPE_CHECKING:
    from playcord.infrastructure.config import Settings
    from playcord.infrastructure.locale import Translator


@dataclass(slots=True)
class ApplicationContainer:
    """Owns shared infrastructure and application services."""

    settings: Settings
    translator: Translator
    pool_manager: PoolManager
    migration_runner: MigrationRunner
    registry: SessionRegistry = field(default_factory=SessionRegistry)
    players_repository: PlayerRepository = field(init=False, repr=False, compare=False)
    games_repository: GameRepository = field(init=False, repr=False, compare=False)
    maintenance_repository: MaintenanceRepository = field(
        init=False,
        repr=False,
        compare=False,
    )
    matches_repository: MatchRepository = field(init=False, repr=False, compare=False)
    ratings_repository: RatingRepository = field(init=False, repr=False, compare=False)
    replays_repository: ReplayRepository = field(init=False, repr=False, compare=False)
    guilds_repository: GuildRepository = field(init=False, repr=False, compare=False)
    roles_repository: RoleRepository = field(init=False, repr=False, compare=False)
    analytics_repository: AnalyticsRepository = field(
        init=False,
        repr=False,
        compare=False,
    )
    analytics_service: AnalyticsService = field(init=False, repr=False, compare=False)
    replay_service: ReplayService = field(init=False, repr=False, compare=False)
    rating_service: RatingService = field(init=False, repr=False, compare=False)
    stats_service: StatsService = field(init=False, repr=False, compare=False)
    matchmaker: Matchmaker = field(init=False, repr=False, compare=False)
    game_session_service: GameSessionService = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        database = self.pool_manager.connect()
        apply_migrations(database)

        self.games_repository = GameRepository(database)
        self.ratings_repository = RatingRepository(
            database,
            self.games_repository,
            self.games_repository.leaderboard,
        )
        self.players_repository = PlayerRepository(
            database,
            self.games_repository,
            self.ratings_repository,
        )
        self.analytics_repository = AnalyticsRepository(database, self.games_repository)
        self.maintenance_repository = MaintenanceRepository(
            database,
            self.games_repository,
        )
        self.guilds_repository = GuildRepository(
            database,
            self.analytics_repository,
            self.players_repository,
            self.games_repository,
            self.maintenance_repository,
        )
        self.matches_repository = MatchRepository(
            database,
            self.players_repository,
            self.guilds_repository,
            self.games_repository,
            self.ratings_repository,
        )
        self.replays_repository = ReplayRepository(database)
        self.roles_repository = RoleRepository(database)

        self.migration_runner.run_startup(
            database,
            self.games_repository,
            self.analytics_repository,
            self.matches_repository,
        )

        self.analytics_service = AnalyticsService(self.analytics_repository)
        self.replay_service = ReplayService(self.replays_repository)
        self.rating_service = RatingService(self.players_repository)
        self.stats_service = StatsService(
            self.matches_repository,
            self.players_repository,
        )
        self.matchmaker = Matchmaker(self.registry)
        self.game_session_service = GameSessionService(
            registry=self.registry,
            matches=self.matches_repository,
            replays=self.replays_repository,
            ratings=self.players_repository,
        )

    def close(self) -> None:
        self.pool_manager.close()
