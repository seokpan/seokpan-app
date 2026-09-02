-- Read-only preflight for the game_participant identity migration.
-- Run against the intended stone_game database before any backfill or constraint migration.
-- A non-empty finding set is a review input; do not infer or repair data from this script.

-- A04B-01: baseline row counts and the size of the required backfill.
SELECT
    COUNT(*) AS total_participant_rows,
    COALESCE(SUM(participant_id IS NULL), 0) AS null_participant_id_rows
FROM game_participant;

-- A04B-02: active games require an authoritative Redis participant mapping before backfill.
SELECT
    gp.game_id,
    COUNT(*) AS participant_rows,
    COALESCE(SUM(gp.participant_id IS NULL), 0) AS null_participant_id_rows
FROM game_participant AS gp
JOIN game AS g ON g.game_id = gp.game_id
WHERE g.status = 'IN_PROGRESS'
GROUP BY gp.game_id
ORDER BY gp.game_id;

-- A04B-03: a member must not occur more than once in one game.
SELECT
    game_id,
    member_id,
    COUNT(*) AS duplicate_rows
FROM game_participant
WHERE member_id IS NOT NULL
GROUP BY game_id, member_id
HAVING COUNT(*) > 1
ORDER BY game_id, member_id;

-- A04B-04: a guest label must not occur more than once in one game.
SELECT
    game_id,
    guest_label,
    COUNT(*) AS duplicate_rows
FROM game_participant
WHERE guest_label IS NOT NULL
GROUP BY game_id, guest_label
HAVING COUNT(*) > 1
ORDER BY game_id, guest_label;

-- A04B-05: member and guest columns must describe exactly one participant kind.
SELECT
    id,
    game_id,
    member_id,
    is_guest,
    guest_label
FROM game_participant
WHERE NOT (
    (is_guest = FALSE AND member_id IS NOT NULL AND guest_label IS NULL)
    OR
    (is_guest = TRUE AND member_id IS NULL AND guest_label REGEXP '^Guest-[0-9]{4}$')
)
ORDER BY game_id, id;

-- A04B-06: every populated identity must be a canonical lowercase, hyphenated UUIDv4.
SELECT
    id,
    game_id,
    participant_id
FROM game_participant
WHERE participant_id IS NOT NULL
  AND participant_id NOT REGEXP
      '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
ORDER BY game_id, id;

-- A04B-07: a populated participant identity must be unique inside its game.
SELECT
    game_id,
    participant_id,
    COUNT(*) AS duplicate_rows
FROM game_participant
WHERE participant_id IS NOT NULL
GROUP BY game_id, participant_id
HAVING COUNT(*) > 1
ORDER BY game_id, participant_id;
