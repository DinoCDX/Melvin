import asyncio
import math
import time

import aiosqlite

from globals import DATA_DIR

DB_PATH = DATA_DIR / "server_stats.db"

HOUR = 3600
DAY = 24 * HOUR
KEEP_SECONDS = 31 * DAY

RANGES = {
    "24h": ("24 hours", 24, DAY),
    "7d": ("7 days", 7, 7 * DAY),
    "30d": ("30 days", 30, 30 * DAY),
}
DEFAULT_RANGE = "7d"

POINT_SIZES = (HOUR, 6 * HOUR, DAY)

_INSERT = """
    INSERT INTO guild_stats (guild_id, hour, messages, voice_minutes, members)
    VALUES (?, ?, ?, ?, ?)
"""

_db_ready = False


async def init_db() -> None:
    global _db_ready
    if _db_ready:
        return

    await asyncio.to_thread(DATA_DIR.mkdir, parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_stats (
                guild_id INTEGER NOT NULL,
                hour INTEGER NOT NULL,
                messages INTEGER NOT NULL DEFAULT 0,
                voice_minutes INTEGER NOT NULL DEFAULT 0,
                members INTEGER,
                PRIMARY KEY (guild_id, hour)
            )
            """,
        )
        await db.commit()
    _db_ready = True


def hour_start(timestamp: float | None = None) -> int:
    now = time.time() if timestamp is None else timestamp
    return int(now // HOUR * HOUR)


async def record(rows: list[tuple[int, int, int, int, int | None]]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            _INSERT
            + """
            ON CONFLICT(guild_id, hour) DO UPDATE SET
                messages = messages + excluded.messages,
                voice_minutes = voice_minutes + excluded.voice_minutes,
                members = COALESCE(excluded.members, members)
            """,
            rows,
        )
        await db.commit()


async def replace(guild_id: int, rows: list[tuple[int, int, int, int | None]]) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM guild_stats WHERE guild_id = ?", (guild_id,))
        await db.executemany(_INSERT, [(guild_id, *row) for row in rows])
        await db.commit()


async def prune() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM guild_stats WHERE hour < ?",
            (hour_start() - KEEP_SECONDS,),
        )
        await db.commit()


def _ago(seconds: int) -> str:
    if seconds >= 2 * DAY:
        return f"{round(seconds / DAY)} days ago"
    if seconds >= 2 * HOUR:
        return f"{round(seconds / HOUR)} hours ago"
    return "1 hour ago"


async def get_series(guild_id: int, range_key: str) -> dict:
    label, max_points, length = RANGES[range_key]
    now_end = hour_start() + HOUR

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT MIN(hour) FROM guild_stats WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            first_hour = (await cursor.fetchone())[0]

        data_start = now_end - length
        if first_hour is not None:
            data_start = max(data_start, first_hour)
        span = now_end - data_start

        size = next(
            (size for size in POINT_SIZES if math.ceil(span / size) <= max_points),
            POINT_SIZES[-1],
        )
        end = math.ceil(now_end / size) * size
        count = min(max_points, (end - data_start // size * size) // size)
        first = end - size * count

        async with db.execute(
            """
            SELECT hour, messages, voice_minutes, members FROM guild_stats
            WHERE guild_id = ? AND hour >= ? AND hour < ?
            ORDER BY hour
            """,
            (guild_id, first, end),
        ) as cursor:
            rows = await cursor.fetchall()

        async with db.execute(
            """
            SELECT members FROM guild_stats
            WHERE guild_id = ? AND hour < ? AND members IS NOT NULL
            ORDER BY hour DESC LIMIT 1
            """,
            (guild_id, first),
        ) as cursor:
            before = await cursor.fetchone()

    messages = [0] * count
    voice = [0] * count
    members: list[int | None] = [None] * count
    for hour, hour_messages, hour_voice, hour_members in rows:
        index = (hour - first) // size
        messages[index] += hour_messages
        voice[index] += hour_voice
        if hour_members is not None:
            members[index] = hour_members

    carried = before[0] if before else None
    first_known = None
    for index, value in enumerate(members):
        if value is None:
            members[index] = carried
        else:
            carried = value
        if first_known is None:
            first_known = members[index]

    return {
        "label": label,
        "starts": list(range(first, end, size)),
        "size": size,
        "start_label": _ago(int(time.time()) - first),
        "messages": messages,
        "voice": voice,
        "members": members,
        "has_data": bool(rows),
        "total_messages": sum(messages),
        "total_voice": sum(voice),
        "member_count": carried,
        "member_change": carried - first_known if carried is not None else 0,
    }
