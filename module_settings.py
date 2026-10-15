import time

import aiosqlite

from globals import DATA_DIR
from module_registry import MODULES, modules

DB_PATH = DATA_DIR / "modules.db"
CACHE_SECONDS = 10

_cache: dict[int, tuple[float, set[str]]] = {}
_db_ready = False


async def init_db() -> None:
    global _db_ready
    if _db_ready:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_modules (
                guild_id INTEGER NOT NULL,
                module TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                PRIMARY KEY (guild_id, module)
            )
            """,
        )
        await db.commit()
    _db_ready = True


async def disabled_modules(guild_id: int) -> set[str]:
    now = time.monotonic()
    cached = _cache.get(guild_id)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]

    async with (
        aiosqlite.connect(DB_PATH) as db,
        db.execute(
            "SELECT module, enabled FROM guild_modules WHERE guild_id = ?",
            (guild_id,),
        ) as cursor,
    ):
        stored = {row[0]: bool(row[1]) async for row in cursor}

    disabled = {
        key
        for key, module in modules().items()
        if not stored.get(key, module.enabled_by_default)
    }

    _cache[guild_id] = (now, disabled)
    return disabled


async def guilds_disabling(module: str) -> set[int]:
    async with (
        aiosqlite.connect(DB_PATH) as db,
        db.execute(
            "SELECT guild_id FROM guild_modules WHERE module = ? AND enabled = 0",
            (module,),
        ) as cursor,
    ):
        return {row[0] async for row in cursor}


async def is_enabled(guild_id: int | None, module: str) -> bool:
    if guild_id is None or module not in MODULES:
        return True
    return module not in await disabled_modules(guild_id)


async def set_enabled(guild_id: int, module: str, *, enabled: bool) -> None:
    if module not in MODULES:
        msg = f"unknown module {module!r}"
        raise ValueError(msg)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO guild_modules (guild_id, module, enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, module) DO UPDATE SET enabled = excluded.enabled
            """,
            (guild_id, module, int(enabled)),
        )
        await db.commit()

    _cache.pop(guild_id, None)
