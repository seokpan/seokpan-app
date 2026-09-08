"""Read rankings via the Game connection; no migration or Identity credential needed."""

from collections.abc import Callable
from typing import Any

from sqlalchemy import Numeric, Select, cast, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from seokpan.persistence.mariadb.models import MemberRow, MemberStatsRow
from seokpan.statistics import (
    MemberStatistics,
    StatisticsPage,
    StatisticsQuery,
    StatisticsUnavailable,
    statistics_page,
)


def statistics_statement(query: StatisticsQuery) -> Select[Any]:
    # Explicit columns: SELECT(MemberRow) would unnecessarily read password_hash/login_id.
    stats = (
        select(
            MemberRow.member_id,
            MemberRow.nickname,
            MemberRow.rating,
            func.coalesce(MemberStatsRow.wins, 0).label("wins"),
            func.coalesce(MemberStatsRow.draws, 0).label("draws"),
            func.coalesce(MemberStatsRow.losses, 0).label("losses"),
            func.coalesce(MemberStatsRow.games_played, 0).label("games_played"),
        )
        .outerjoin(MemberStatsRow, MemberStatsRow.member_id == MemberRow.member_id)
        .cte("stats")
    )
    # 30 fractional digits distinguish ratios of unsigned 32-bit game counters.
    rate = cast(stats.c.wins, Numeric(65, 30)) / func.nullif(stats.c.games_played, 0)
    ranked = (
        select(
            stats.c.member_id,
            func.row_number()
            .over(
                order_by=(
                    stats.c.rating.desc(),
                    stats.c.wins.desc(),
                    rate.desc(),
                    stats.c.games_played.desc(),
                    stats.c.nickname.asc(),
                )
            )
            .label("rank"),
        )
        .where(stats.c.games_played > 0)
        .cte("ranked")
    )
    within_page = ranked.c.rank.between(query.offset + 1, query.offset + query.limit + 1)
    wanted = (
        within_page
        if query.member_id is None
        else or_(
            within_page,
            stats.c.member_id == query.member_id,
        )
    )
    # One statement supplies both page and own rank from the same DB read snapshot.
    return (
        select(*stats.c, ranked.c.rank)
        .outerjoin(
            ranked,
            ranked.c.member_id == stats.c.member_id,
        )
        .where(wanted)
        .order_by(ranked.c.rank)
    )


class MariaDBStatisticsAdapter:
    def __init__(self, game_session_factory: Callable[[], AsyncSession]) -> None:
        self._sessions = game_session_factory

    async def read(self, query: StatisticsQuery) -> StatisticsPage:
        try:
            async with self._sessions() as session:
                result = await session.execute(statistics_statement(query))
                rows = tuple(MemberStatistics(**dict(row)) for row in result.mappings())
                return statistics_page(rows, query)
        except SQLAlchemyError as error:
            raise StatisticsUnavailable() from error
