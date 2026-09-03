"""MariaDB mappings owned by the application."""

from seokpan.persistence.mariadb.game_adapter import MariaDBGamePersistenceAdapter
from seokpan.persistence.mariadb.models import (
    Base,
    GameParticipantRow,
    GameResultRow,
    GameRow,
    MemberRow,
    MemberStatsRow,
    MoveRow,
    RatingHistoryRow,
)

__all__ = [
    "Base",
    "GameParticipantRow",
    "GameResultRow",
    "GameRow",
    "MemberRow",
    "MemberStatsRow",
    "MoveRow",
    "MariaDBGamePersistenceAdapter",
    "RatingHistoryRow",
]
