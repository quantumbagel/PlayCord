-- PlayCord PostgreSQL Database Schema
-- Version: 3.0.0
-- Description: Scalable relational architecture for matchmaking, ratings, replay, and analytics.

-- ============================================================================
-- TEARDOWN (for full rebuild)
-- ============================================================================

DROP MATERIALIZED VIEW IF EXISTS player_statistics;
DROP MATERIALIZED VIEW IF EXISTS global_leaderboard;
DROP VIEW IF EXISTS player_statistics;
DROP VIEW IF EXISTS global_leaderboard;

DROP TABLE IF EXISTS replay_events CASCADE;
DROP TABLE IF EXISTS rating_history CASCADE;
DROP TABLE IF EXISTS audit_events CASCADE;
DROP TABLE IF EXISTS analytics_events CASCADE;
DROP TABLE IF EXISTS match_moves CASCADE;
DROP TABLE IF EXISTS match_role_assignments CASCADE;
DROP TABLE IF EXISTS match_participants CASCADE;
DROP TABLE IF EXISTS matches CASCADE;
DROP TABLE IF EXISTS user_game_ratings CASCADE;
DROP TABLE IF EXISTS analytics_event_types CASCADE;
DROP TABLE IF EXISTS games CASCADE;
DROP TABLE IF EXISTS users CASCADE;
DROP TABLE IF EXISTS guilds CASCADE;
DROP TABLE IF EXISTS database_migrations CASCADE;

DROP PROCEDURE IF EXISTS ensure_monthly_partitions(TEXT, INTEGER, INTEGER) CASCADE;
DROP FUNCTION IF EXISTS trg_sync_matches_played_on_complete() CASCADE;
DROP FUNCTION IF EXISTS trg_validate_match_from_matches() CASCADE;
DROP FUNCTION IF EXISTS trg_validate_match_from_participants() CASCADE;
DROP FUNCTION IF EXISTS validate_match_participant_count(BIGINT) CASCADE;
DROP FUNCTION IF EXISTS enforce_rating_floors() CASCADE;
DROP FUNCTION IF EXISTS trg_guard_match_status_and_timestamps() CASCADE;
DROP FUNCTION IF EXISTS update_updated_at_column() CASCADE;

DROP TYPE IF EXISTS match_status CASCADE;

-- ============================================================================
-- ENUMS
-- ============================================================================

CREATE TYPE match_status AS ENUM (
    'queued',
    'in_progress',
    'completed',
    'abandoned',
    'interrupted',
    'cancelled'
    );

-- ============================================================================
-- CORE OPERATIONAL TABLES
-- ============================================================================

-- Discord guilds (servers)
CREATE TABLE guilds
(
    guild_id    BIGINT PRIMARY KEY,
    settings    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    is_deleted  BOOLEAN     NOT NULL DEFAULT FALSE,
    deleted_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_guild_deleted_consistency CHECK (
        (is_deleted = TRUE AND deleted_at IS NOT NULL) OR
        (is_deleted = FALSE AND deleted_at IS NULL)
        )
);

CREATE INDEX idx_guilds_active ON guilds (is_active, created_at DESC)
    WHERE is_deleted = FALSE;

-- Discord users
CREATE TABLE users
(
    user_id      BIGINT PRIMARY KEY,
    username     VARCHAR(100)  NOT NULL,
    preferences  JSONB         NOT NULL DEFAULT '{}'::jsonb,
    is_bot       BOOLEAN       NOT NULL DEFAULT FALSE,
    is_active    BOOLEAN       NOT NULL DEFAULT TRUE,
    is_deleted   BOOLEAN       NOT NULL DEFAULT FALSE,
    deleted_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_username_not_empty CHECK (LENGTH(TRIM(username)) > 0),
    CONSTRAINT chk_user_deleted_consistency CHECK (
        (is_deleted = TRUE AND deleted_at IS NOT NULL) OR
        (is_deleted = FALSE AND deleted_at IS NULL)
        )
);

CREATE INDEX idx_users_active ON users (is_active, created_at DESC)
    WHERE is_deleted = FALSE;
CREATE INDEX idx_users_username ON users (username varchar_pattern_ops)
    WHERE is_deleted = FALSE;

