from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import mysql

from seokpan.persistence.mariadb.models import Base

BACKEND_ROOT = Path(__file__).parents[2]
AUDIT_SQL = BACKEND_ROOT / "migrations" / "audit" / "game_participant_identity.sql"
BASELINE_REVISION = "20260901_0001"
EXPAND_REVISION = "20260902_0002"
DATABASE_URL = "mysql+asyncmy://db_admin@db.stone.test:3306/stone_game"


def offline_sql(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    revision_range: str,
) -> str:
    monkeypatch.setenv("SEOKPAN_MIGRATION_DATABASE_URL", DATABASE_URL)
    config = Config(BACKEND_ROOT / "alembic.ini")
    output = StringIO()

    with redirect_stdout(output):
        if operation == "upgrade":
            command.upgrade(config, revision_range, sql=True)
        else:
            command.downgrade(config, revision_range, sql=True)

    return output.getvalue()


def test_participant_identity_metadata_is_nullable_char_36() -> None:
    participant_id = Base.metadata.tables["game_participant"].c.participant_id

    assert participant_id.type.compile(dialect=mysql.dialect()) == "CHAR(36)"
    assert participant_id.nullable is True
    assert participant_id.unique is None


def test_expand_upgrade_only_adds_nullable_participant_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = offline_sql(
        monkeypatch,
        "upgrade",
        f"{BASELINE_REVISION}:{EXPAND_REVISION}",
    )

    assert "ALTER TABLE game_participant ADD COLUMN participant_id CHAR(36)" in sql
    assert "CREATE TABLE" not in sql
    assert "UPDATE game_participant" not in sql
    assert "NOT NULL" not in sql
    assert "UNIQUE" not in sql
    assert "CHECK" not in sql


def test_expand_downgrade_only_drops_participant_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = offline_sql(
        monkeypatch,
        "downgrade",
        f"{EXPAND_REVISION}:{BASELINE_REVISION}",
    )

    assert "ALTER TABLE game_participant DROP COLUMN participant_id" in sql
    assert "DROP TABLE" not in sql


def test_audit_sql_is_read_only_and_covers_preflight_findings() -> None:
    sql = AUDIT_SQL.read_text(encoding="utf-8")
    executable_sql = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    ).upper()

    assert sql.count("-- A04B-") == 7
    assert "TOTAL_PARTICIPANT_ROWS" in executable_sql
    assert "NULL_PARTICIPANT_ID_ROWS" in executable_sql
    assert "IN_PROGRESS" in executable_sql
    assert "GROUP BY GAME_ID, MEMBER_ID" in executable_sql
    assert "GROUP BY GAME_ID, GUEST_LABEL" in executable_sql
    assert "MEMBER_ID IS NOT NULL" in executable_sql
    assert "MEMBER_ID IS NULL" in executable_sql
    assert "PARTICIPANT_ID NOT REGEXP" in executable_sql
    assert "GROUP BY GAME_ID, PARTICIPANT_ID" in executable_sql
    for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "CREATE ", "DROP "):
        assert forbidden not in executable_sql
