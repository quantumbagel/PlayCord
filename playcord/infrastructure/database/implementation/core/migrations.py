"""Apply versioned database migrations tracked in database_migrations.

Starting from 3.0.0 - full schema rebuild.
No backwards compatibility is maintained across baseline rebuilds.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from playcord.infrastructure.logging import get_logger

if TYPE_CHECKING:
    from playcord.infrastructure.database.implementation.database import Database

logger = get_logger("database.migrations")


def _load_migration_sql(filename: str) -> str:
    """Load SQL content from a migration SQL file."""
    sql_dir = Path(__file__).resolve().parent.parent / "sql"
    sql_path = sql_dir / filename
    with sql_path.open("r", encoding="utf-8") as fh:
        return fh.read()


MIGRATIONS: list[tuple[str, str, list[str]]] = [
    (
        "3.0.0",
        (
            "Rebuilt baseline schema for scalable matchmaking, ratings, replay,"
            " and analytics."
        ),
        [_load_migration_sql("schema.sql")],
    ),
    (
        "3.0.1",
        "Fix games.rating_config key validation to match game registration payload.",
        [_load_migration_sql("migration_3_0_1.sql")],
    ),
    (
        "3.0.2",
        (
            "Switch replay reads/writes to canonical replay_events and backfill"
            " legacy data."
        ),
        [_load_migration_sql("migration_3_0_2.sql")],
    ),
    (
        "3.0.3",
        "Add support for plugin-owned role assignments.",
        [_load_migration_sql("migration_3_0_3.sql")],
    ),
    (
        "3.0.4",
        (
            "Require application-assigned match_id (Discord thread snowflake);"
            " drop identity column and legacy sequence."
        ),
        [_load_migration_sql("migration_3_0_4.sql")],
    ),
]


def get_migration_hash(migration_sql: str) -> str:
    """Compute SHA256 hash of migration SQL for integrity checking."""
    return hashlib.sha256(migration_sql.strip().encode("utf-8")).hexdigest()


def apply_migrations(database) -> None:
    """Apply all pending migrations in order, tracking by version."""
    # Create database_migrations table if it doesn't exist
    try:
        with database.transaction() as cur:
            cur.execute("""
                        CREATE TABLE IF NOT EXISTS database_migrations
                        (
                            version     TEXT PRIMARY KEY,
                            description TEXT,
                            applied_at  TIMESTAMPTZ DEFAULT NOW(),
                            sql_hash    VARCHAR(64)
                        );
                        """)
    except Exception as e:
        logger.exception(f"Failed to create database_migrations table: {e}")
        raise

    applied_versions = set()
    try:
        with database.transaction() as cur:
            cur.execute("SELECT version FROM database_migrations;")
            for row in cur.fetchall():
                version = None
                if isinstance(row, dict):
                    version = row.get("version")
                else:
                    try:
                        version = row["version"]
                    except (TypeError, KeyError):
                        try:
                            version = row[0]
                        except (TypeError, IndexError, KeyError):
                            version = None
                if version is not None:
                    applied_versions.add(str(version))
    except Exception as e:
        logger.warning(f"Could not fetch applied migrations: {e}")

    for version, description, statements in MIGRATIONS:
        if version in applied_versions:
            logger.info(f"Skipping already-applied migration {version}")
            continue

        logger.warning(f"Applying database migration {version} ({description})")

        try:
            with database.transaction() as cur:
                for stmt in statements:
                    stmt = stmt.strip()
                    if not stmt:
                        continue
                    logger.debug(f"Executing: {stmt[:100]}...")
                    cur.execute(stmt)

                # Track the migration
                migration_text = "\n".join(statements)
                sql_hash = get_migration_hash(migration_text)

                cur.execute(
                    """
                    INSERT INTO database_migrations (version, description, sql_hash)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (version) DO UPDATE SET description = EXCLUDED.description,
                                                        sql_hash    = EXCLUDED.sql_hash;
                    """,
                    (version, description, sql_hash),
                )
            logger.info(f"✓ Migration {version} applied successfully")

        except Exception as e:
            logger.exception(f"Migration {version} failed ({type(e).__name__}): {e}")
            msg = f"Migration {version} failed: {e}"
            raise Exception(msg) from e


@dataclass(slots=True)
class MigrationRunner:
    """Applies database migrations and coordinates post-migration startup tasks."""

    analytics_retention_days: int = 30

    def apply_migrations(self, database: Database) -> None:
        """Run versioned SQL migrations only (connection pool; no domain logic)."""
        apply_migrations(database)

    def run_startup(
            self,
            database: Database,
            games: object,
            analytics: object,
            matches: object,
    ) -> None:
        """After migrations: refresh SQL assets, sync game registry, analytics cleanup,
        and mark stale in-progress matches interrupted.
        """
        database.refresh_sql_assets()
        games.sync_games_from_code()
        analytics.cleanup_old_analytics(days=self.analytics_retention_days)
        interrupted = matches.interrupt_stale_matches()
        if interrupted:
            logger.warning(
                "Marked %s stale in-progress matches as interrupted during startup",
                interrupted,
            )