-- Game registry
CREATE TABLE games
(
    game_id                    INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    game_name                  VARCHAR(100)  NOT NULL UNIQUE,
    display_name               VARCHAR(200)  NOT NULL,
    min_players                INTEGER       NOT NULL,
    max_players                INTEGER       NOT NULL,
    rating_config              JSONB         NOT NULL DEFAULT '{"mu": 1500.0, "sigma": 166.6666666667, "beta": 83.3333333333, "tau": 8.3333333333, "draw": 0.10, "min_mu": 0.0, "min_sigma": 1.0}'::jsonb,
    game_metadata              JSONB         NOT NULL DEFAULT '{}'::jsonb,
    game_schema_version        INTEGER       NOT NULL DEFAULT 1,
    default_mu                 DOUBLE PRECISION NOT NULL DEFAULT 1500.0,
    default_sigma              DOUBLE PRECISION NOT NULL DEFAULT 166.6666666667,
    default_beta               DOUBLE PRECISION NOT NULL DEFAULT 83.3333333333,
    default_tau                DOUBLE PRECISION NOT NULL DEFAULT 8.3333333333,
    default_draw_probability   DOUBLE PRECISION NOT NULL DEFAULT 0.10,
    is_active                  BOOLEAN       NOT NULL DEFAULT TRUE,
    is_deleted                 BOOLEAN       NOT NULL DEFAULT FALSE,
    deleted_at                 TIMESTAMPTZ,
    created_at                 TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_game_name_format CHECK (game_name ~ '^[a-z][a-z0-9_]*$'),
    CONSTRAINT chk_game_players CHECK (min_players >= 1 AND max_players >= min_players),
    CONSTRAINT chk_game_rating_config_keys CHECK (
        rating_config ? 'sigma' AND
        rating_config ? 'beta' AND
        rating_config ? 'tau' AND
        rating_config ? 'draw'
        ),
    CONSTRAINT chk_game_default_mu_floor CHECK (default_mu >= 0),
    CONSTRAINT chk_game_default_sigma_floor CHECK (default_sigma >= 1.0),
    CONSTRAINT chk_game_draw_probability CHECK (default_draw_probability >= 0 AND default_draw_probability <= 1),
    CONSTRAINT chk_game_deleted_consistency CHECK (
        (is_deleted = TRUE AND deleted_at IS NOT NULL) OR
        (is_deleted = FALSE AND deleted_at IS NULL)
        )
);

CREATE INDEX idx_games_active ON games (is_active, game_name)
    WHERE is_deleted = FALSE;

