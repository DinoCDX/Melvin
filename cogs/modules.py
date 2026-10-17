import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

import module_settings
from globals import ERROR_MESSAGE, MELVIN_CHECK_EMOJI, MELVIN_CROSS_EMOJI, MELVIN_EMOJI
from ui import ErrorUI, SmallSeparator

log = logging.getLogger(__name__)


async def _reply_error(interaction: discord.Interaction, message: str) -> None:
    view = ErrorUI(message)
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


TOGGLE_TEMPLATE = r"modules:toggle:(?P<guild>\d+):(?P<module>[a-z0-9_]+):(?P<state>[01])"


def _toggle_id(guild_id: int, module: str, enabled: bool) -> str:
    return f"modules:toggle:{guild_id}:{module}:{int(enabled)}"


def _toggle_look(enabled: bool) -> dict:
    return {
        "label": "On" if enabled else "Off",
        "emoji": MELVIN_CHECK_EMOJI if enabled else MELVIN_CROSS_EMOJI,
        "style": discord.ButtonStyle.secondary,
    }


async def _toggle(
    interaction: discord.Interaction,
    guild_id: int,
    module: str,
    enabled: bool,
) -> None:
    if interaction.guild_id != guild_id:
        await _reply_error(interaction, "**This button belongs to another server.**")
        return

    permissions = getattr(interaction.user, "guild_permissions", None)
    if not (permissions and permissions.manage_guild):
        await _reply_error(interaction, "**You do not have permission to do this.**")
        return
    if module not in module_settings.MODULES:
        await _reply_error(interaction, ERROR_MESSAGE)
        return

    try:
        await module_settings.set_enabled(guild_id, module, enabled=not enabled)
    except Exception:
        log.exception("Failed to toggle module %s in guild %s", module, guild_id)
        await _reply_error(interaction, ERROR_MESSAGE)
        return

    await interaction.response.edit_message(view=await ModulesView.build(guild_id))


class ToggleButton(discord.ui.Button["ModulesView"]):
    def __init__(self, guild_id: int, module: str, *, enabled: bool) -> None:
        super().__init__(
            **_toggle_look(enabled),
            custom_id=_toggle_id(guild_id, module, enabled),
        )
        self.guild_id = guild_id
        self.module = module
        self.enabled = enabled

    async def callback(self, interaction: discord.Interaction) -> None:
        await _toggle(interaction, self.guild_id, self.module, self.enabled)


class ToggleDynamic(
    discord.ui.DynamicItem[discord.ui.Button],
    template=TOGGLE_TEMPLATE,
):
    def __init__(self, *, guild_id: int, module: str, enabled: bool) -> None:
        super().__init__(
            discord.ui.Button(
                **_toggle_look(enabled),
                custom_id=_toggle_id(guild_id, module, enabled),
            ),
        )
        self.guild_id = guild_id
        self.module = module
        self.enabled = enabled

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item,
        match: re.Match[str],
        /,
    ) -> ToggleDynamic:
        return cls(
            guild_id=int(match["guild"]),
            module=match["module"],
            enabled=match["state"] == "1",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _toggle(interaction, self.guild_id, self.module, self.enabled)


class ModulesView(discord.ui.LayoutView):
    def __init__(self, guild_id: int, disabled: set[str]) -> None:
        super().__init__(timeout=300)
        container = discord.ui.Container(
            discord.ui.TextDisplay(
                f"# {MELVIN_EMOJI} Modules\n"
                "-# **Turn Melvin's features on or off for this server.**",
            ),
            SmallSeparator(),
        )
        for module, (label, description) in module_settings.MODULES.items():
            container.add_item(
                discord.ui.Section(
                    f"**{label}**\n-# {description}",
                    accessory=ToggleButton(
                        guild_id,
                        module,
                        enabled=module not in disabled,
                    ),
                ),
            )
        self.add_item(container)

    @classmethod
    async def build(cls, guild_id: int) -> ModulesView:
        return cls(guild_id, await module_settings.disabled_modules(guild_id))


class ModulesCog(
    commands.Cog,
    name="modules",
    description="Turn Melvin's features on or off for a server.",
):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(ToggleDynamic)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(ToggleDynamic)

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            msg = "**You do not have permission to do this.**"
        elif isinstance(error, app_commands.NoPrivateMessage):
            msg = "**This command can only be used in a server.**"
        else:
            msg = ERROR_MESSAGE
        await _reply_error(interaction, msg)

    @app_commands.command(
        name="modules",
        description="Turn Melvin's features on or off for this server.",
    )
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def modules(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        await interaction.response.send_message(
            view=await ModulesView.build(interaction.guild.id),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModulesCog(bot))
