import asyncio
import logging
import math
import operator
from datetime import UTC, datetime
from itertools import groupby

import aiosqlite

import charts
from globals import DATA_DIR

DB_PATH = DATA_DIR / "uptime.db"
STATS_DB_PATH = DATA_DIR / "stats.db"
START_TIME_FILE = DATA_DIR / "start_time.txt"

MAX_HISTORY_POINTS = 14
CHART_WIDTH = 200
CHART_HEIGHT = 60

DEGRADED_GATEWAY_MS = 400
DEGRADED_API_MS = 800
STALE_AFTER_SECONDS = 5 * 60
STATE_ORDER = ["ok", "degraded", "down"]
STATE_LABELS = {
    "ok": "Everything appears normal :)",
    "degraded": "Things seem a little slow right now :/",
    "down": "Melvin doesn't seem to be responding :(",
    "unknown": "No data yet :|",
}

log = logging.getLogger(__name__)

_db_ready = False


def set_start_time() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    START_TIME_FILE.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")


def _get_start_time() -> datetime | None:
    try:
        if START_TIME_FILE.is_file():
            return datetime.fromisoformat(
                START_TIME_FILE.read_text(encoding="utf-8").strip(),
            )
    except Exception:
        log.exception("retrieving the start time failed")
    return None


def _format_seconds(seconds: int) -> str:
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60

    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _seconds_since(checked_at: str) -> int:
    return int((datetime.now(UTC) - datetime.fromisoformat(checked_at)).total_seconds())


def _format_relative(checked_at: str) -> str:
    seconds = _seconds_since(checked_at)

    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _format_span(rows: list[aiosqlite.Row]) -> str | None:
    if len(rows) < 2:
        return None
    first = datetime.fromisoformat(rows[0]["checked_at"])
    last = datetime.fromisoformat(rows[-1]["checked_at"])
    seconds = int((last - first).total_seconds())
    return _format_seconds(seconds) if seconds >= 60 else None


async def init_db() -> None:
    global _db_ready
    if _db_ready:
        return

    await asyncio.to_thread(DATA_DIR.mkdir, parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS shard_latency (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shard_id INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                api_latency_ms REAL,
                checked_at TEXT NOT NULL
            )
            """,
        )
        cursor = await db.execute("PRAGMA table_info(shard_latency)")
        if "api_latency_ms" not in {row[1] for row in await cursor.fetchall()}:
            await db.execute("ALTER TABLE shard_latency ADD COLUMN api_latency_ms REAL")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_shard_latency_shard_checked "
            "ON shard_latency (shard_id, checked_at)",
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_count INTEGER NOT NULL,
                member_count INTEGER NOT NULL,
                checked_at TEXT NOT NULL
            )
            """,
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_metrics_checked ON bot_metrics (checked_at)",
        )
        await db.commit()
    _db_ready = True


async def record_latency(
    shard_id: int,
    latency_ms: float,
    api_latency_ms: float,
) -> None:
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO shard_latency (shard_id, latency_ms, api_latency_ms, checked_at)
            VALUES (?, ?, ?, ?)
            """,
            (shard_id, latency_ms, api_latency_ms, datetime.now(UTC).isoformat()),
        )
        await db.execute(
            """
            DELETE FROM shard_latency
            WHERE shard_id = ? AND id NOT IN (
                SELECT id FROM shard_latency
                WHERE shard_id = ?
                ORDER BY checked_at DESC
                LIMIT ?
            )
            """,
            (shard_id, shard_id, MAX_HISTORY_POINTS),
        )
        await db.commit()


async def record_metrics(guild_count: int, member_count: int) -> None:
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO bot_metrics (guild_count, member_count, checked_at)
            VALUES (?, ?, ?)
            """,
            (guild_count, member_count, datetime.now(UTC).isoformat()),
        )
        await db.execute(
            """
            DELETE FROM bot_metrics
            WHERE id NOT IN (
                SELECT id FROM bot_metrics
                ORDER BY checked_at DESC
                LIMIT ?
            )
            """,
            (MAX_HISTORY_POINTS,),
        )
        await db.commit()


