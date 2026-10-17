# a blank module to copy. files starting with an underscore aren't loaded.
#   1. copy it to cogs/<key>.py and replace "template" with the key
#   2. fill in MODULE
#   3. restart the bot
# see docs/developers.md for details

import logging

import discord
from discord import app_commands
from discord.ext import commands

import module_config
import module_settings
from module_registry import Module, Setting
from ui import ResponseUI, open_config

log = logging.getLogger(__name__)

KEY = "template"

# label and description show on /modules and the dashboard. settings are optional,
# without them remove the config command
MODULE = Module(
    KEY,
    "Template",
    "One short line about what it does.",
    settings=(
        Setting(
            "channel",
            "Channel",
            "Where this module posts. Nothing is posted until one is set.",
            "channel",
        ),
    ),
)


# server only, remove both to allow dms and user installs. these only work on a
# GroupCog, a cog with no commands should be a plain commands.Cog instead
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class TemplateCog(
    commands.GroupCog,
    name=KEY,
    description="you'd describe what this cog / module does here",
):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot

    # blocked automatically when the module is off
    @app_commands.command(name="hello", description="Say hello.")
    async def hello(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            view=ResponseUI(f"**Hello, {interaction.user.mention}!**"),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="config",
        description="Change this module's settings for this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction) -> None:
        await open_config(interaction, KEY)

    # listeners aren't blocked when the module is off, so they check it themselves
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot or not await module_settings.is_enabled(member.guild.id, KEY):
            return
        channel_id = await module_config.get(member.guild.id, KEY, "channel")
        channel = self.bot.get_channel(int(channel_id)) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(
                    view=ResponseUI(f"**{member.mention} joined.**"),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                pass

    # runs even when the module is off, so a removed server's settings are cleared
    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if not module_config.is_configurable(KEY):
            return
        try:
            await module_config.clear_module(guild.id, KEY)
        except Exception:
            log.exception("Couldn't clean up %s settings for guild %s", KEY, guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TemplateCog(bot))
