import datetime
import logging

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

from globals import DATA_DIR
from ui import InfoUI

log = logging.getLogger(__name__)

MIDNIGHT = datetime.time(hour=0, minute=0, second=0, tzinfo=datetime.UTC)


class StatsCog(
    commands.Cog,
    name="stats",
    description="Commands relating to Melvins statistics",
):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.db_path = DATA_DIR / "stats.db"

    async def cog_load(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_logs (
                    command_name TEXT,
                    user_id INTEGER,
                    guild_id INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """,
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    guild_count INTEGER,
                    user_count INTEGER
                )
                """,
            )
            await conn.commit()
        self.stats_loop.start()

    async def cog_unload(self) -> None:
        self.stats_loop.cancel()

    async def _installs(self) -> tuple[int, int]:
        app = self.bot.application or await self.bot.application_info()
        return len(self.bot.guilds), app.approximate_user_install_count or 0

    async def _snapshot(self, conn: aiosqlite.Connection) -> None:
        await conn.execute(
            "INSERT INTO daily_snapshots (guild_count, user_count) VALUES (?, ?)",
            await self._installs(),
        )
        await conn.commit()

    @tasks.loop(time=MIDNIGHT)
    async def stats_loop(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await self._snapshot(conn)

    @stats_loop.before_loop
    async def before_daily_task(self) -> None:
        await self.bot.wait_until_ready()
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT EXISTS (SELECT 1 FROM daily_snapshots)",
            ) as cursor:
                (has_snapshot,) = await cursor.fetchone()
            if not has_snapshot:
                await self._snapshot(conn)

    @app_commands.command(
        name="stats",
        description="Display statistics about Melvin.",
    )
    async def stats(self, interaction: discord.Interaction) -> None:
        guild_installs, user_installs = await self._installs()

        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT COUNT(*), COUNT(*) FILTER (WHERE timestamp >= datetime('now', '-1 day'))
                FROM command_logs
                """,
            ) as cursor:
                total_commands, daily_commands = await cursor.fetchone()

            async with conn.execute(
                """
                SELECT guild_count, user_count FROM daily_snapshots
                ORDER BY abs(strftime('%s', timestamp) - strftime('%s', datetime('now', '-1 day'))) ASC
                LIMIT 1
                """,
            ) as cursor:
                snapshot = await cursor.fetchone()

        daily_installs_delta = (
            guild_installs - snapshot[0] + user_installs - snapshot[1]
            if snapshot
            else 0
        )

        await interaction.response.send_message(
            view=InfoUI(
                title="Melvin Stats",
                subtitle=(
                    f"**Guild Installs:** {guild_installs}\n"
                    f"**User Installs:** {user_installs}\n"
                    f"**Total Installs (Last 24h):** {daily_installs_delta}\n\n"
                    f"**Total Installs (All-Time):** {guild_installs + user_installs}\n\n"
                    f"**Commands Run (Last 24h):** {daily_commands}\n"
                    f"**Commands Run (All-Time):** {total_commands}\n"
                ),
            ),
        )

    @commands.Cog.listener()
    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: app_commands.Command | app_commands.ContextMenu,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT INTO command_logs (command_name, user_id, guild_id) VALUES (?, ?, ?)",
                (command.name, interaction.user.id, interaction.guild_id),
            )
            await conn.commit()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))
