import logging
from collections import Counter

import discord
from discord.ext import commands, tasks

import module_settings
import server_stats
from module_registry import Module

MODULE = Module(
    "serverstats",
    "Server Stats",
    "Counts messages, voice minutes, and members for the dashboard.",
)

log = logging.getLogger(__name__)


def _voice_members(guild: discord.Guild) -> int:
    return sum(
        not member.bot
        for channel in (*guild.voice_channels, *guild.stage_channels)
        if channel != guild.afk_channel
        for member in channel.members
    )


class ServerStatsCog(
    commands.Cog,
    name="serverstats",
    description="Message, voice, and member totals for this server.",
):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self._messages: Counter[int] = Counter()
        self._written: dict[int, tuple[int, int | None]] = {}
        self._pruned_hour = 0

    async def cog_load(self) -> None:
        await server_stats.init_db()
        self.collect.start()

    async def cog_unload(self) -> None:
        self.collect.cancel()
        await self._flush()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is not None and not message.author.bot:
            self._messages[message.guild.id] += 1

    async def _flush(self, *, with_voice: bool = False) -> None:
        hour = server_stats.hour_start()
        messages, self._messages = self._messages, Counter()
        disabled = await module_settings.guilds_disabling("serverstats")

        rows = []
        for guild in self.bot.guilds:
            if guild.id in disabled:
                continue
            count = messages[guild.id]
            voice = _voice_members(guild) if with_voice else 0
            snapshot = (hour, guild.member_count)
            if count or voice or self._written.get(guild.id) != snapshot:
                rows.append((guild.id, hour, count, voice, guild.member_count))

        if rows:
            await server_stats.record(rows)
            for guild_id, row_hour, _, _, members in rows:
                self._written[guild_id] = (row_hour, members)

    @tasks.loop(minutes=1)
    async def collect(self) -> None:
        try:
            await self._flush(with_voice=True)
        except Exception:
            log.exception("Failed to save server stats")

        hour = server_stats.hour_start()
        if hour != self._pruned_hour:
            self._pruned_hour = hour
            try:
                await server_stats.prune()
            except Exception:
                log.exception("Failed to prune old server stats")

    @collect.before_loop
    async def before_collect(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerStatsCog(bot))