-- One global TrueSkill row per (user, game)
CREATE TABLE user_game_ratings
(
    rating_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id                BIGINT            NOT NULL,
    game_id                INTEGER           NOT NULL,
    mu                     DOUBLE PRECISION  NOT NULL DEFAULT 1500.0,
    sigma                  DOUBLE PRECISION  NOT NULL DEFAULT 166.6666666667,
    conservative_rating    DOUBLE PRECISION
        GENERATED ALWAYS AS (mu - (3.0 * sigma)) STORED,
    matches_played         INTEGER           NOT NULL DEFAULT 0,
    last_played            TIMESTAMPTZ,
    last_sigma_increase    TIMESTAMPTZ,
    is_deleted             BOOLEAN           NOT NULL DEFAULT FALSE,
    created_at             TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ       NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_user_game UNIQUE (user_id, game_id),
    CONSTRAINT fk_user_game_rating_user FOREIGN KEY (user_id)
        REFERENCES users (user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_user_game_rating_game FOREIGN KEY (game_id)
        REFERENCES games (game_id) ON DELETE RESTRICT,
    CONSTRAINT chk_rating_floor CHECK (mu >= 0 AND sigma >= 1.0),
    CONSTRAINT chk_matches_played_floor CHECK (matches_played >= 0)
);

CREATE INDEX idx_user_game_ratings_game_user ON user_game_ratings (game_id, user_id)
    WHERE is_deleted = FALSE;
CREATE INDEX idx_user_game_ratings_leaderboard ON user_game_ratings (
                                                                    game_id, conservative_rating DESC, matches_played DESC, user_id
    ) WHERE is_deleted = FALSE;
CREATE INDEX idx_user_game_ratings_last_played ON user_game_ratings (last_played DESC)
    WHERE last_played IS NOT NULL;

-- Match sessions
CREATE TABLE matches
(
    match_id         BIGINT         NOT NULL PRIMARY KEY,
    game_id          INTEGER        NOT NULL,
    guild_id         BIGINT         NOT NULL,
    channel_id       BIGINT         NOT NULL,
    thread_id        BIGINT,
    status           match_status   NOT NULL DEFAULT 'queued',
    is_rated         BOOLEAN        NOT NULL DEFAULT TRUE,
    is_archived      BOOLEAN        NOT NULL DEFAULT FALSE,
    game_config      JSONB          NOT NULL DEFAULT '{}'::jsonb,
    metadata         JSONB          NOT NULL DEFAULT '{}'::jsonb,
    match_code       VARCHAR(8),
    created_at       TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    started_at       TIMESTAMPTZ,
    ended_at         TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_match_game FOREIGN KEY (game_id)
        REFERENCES games (game_id) ON DELETE RESTRICT,
    CONSTRAINT fk_match_guild FOREIGN KEY (guild_id)
        REFERENCES guilds (guild_id) ON DELETE RESTRICT,
    CONSTRAINT chk_match_started_required CHECK (
        status NOT IN ('in_progress', 'completed') OR started_at IS NOT NULL
        ),
    CONSTRAINT chk_match_end_required CHECK (
        status NOT IN ('completed', 'abandoned', 'interrupted', 'cancelled')
            OR ended_at IS NOT NULL
        ),
    CONSTRAINT chk_match_active_without_end CHECK (
        status NOT IN ('queued', 'in_progress') OR ended_at IS NULL
        ),
    CONSTRAINT chk_match_end_after_started CHECK (
        ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at
        )
);

CREATE UNIQUE INDEX idx_matches_match_code ON matches (match_code)
    WHERE match_code IS NOT NULL;
CREATE UNIQUE INDEX idx_matches_match_code_ci ON matches ((lower(match_code)))
    WHERE match_code IS NOT NULL;
CREATE INDEX idx_matches_status_created ON matches (status, created_at DESC);
CREATE INDEX idx_matches_guild_game_status ON matches (guild_id, game_id, status, created_at DESC);
CREATE INDEX idx_matches_ended_at ON matches (ended_at DESC)
    WHERE ended_at IS NOT NULL;

-- Match participants and per-match rating deltas
CREATE TABLE match_participants
(
    participant_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id         BIGINT           NOT NULL,
    user_id          BIGINT           NOT NULL,
    player_number    INTEGER          NOT NULL,
    final_ranking    INTEGER,
    score            DOUBLE PRECISION,
    mu_before        DOUBLE PRECISION,
    sigma_before     DOUBLE PRECISION,
    mu_delta         DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    sigma_delta      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    is_deleted       BOOLEAN          NOT NULL DEFAULT FALSE,
    joined_at        TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ      NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_match_user UNIQUE (match_id, user_id),
    CONSTRAINT uq_match_player_number UNIQUE (match_id, player_number),
    CONSTRAINT fk_match_participant_match FOREIGN KEY (match_id)
        REFERENCES matches (match_id) ON DELETE CASCADE,
    CONSTRAINT fk_match_participant_user FOREIGN KEY (user_id)
        REFERENCES users (user_id) ON DELETE RESTRICT,
    CONSTRAINT chk_player_number_floor CHECK (player_number >= 1),
    CONSTRAINT chk_final_ranking_floor CHECK (final_ranking IS NULL OR final_ranking >= 1)
);

CREATE INDEX idx_match_participants_match_order ON match_participants (match_id, player_number);
CREATE INDEX idx_match_participants_user ON match_participants (user_id, joined_at DESC);
CREATE INDEX idx_match_participants_match_rank ON match_participants (match_id, final_ranking)
    WHERE final_ranking IS NOT NULL;

-- Plugin-owned role assignments for matches
CREATE TABLE match_role_assignments
(
    assignment_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id          BIGINT        NOT NULL,
    player_id         BIGINT        NOT NULL,
    role_id           VARCHAR(100)  NOT NULL,
    seat_index        INTEGER       NOT NULL,
    created_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    
    CONSTRAINT uq_match_role_player UNIQUE (match_id, player_id),
    CONSTRAINT fk_role_match FOREIGN KEY (match_id)
        REFERENCES matches (match_id) ON DELETE CASCADE,
    CONSTRAINT fk_role_player FOREIGN KEY (player_id)
        REFERENCES users (user_id) ON DELETE RESTRICT,
    CONSTRAINT chk_role_seat_index CHECK (seat_index >= 0)
);

CREATE INDEX IF NOT EXISTS idx_match_role_assignments_match
    ON match_role_assignments (match_id);

CREATE INDEX IF NOT EXISTS idx_match_role_assignments_player
    ON match_role_assignments (player_id);

CREATE INDEX IF NOT EXISTS idx_match_role_assignments_role
    ON match_role_assignments (role_id);

-- Historical rating changes
CREATE TABLE rating_history
(
    history_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           BIGINT            NOT NULL,
    guild_id          BIGINT,
    game_id           INTEGER           NOT NULL,
    match_id          BIGINT,
    mu_before         DOUBLE PRECISION  NOT NULL,
    sigma_before      DOUBLE PRECISION  NOT NULL,
    mu_after          DOUBLE PRECISION  NOT NULL,
    sigma_after       DOUBLE PRECISION  NOT NULL,
    is_deleted        BOOLEAN           NOT NULL DEFAULT FALSE,
    created_at        TIMESTAMPTZ       NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_rating_history_user FOREIGN KEY (user_id)
        REFERENCES users (user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rating_history_guild FOREIGN KEY (guild_id)
        REFERENCES guilds (guild_id) ON DELETE SET NULL,
    CONSTRAINT fk_rating_history_game FOREIGN KEY (game_id)
        REFERENCES games (game_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rating_history_match FOREIGN KEY (match_id)
        REFERENCES matches (match_id) ON DELETE SET NULL
);

CREATE INDEX idx_rating_history_user_game_time ON rating_history (user_id, game_id, created_at DESC);
CREATE INDEX idx_rating_history_match ON rating_history (match_id);
CREATE INDEX idx_rating_history_guild_game_time ON rating_history (guild_id, game_id, created_at DESC);

-- Canonical replay events stream (structured events)
CREATE TABLE replay_events
(
    event_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id          BIGINT           NOT NULL,
    sequence_number   INTEGER          NOT NULL,
    event_type        VARCHAR(100)     NOT NULL,
    actor_user_id     BIGINT,
    payload           JSONB            NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ      NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_replay_match_sequence UNIQUE (match_id, sequence_number),
    CONSTRAINT fk_replay_events_match FOREIGN KEY (match_id)
        REFERENCES matches (match_id) ON DELETE CASCADE,
    CONSTRAINT fk_replay_events_actor FOREIGN KEY (actor_user_id)
        REFERENCES users (user_id) ON DELETE SET NULL,
    CONSTRAINT chk_replay_sequence CHECK (sequence_number >= 1)
);

CREATE INDEX idx_replay_events_match_seq ON replay_events (match_id, sequence_number);
CREATE INDEX idx_replay_events_actor_time ON replay_events (actor_user_id, created_at DESC)
    WHERE actor_user_id IS NOT NULL;

-- ============================================================================
-- HIGH-VOLUME TIME-SERIES TABLES (RANGE PARTITIONED ON created_at)
-- ============================================================================

-- Turn-by-turn replay/move stream
CREATE TABLE match_moves
(
    created_at         TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    move_id            BIGINT GENERATED ALWAYS AS IDENTITY,
    match_id           BIGINT           NOT NULL,
    user_id            BIGINT,
    move_number        INTEGER          NOT NULL,
    kind               VARCHAR(20)      NOT NULL DEFAULT 'move',
    move_data          JSONB            NOT NULL,
    game_state_after   JSONB,
    is_game_affecting  BOOLEAN          NOT NULL DEFAULT TRUE,
    is_deleted         BOOLEAN          NOT NULL DEFAULT FALSE,
    time_taken_ms      INTEGER,

    CONSTRAINT pk_match_moves PRIMARY KEY (created_at, move_id),
    CONSTRAINT fk_match_moves_match FOREIGN KEY (match_id)
        REFERENCES matches (match_id) ON DELETE CASCADE,
    CONSTRAINT fk_match_moves_user FOREIGN KEY (user_id)
        REFERENCES users (user_id) ON DELETE SET NULL,
    CONSTRAINT chk_match_moves_number CHECK (move_number >= 1),
    CONSTRAINT chk_match_moves_kind CHECK (kind IN ('move', 'system', 'reset')),
    CONSTRAINT chk_match_moves_time_taken CHECK (time_taken_ms IS NULL OR time_taken_ms >= 0)
) PARTITION BY RANGE (created_at);

CREATE INDEX idx_match_moves_match_sequence ON match_moves (match_id, move_number);
CREATE INDEX idx_match_moves_match_created ON match_moves (match_id, created_at DESC);
CREATE INDEX idx_match_moves_user_created ON match_moves (user_id, created_at DESC);
CREATE INDEX idx_match_moves_kind_created ON match_moves (kind, created_at DESC);

-- Analytics events stream
CREATE TABLE analytics_event_types
(
    event_type   VARCHAR(100) PRIMARY KEY,
    description  TEXT,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE TABLE analytics_events
(
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    event_id        BIGINT GENERATED ALWAYS AS IDENTITY,
    event_type      VARCHAR(100)     NOT NULL,
    user_id         BIGINT,
    guild_id        BIGINT,
    game_id         INTEGER,
    match_id        BIGINT,
    metadata        JSONB            DEFAULT '{}'::jsonb,

    CONSTRAINT pk_analytics_events PRIMARY KEY (created_at, event_id),
    CONSTRAINT fk_analytics_event_type FOREIGN KEY (event_type)
        REFERENCES analytics_event_types (event_type) ON DELETE RESTRICT
) PARTITION BY RANGE (created_at);

CREATE INDEX idx_analytics_events_type_time ON analytics_events (event_type, created_at DESC);
CREATE INDEX idx_analytics_events_user_time ON analytics_events (user_id, created_at DESC);
CREATE INDEX idx_analytics_events_guild_time ON analytics_events (guild_id, created_at DESC);
CREATE INDEX idx_analytics_events_game_time ON analytics_events (game_id, created_at DESC);
CREATE INDEX idx_analytics_events_match_time ON analytics_events (match_id, created_at DESC);

INSERT INTO analytics_event_types (event_type, description)
VALUES ('bot_started', 'Bot process booted successfully'),
       ('command_used', 'A slash command or move command was executed'),
       ('error_occurred', 'An unexpected runtime or infrastructure error occurred'),
       ('game_abandoned', 'A match was abandoned before completion'),
       ('game_completed', 'A match reached a completed outcome'),
       ('game_interrupted', 'A match was interrupted before completion'),
       ('game_started', 'A new match runtime was started'),
       ('guild_joined', 'Bot joined a guild'),
       ('guild_left', 'Bot left a guild'),
       ('matchmaking_joined', 'A player joined a matchmaking lobby'),
       ('matchmaking_left', 'A player left a matchmaking lobby'),
       ('matchmaking_matched', 'A lobby transitioned into an active match'),
       ('matchmaking_started', 'A matchmaking lobby was created'),
       ('matchmaking_completed', 'A matchmaking lobby completed successfully'),
       ('matchmaking_cancelled', 'A matchmaking lobby ended without a game'),
       ('player_joined', 'A player joined a match'),
       ('player_left', 'A player left a match'),
       ('move_made', 'A match move or equivalent interaction was applied'),
       ('move_valid', 'A move was accepted'),
       ('move_invalid', 'A move was rejected as invalid'),
       ('move_rejected', 'A move could not be routed or executed'),
       ('rating_updated', 'A player rating row changed'),
       ('skill_decay_applied', 'Inactivity-based sigma decay was applied'),
       ('game_errored', 'Game or match error (legacy name; prefer error_occurred in new code)')
ON CONFLICT (event_type) DO UPDATE SET description = EXCLUDED.description;

-- Immutable audit stream
CREATE TABLE audit_events
(
    created_at       TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    audit_id         BIGINT GENERATED ALWAYS AS IDENTITY,
    action_type      VARCHAR(100)     NOT NULL,
    actor_user_id    BIGINT,
    resource_type    VARCHAR(100)     NOT NULL,
    resource_id      BIGINT,
    before_state     JSONB,
    after_state      JSONB,
    metadata         JSONB            NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT pk_audit_events PRIMARY KEY (created_at, audit_id),
    CONSTRAINT chk_audit_action_not_empty CHECK (LENGTH(TRIM(action_type)) > 0),
    CONSTRAINT chk_audit_resource_not_empty CHECK (LENGTH(TRIM(resource_type)) > 0)
) PARTITION BY RANGE (created_at);

CREATE INDEX idx_audit_events_action_time ON audit_events (action_type, created_at DESC);
CREATE INDEX idx_audit_events_actor_time ON audit_events (actor_user_id, created_at DESC);
CREATE INDEX idx_audit_events_resource_time ON audit_events (resource_type, resource_id, created_at DESC);

-- ============================================================================
-- PARTITION MANAGEMENT
-- ============================================================================

CREATE OR REPLACE PROCEDURE ensure_monthly_partitions(
    p_parent_table TEXT,
    p_months_back INTEGER DEFAULT 1,
    p_months_ahead INTEGER DEFAULT 12
)
    LANGUAGE plpgsql
AS
$$
DECLARE
    v_month          DATE;
    v_partition_name TEXT;
    v_range_start    TIMESTAMPTZ;
    v_range_end      TIMESTAMPTZ;
BEGIN
    FOR v_month IN
        SELECT gs::date
        FROM generate_series(
                     (date_trunc('month', CURRENT_TIMESTAMP) - make_interval(months => p_months_back)),
                     (date_trunc('month', CURRENT_TIMESTAMP) + make_interval(months => p_months_ahead)),
                     interval '1 month'
             ) AS gs
        LOOP
            v_partition_name := format('%s_%s', p_parent_table, to_char(v_month, 'YYYYMM'));
            v_range_start := date_trunc('month', v_month::timestamp);
            v_range_end := v_range_start + interval '1 month';

            EXECUTE format(
                    'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L);',
                    v_partition_name, p_parent_table, v_range_start, v_range_end
                    );
        END LOOP;
END;
$$;

CALL ensure_monthly_partitions('match_moves', 2, 18);
CALL ensure_monthly_partitions('analytics_events', 2, 18);
CALL ensure_monthly_partitions('audit_events', 2, 18);

CREATE TABLE IF NOT EXISTS match_moves_default PARTITION OF match_moves DEFAULT;
CREATE TABLE IF NOT EXISTS analytics_events_default PARTITION OF analytics_events DEFAULT;
CREATE TABLE IF NOT EXISTS audit_events_default PARTITION OF audit_events DEFAULT;

-- ============================================================================
-- TRIGGERS & VALIDATION
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
    RETURNS TRIGGER
    LANGUAGE plpgsql
AS
$$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_guilds_updated_at
    BEFORE UPDATE
    ON guilds
    FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER tr_users_updated_at
    BEFORE UPDATE
    ON users
    FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER tr_games_updated_at
    BEFORE UPDATE
    ON games
    FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER tr_user_game_ratings_updated_at
    BEFORE UPDATE
    ON user_game_ratings
    FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER tr_matches_updated_at
    BEFORE UPDATE
    ON matches
    FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER tr_match_participants_updated_at
    BEFORE UPDATE
    ON match_participants
    FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();

-- Guard state transitions and ensure core timestamps are aligned with status.
CREATE OR REPLACE FUNCTION trg_guard_match_status_and_timestamps()
    RETURNS TRIGGER
    LANGUAGE plpgsql
AS
$$
BEGIN
    IF TG_OP = 'UPDATE'
        AND OLD.status IN ('completed', 'abandoned', 'interrupted', 'cancelled')
        AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION 'match % cannot transition away from terminal status %', NEW.match_id, OLD.status;
    END IF;

    IF NEW.status IN ('in_progress', 'completed') AND NEW.started_at IS NULL THEN
        NEW.started_at := NOW();
    END IF;

    IF NEW.status IN ('completed', 'abandoned', 'interrupted', 'cancelled') THEN
        NEW.ended_at := COALESCE(NEW.ended_at, NOW());
    ELSIF TG_OP = 'UPDATE' AND OLD.status IN ('queued', 'in_progress') THEN
        NEW.ended_at := NULL;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_matches_status_guard
    BEFORE INSERT OR UPDATE OF status, started_at, ended_at
    ON matches
    FOR EACH ROW
EXECUTE FUNCTION trg_guard_match_status_and_timestamps();

-- Hard-floor guard with explicit error messages.
CREATE OR REPLACE FUNCTION enforce_rating_floors()
    RETURNS TRIGGER
    LANGUAGE plpgsql
AS
$$
BEGIN
    IF NEW.mu < 0 THEN
        RAISE EXCEPTION 'mu cannot be below 0 (user_id=%, game_id=%)', NEW.user_id, NEW.game_id;
    END IF;

    IF NEW.sigma < 1.0 THEN
        RAISE EXCEPTION 'sigma cannot be below 1.0 (user_id=%, game_id=%)', NEW.user_id, NEW.game_id;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_user_game_ratings_floor
    BEFORE INSERT OR UPDATE OF mu, sigma
    ON user_game_ratings
    FOR EACH ROW
EXECUTE FUNCTION enforce_rating_floors();

-- Deferred multi-row validation for player counts and completion data.
CREATE OR REPLACE FUNCTION validate_match_participant_count(p_match_id BIGINT)
    RETURNS VOID
    LANGUAGE plpgsql
AS
$$
DECLARE
    v_status           match_status;
    v_min_players      INTEGER;
    v_max_players      INTEGER;
    v_participant_cnt  INTEGER;
    v_ranked_cnt       INTEGER;
BEGIN
    SELECT m.status, g.min_players, g.max_players
    INTO v_status, v_min_players, v_max_players
    FROM matches m
             JOIN games g ON g.game_id = m.game_id
    WHERE m.match_id = p_match_id;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT COUNT(*),
           COUNT(*) FILTER (WHERE final_ranking IS NOT NULL)
    INTO v_participant_cnt, v_ranked_cnt
    FROM match_participants mp
    WHERE mp.match_id = p_match_id
      AND mp.is_deleted = FALSE;

    IF v_status IN ('in_progress', 'completed') THEN
        IF v_participant_cnt < v_min_players THEN
            RAISE EXCEPTION
                'match % requires at least % participants before status %, found %',
                p_match_id, v_min_players, v_status, v_participant_cnt;
        END IF;
        IF v_participant_cnt > v_max_players THEN
            RAISE EXCEPTION
                'match % allows at most % participants, found %',
                p_match_id, v_max_players, v_participant_cnt;
        END IF;
    END IF;

    IF v_status = 'completed' AND v_ranked_cnt <> v_participant_cnt THEN
            RAISE EXCEPTION
            'match % cannot be completed without final_ranking for every participant',
            p_match_id;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION trg_validate_match_from_participants()
    RETURNS TRIGGER
    LANGUAGE plpgsql
AS
$$
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM validate_match_participant_count(OLD.match_id);
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        PERFORM validate_match_participant_count(OLD.match_id);
        PERFORM validate_match_participant_count(NEW.match_id);
        RETURN NEW;
    END IF;

    PERFORM validate_match_participant_count(NEW.match_id);
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION trg_validate_match_from_matches()
    RETURNS TRIGGER
    LANGUAGE plpgsql
AS
$$
BEGIN
    PERFORM validate_match_participant_count(NEW.match_id);
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER tr_match_participants_validate_counts
    AFTER INSERT OR UPDATE OR DELETE
    ON match_participants
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
EXECUTE FUNCTION trg_validate_match_from_participants();

CREATE CONSTRAINT TRIGGER tr_matches_validate_counts
    AFTER INSERT OR UPDATE OF status, game_id
    ON matches
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
EXECUTE FUNCTION trg_validate_match_from_matches();

-- Keep ratings.matches_played synchronized exactly on status transition to completed.
CREATE OR REPLACE FUNCTION trg_sync_matches_played_on_complete()
    RETURNS TRIGGER
    LANGUAGE plpgsql
AS
$$
DECLARE
    v_ended_at TIMESTAMPTZ;
BEGIN
    IF NEW.status <> 'completed' THEN
        RETURN NEW;
    END IF;

    IF OLD.status = 'completed' THEN
        RETURN NEW;
    END IF;

    IF NEW.is_rated = FALSE THEN
        RETURN NEW;
    END IF;

    v_ended_at := COALESCE(NEW.ended_at, NOW());

    INSERT INTO user_game_ratings (user_id, game_id, mu, sigma, matches_played, last_played)
    SELECT mp.user_id,
           NEW.game_id,
           g.default_mu,
           g.default_sigma,
           1,
           v_ended_at
    FROM match_participants mp
             JOIN games g ON g.game_id = NEW.game_id
    WHERE mp.match_id = NEW.match_id
      AND mp.is_deleted = FALSE
    ON CONFLICT (user_id, game_id) DO UPDATE
        SET matches_played = user_game_ratings.matches_played + 1,
            last_played = GREATEST(
                    COALESCE(user_game_ratings.last_played, v_ended_at),
                    v_ended_at
                             ),
            updated_at = NOW();

    RETURN NEW;
END;
$$;

CREATE TRIGGER tr_matches_sync_matches_played
    AFTER UPDATE OF status
    ON matches
    FOR EACH ROW
EXECUTE FUNCTION trg_sync_matches_played_on_complete();

-- ============================================================================
-- LEADERBOARD / STATS (MATERIALIZED VIEWS)
-- ============================================================================

CREATE MATERIALIZED VIEW global_leaderboard AS
SELECT
    ugr.game_id,
    g.game_name,
    ugr.user_id,
    u.username,
    ugr.mu,
    ugr.sigma,
    ugr.conservative_rating,
    ugr.matches_played,
    RANK() OVER (
        PARTITION BY ugr.game_id
        ORDER BY ugr.conservative_rating DESC, ugr.mu DESC, ugr.sigma ASC, ugr.matches_played DESC, ugr.user_id
        ) AS global_rank,
    ugr.updated_at AS rating_updated_at
FROM user_game_ratings ugr
         JOIN users u ON u.user_id = ugr.user_id
         JOIN games g ON g.game_id = ugr.game_id
WHERE ugr.is_deleted = FALSE
  AND u.is_deleted = FALSE
  AND u.is_bot = FALSE
  AND g.is_deleted = FALSE;

CREATE UNIQUE INDEX ux_global_leaderboard_game_user ON global_leaderboard (game_id, user_id);
CREATE INDEX idx_global_leaderboard_game_rank ON global_leaderboard (game_id, global_rank);

CREATE MATERIALIZED VIEW player_statistics AS
WITH completed_participation AS (
    SELECT
        mp.user_id,
        m.game_id,
        mp.final_ranking,
        mp.mu_delta,
        mp.sigma_delta,
        m.ended_at
    FROM match_participants mp
             JOIN matches m ON m.match_id = mp.match_id
    WHERE mp.is_deleted = FALSE
      AND m.status = 'completed'
),
aggregates AS (
    SELECT
        cp.user_id,
        cp.game_id,
        COUNT(*)::INTEGER                                       AS completed_matches,
        COUNT(*) FILTER (WHERE cp.final_ranking = 1)::INTEGER  AS wins,
        AVG(cp.final_ranking)::DOUBLE PRECISION                AS average_rank,
        SUM(cp.mu_delta)::DOUBLE PRECISION                     AS total_mu_delta,
        SUM(cp.sigma_delta)::DOUBLE PRECISION                  AS total_sigma_delta,
        MAX(cp.ended_at)                                       AS last_match_at
    FROM completed_participation cp
    GROUP BY cp.user_id, cp.game_id
)
SELECT
    a.user_id,
    a.game_id,
    g.game_name,
    u.username,
    a.completed_matches,
    a.wins,
    CASE
        WHEN a.completed_matches = 0 THEN 0
        ELSE (a.wins::DOUBLE PRECISION / a.completed_matches::DOUBLE PRECISION)
        END                                                   AS win_rate,
    a.average_rank,
    a.total_mu_delta,
    a.total_sigma_delta,
    a.last_match_at,
    COALESCE(ugr.mu, g.default_mu)                           AS current_mu,
    COALESCE(ugr.sigma, g.default_sigma)                     AS current_sigma,
    COALESCE(ugr.conservative_rating, g.default_mu - (3.0 * g.default_sigma))
                                                          AS current_conservative_rating
FROM aggregates a
         JOIN users u ON u.user_id = a.user_id
         JOIN games g ON g.game_id = a.game_id
         LEFT JOIN user_game_ratings ugr ON ugr.user_id = a.user_id
    AND ugr.game_id = a.game_id
    AND ugr.is_deleted = FALSE
WHERE u.is_deleted = FALSE
  AND g.is_deleted = FALSE;

CREATE UNIQUE INDEX ux_player_statistics_user_game ON player_statistics (user_id, game_id);
CREATE INDEX idx_player_statistics_game_winrate ON player_statistics (game_id, win_rate DESC, completed_matches DESC);

-- Recommended refresh strategy:
--   1) Keep unique indexes above so CONCURRENT refresh is non-blocking for readers.
--   2) Schedule with pg_cron (or an app background worker), for example:
--      SELECT cron.schedule('refresh-global-leaderboard', '*/5 * * * *',
--                           'REFRESH MATERIALIZED VIEW CONCURRENTLY global_leaderboard');
--      SELECT cron.schedule('refresh-player-statistics', '*/10 * * * *',
--                           'REFRESH MATERIALIZED VIEW CONCURRENTLY player_statistics');

-- ============================================================================
-- MIGRATION TRACKING
-- ============================================================================

CREATE TABLE IF NOT EXISTS database_migrations
(
    version      TEXT PRIMARY KEY,
    description  TEXT,
    applied_at   TIMESTAMPTZ DEFAULT NOW(),
    sql_hash     VARCHAR(64)
);
