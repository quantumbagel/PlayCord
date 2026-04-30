-- Migration 3.0.3
-- Add support for plugin-owned role assignments.

CREATE TABLE IF NOT EXISTS match_role_assignments
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