def _shard_state(latency_ms: float, api_latency_ms: float, checked_at: str) -> str:
    if _seconds_since(checked_at) > STALE_AFTER_SECONDS:
        return "down"
    if latency_ms > DEGRADED_GATEWAY_MS or api_latency_ms > DEGRADED_API_MS:
        return "degraded"
    return "ok"


def summarize_status(shards: list[dict]) -> dict[str, str]:
    state = (
        max((shard["state"] for shard in shards), key=STATE_ORDER.index)
        if shards
        else "unknown"
    )
    return {"state": state, "label": STATE_LABELS[state]}


def _build_shard(shard_id: int, rows: list[aiosqlite.Row], uptime: str) -> dict:
    gateway_history = [row["latency_ms"] for row in rows]
    api_history = [row["api_latency_ms"] or 0.0 for row in rows]
    lo = min(*gateway_history, *api_history)
    hi = max(*gateway_history, *api_history)
    gateway_ys = charts.scale(gateway_history, lo, hi, CHART_HEIGHT)
    api_ys = charts.scale(api_history, lo, hi, CHART_HEIGHT)
    peak_ys = list(map(min, gateway_ys, api_ys, strict=True))
    latest = rows[-1]
    latency_ms = round(gateway_history[-1])
    api_latency_ms = round(api_history[-1])

    return {
        "shard_id": shard_id,
        "state": _shard_state(latency_ms, api_latency_ms, latest["checked_at"]),
        "latency_ms": latency_ms,
        "api_latency_ms": api_latency_ms,
        "last_checked": _format_relative(latest["checked_at"]),
        "uptime": uptime,
        "span": _format_span(rows),
        "chart_points": charts.area_points(gateway_ys, CHART_WIDTH, CHART_HEIGHT),
        "api_chart_points": charts.area_points(api_ys, CHART_WIDTH, CHART_HEIGHT),
        "tooltip_points": charts.tooltip_points(
            [_format_relative(row["checked_at"]) for row in rows],
            peak_ys,
            CHART_HEIGHT,
            [
                ("Gateway", gateway_history, "ms", "series-1"),
                ("API", api_history, "ms", "series-2"),
            ],
        ),
        "chart_width": CHART_WIDTH,
        "chart_height": CHART_HEIGHT,
    }


async def get_shard_status() -> list[dict]:
    await init_db()

    start_time = _get_start_time()
    uptime = (
        _format_seconds(int((datetime.now(UTC) - start_time).total_seconds()))
        if start_time
        else "—"
    )

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT shard_id, latency_ms, api_latency_ms, checked_at FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY shard_id ORDER BY checked_at DESC
                ) AS rank
                FROM shard_latency
            )
            WHERE rank <= ?
            ORDER BY shard_id, checked_at
            """,
            (MAX_HISTORY_POINTS,),
        ) as cursor:
            all_rows = await cursor.fetchall()

    shards = []
    for shard_id, group in groupby(all_rows, key=operator.itemgetter("shard_id")):
        rows = [row for row in group if math.isfinite(row["latency_ms"])]
        if rows:
            shards.append(_build_shard(shard_id, rows, uptime))
    return shards


async def _get_command_count() -> int:
    if not await asyncio.to_thread(STATS_DB_PATH.is_file):
        return 0
    try:
        async with (
            aiosqlite.connect(STATS_DB_PATH) as db,
            db.execute("SELECT COUNT(*) FROM command_logs") as cursor,
        ):
            row = await cursor.fetchone()
    except aiosqlite.OperationalError:
        log.exception("counting commands failed")
        return 0
    return row[0] if row else 0


async def _get_latest_metrics() -> aiosqlite.Row | None:
    await init_db()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT guild_count, member_count, checked_at FROM bot_metrics
            ORDER BY checked_at DESC
            LIMIT 1
            """,
        ) as cursor:
            return await cursor.fetchone()


async def get_metrics_status() -> dict | None:
    latest, command_count = await asyncio.gather(
        _get_latest_metrics(),
        _get_command_count(),
    )
    if not latest:
        return None

    return {
        "guild_count": latest["guild_count"],
        "member_count": latest["member_count"],
        "command_count": command_count,
        "last_checked": _format_relative(latest["checked_at"]),
    }
