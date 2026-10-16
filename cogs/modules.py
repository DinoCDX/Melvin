import logging

import discord
from discord import app_commands
from discord.ext import commands

import module_settings
from globals import ERROR_MESSAGE, MELVIN_CHECK_EMOJI, MELVIN_CROSS_EMOJI, MELVIN_EMOJI
from ui import ErrorUI, Paginator

log = logging.getLogger(__name__)

PAGE_SIZE = 8
TITLE = f"# {MELVIN_EMOJI} Modules\n-# **Turn Melvin's features on or off for this server.**"
FILTERS = (
    ("all", "All modules", "Every module."),
    ("on", "Enabled", "Only modules that are on."),
    ("off", "Disabled", "Only modules that are off."),
)


async def _reply_error(interaction: discord.Interaction, message: str) -> None:
    view = ErrorUI(message)
    if interaction.response.is_done():
        await interaction.followup.send(view=view, ephemeral=True)
    else:
        await interaction.response.send_message(view=view, ephemeral=True)


def _order(filter_by: str, disabled: set[str]) -> list[str]:
    keys = list(module_settings.MODULES)
    if filter_by == "on":
        keys = [key for key in keys if key not in disabled]
    elif filter_by == "off":
        keys = [key for key in keys if key in disabled]
    return sorted(keys, key=lambda key: key in disabled)


class ToggleButton(discord.ui.Button["ModulesView"]):
    def __init__(self, guild_id: int, module: str, *, enabled: bool) -> None:
        super().__init__(
            label="On" if enabled else "Off",
            emoji=MELVIN_CHECK_EMOJI if enabled else MELVIN_CROSS_EMOJI,
            style=discord.ButtonStyle.secondary,
        )
        self.guild_id = guild_id
        self.module = module
        self.enabled = enabled

    async def callback(self, interaction: discord.Interaction) -> None:
        permissions = getattr(interaction.user, "guild_permissions", None)
        if not (permissions and permissions.manage_guild):
            await _reply_error(
                interaction,
                "**You do not have permission to do this.**",
            )
            return

        try:
            await module_settings.set_enabled(
                self.guild_id,
                self.module,
                enabled=not self.enabled,
            )
        except Exception:
            log.exception(
                "Failed to toggle module %s in guild %s",
                self.module,
                self.guild_id,
            )
            await _reply_error(interaction, ERROR_MESSAGE)
            return

        view = self.view
        if not isinstance(view, ModulesView):
            return
        await view.refresh(reorder=False)
        await interaction.response.edit_message(view=view)


class FilterSelect(discord.ui.Select["ModulesView"]):
    def __init__(self, current: str) -> None:
        super().__init__(
            placeholder="Show modules",
            options=[
                discord.SelectOption(
                    label=label,
                    value=value,
                    description=description,
                    default=value == current,
                )
                for value, label, description in FILTERS
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ModulesView):
            return
        view.filter_by = self.values[0]
        for option in self.options:
            option.default = option.value == view.filter_by
        await view.refresh(reorder=True)
        await interaction.response.edit_message(view=view)


class ModulesView(Paginator):
    def __init__(
        self,
        guild_id: int,
        disabled: set[str],
        *,
        filter_by: str = "all",
        page: int = 0,
        order: list[str] | None = None,
    ) -> None:
        self.guild_id = guild_id
        self.filter_by = filter_by
        self.order = order if order is not None else _order(filter_by, disabled)
        super().__init__(
            TITLE,
            self._sections(disabled),
            data_name="modules",
            per_page=PAGE_SIZE,
            container=True,
            timeout=300,
        )
        self.add_over(discord.ui.ActionRow(FilterSelect(filter_by)))
        self.current_page = page
        self.update_data(TITLE, self._sections(disabled), keep_page=True)

    def _sections(self, disabled: set[str]) -> list[discord.ui.Section]:
        sections: list[discord.ui.Section] = []
        for key in self.order:
            label, description = module_settings.MODULES[key]
            sections.append(
                discord.ui.Section(
                    f"**{label}**\n-# {description}",
                    accessory=ToggleButton(
                        self.guild_id,
                        key,
                        enabled=key not in disabled,
                    ),
                ),
            )
        return sections

    async def refresh(self, *, reorder: bool) -> None:
        disabled = await module_settings.disabled_modules(self.guild_id)
        if reorder:
            self.order = _order(self.filter_by, disabled)
        self.update_data(TITLE, self._sections(disabled), keep_page=not reorder)

    @classmethod
    async def build(cls, guild_id: int) -> "ModulesView":
        return cls(guild_id, await module_settings.disabled_modules(guild_id))


class ModulesCog(
    commands.Cog,
    name="modules",
    description="Turn Melvin's features on or off for a server.",
):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot

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
        view = await ModulesView.build(interaction.guild.id)
        await interaction.response.send_message(view=view)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModulesCog(bot))