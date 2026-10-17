import logging
import random
import re
import shlex
import sqlite3
import time
from dataclasses import dataclass, field

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

import module_config
import module_settings
from globals import DATA_DIR, MELVIN_EMOJI
from module_registry import Module, Setting
from ui import ErrorUI, InfoUI, Paginator, PositiveUI, open_config

MODULE = Module(
    "tags",
    "Tags",
    "Saved snippets for this server, with variables, embeds, and timestamps.",
    settings=(
        Setting(
            "prefix",
            "Chat prefix",
            "The character that posts a tag from chat, like ?rules. Up to 3 characters, no spaces.",
            "text",
            default="?",
            max_length=3,
            placeholder="?",
        ),
    ),
)

log = logging.getLogger(__name__)

NO_MENTIONS = discord.AllowedMentions.none()
DEFAULT_PREFIX = "?"
PREFIX_LENGTH = 3

TAG_NAME = re.compile(r"[a-z0-9_-]{1,32}")
TAG_CONTENT_LENGTH = 4000
MESSAGE_TEXT_LENGTH = 2000
CONTAINER_TEXT_LENGTH = 4000
TAG_AUTOCOMPLETE_LIMIT = 25
TAGS_PER_PAGE = 15
EDITABLE_LIMIT = 25

EMBED_TITLE_LENGTH = 256
EMBED_DESCRIPTION_LENGTH = 4096
EMBED_FIELD_NAME_LENGTH = 256
EMBED_FIELD_VALUE_LENGTH = 1024
EMBED_FOOTER_LENGTH = 2048
EMBED_FIELD_LIMIT = 25
BUTTON_LIMIT = 5
BUTTON_LABEL_LENGTH = 80

EMBED_KEYS = (
    "title",
    "description",
    "url",
    "color",
    "image",
    "thumbnail",
    "footer",
    "field",
)
VARIABLE = re.compile(
    r"\$\$|\$\*"
    r"|\$time(?::(?P<style>[tTdDfFR]))?"
    r"|\$(?P<num>\d+)(?P<rest>\+)?"
    r"|\$rand\[(?P<low>-?\d+)-(?P<high>-?\d+)\]"
    r"|\$pick\[(?P<options>[^\]\n]*)\]",
)
HEX_COLOR = re.compile(r"#?([0-9a-fA-F]{6})")


class TagFormatError(ValueError):
    pass


@dataclass
class TagBody:
    kind: str = "text"
    text: str = ""
    title: str | None = None
    description: str | None = None
    url: str | None = None
    color: discord.Color | None = None
    image: str | None = None
    thumbnail: str | None = None
    footer: str | None = None
    embed_fields: list[tuple[str, str]] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    buttons: list[tuple[str, str]] = field(default_factory=list)


def _tag_name(name: str) -> str | None:
    name = name.strip().lower()
    return name if TAG_NAME.fullmatch(name) else None


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _link(value: str, label: str) -> str:
    if not value.startswith(("http://", "https://")) or " " in value:
        msg = f"`{label}` must be a link starting with http:// or https://."
        raise TagFormatError(msg)
    return value


def _color(value: str) -> discord.Color:
    match = HEX_COLOR.fullmatch(value.strip())
    if match is None:
        msg = "Colors must be a hex code like `#F4A261`."
        raise TagFormatError(msg)
    return discord.Color(int(match.group(1), 16))


def _limit(value: str, limit: int, label: str) -> str:
    if len(value) > limit:
        msg = f"`{label}` can be at most {limit} characters."
        raise TagFormatError(msg)
    return value


def _parse_embed(lines: list[str]) -> TagBody:
    body = TagBody(kind="embed")
    current = None
    for line in lines:
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if sep and key in EMBED_KEYS:
            current = key
            value = value.strip()
            if key == "field":
                parts = [part.strip() for part in value.split("|")]
                if len(parts) != 2 or not all(parts):
                    msg = "Fields look like `field: name | value`."
                    raise TagFormatError(msg)
                body.embed_fields.append(
                    (
                        _limit(parts[0], EMBED_FIELD_NAME_LENGTH, "field name"),
                        _limit(parts[1], EMBED_FIELD_VALUE_LENGTH, "field value"),
                    ),
                )
                if len(body.embed_fields) > EMBED_FIELD_LIMIT:
                    msg = f"An embed can have at most {EMBED_FIELD_LIMIT} fields."
                    raise TagFormatError(msg)
            elif key == "description":
                body.description = _limit(value, EMBED_DESCRIPTION_LENGTH, "description")
            elif key == "title":
                body.title = _limit(value, EMBED_TITLE_LENGTH, "title")
            elif key == "footer":
                body.footer = _limit(value, EMBED_FOOTER_LENGTH, "footer")
            elif key == "color":
                body.color = _color(value)
            elif key == "url":
                body.url = _link(value, "url")
            elif key == "image":
                body.image = _link(value, "image")
            elif key == "thumbnail":
                body.thumbnail = _link(value, "thumbnail")
        elif current == "description":
            body.description = (
                line if body.description is None else f"{body.description}\n{line}"
            )
        elif line.strip():
            msg = f"Unknown line in an embed: `{line.strip()[:40]}`."
            raise TagFormatError(msg)

    if body.title is None and body.description is None and not body.embed_fields:
        msg = "An embed needs a `title`, `description`, or `field`."
        raise TagFormatError(msg)
    return body


def _parse_container(lines: list[str]) -> TagBody:
    body = TagBody(kind="container")
    block: list[str] = []

    def flush() -> None:
        text = "\n".join(block).strip()
        if text:
            body.blocks.append(text)
        block.clear()

    for line in lines:
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if line.strip() == "---":
            flush()
        elif sep and key == "color":
            body.color = _color(value)
        elif sep and key == "button":
            parts = [part.strip() for part in value.split("|")]
            if len(parts) != 2 or not parts[0]:
                msg = "Buttons look like `button: label | link`."
                raise TagFormatError(msg)
            body.buttons.append(
                (parts[0][:BUTTON_LABEL_LENGTH], _link(parts[1], "button")),
            )
            if len(body.buttons) > BUTTON_LIMIT:
                msg = f"A container can have at most {BUTTON_LIMIT} buttons."
                raise TagFormatError(msg)
        else:
            block.append(line)
    flush()

    if not body.blocks:
        msg = "A container needs some text."
        raise TagFormatError(msg)
    if sum(len(text) for text in body.blocks) > CONTAINER_TEXT_LENGTH:
        msg = f"A container's text can be at most {CONTAINER_TEXT_LENGTH} characters."
        raise TagFormatError(msg)
    return body


def parse_tag(raw: str) -> TagBody:
    text = raw.strip()
    if not text:
        msg = "A tag needs some text."
        raise TagFormatError(msg)

    lines = text.splitlines()
    marker = lines[0].strip().lower()
    if marker == "[embed]":
        return _parse_embed(lines[1:])
    if marker == "[container]":
        return _parse_container(lines[1:])
    if len(text) > MESSAGE_TEXT_LENGTH:
        msg = f"A plain text tag can be at most {MESSAGE_TEXT_LENGTH} characters."
        raise TagFormatError(msg)
    return TagBody(text=text)


def check_tag(raw: str) -> str | None:
    try:
        parse_tag(raw)
    except TagFormatError as error:
        return str(error)
    return None


def split_args(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _expand(text: str, args: list[str], now: int) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "$$":
            return "$"
        if token == "$*":
            return " ".join(args)
        if token.startswith("$time"):
            return f"<t:{now}:{match.group('style') or 'f'}>"
        if token.startswith("$rand"):
            low, high = sorted((int(match.group("low")), int(match.group("high"))))
            return str(random.randint(low, high))
        if token.startswith("$pick"):
            options = [option.strip() for option in match.group("options").split("|")]
            options = [option for option in options if option]
            return random.choice(options) if options else ""
        number = int(match.group("num"))
        if number < 1 or number > len(args):
            return ""
        if match.group("rest"):
            return " ".join(args[number - 1 :])
        return args[number - 1]

    return VARIABLE.sub(replace, text)


def _text_or(value: str, fallback: str) -> str:
    return value if value.strip() else fallback


def render(
    body: TagBody,
    args: list[str],
    now: int,
) -> tuple[str | None, discord.Embed | None, discord.ui.LayoutView | None]:
    def expand(value: str) -> str:
        return _expand(value, args, now)

    if body.kind == "embed":
        embed = discord.Embed(
            title=_cut(expand(body.title), EMBED_TITLE_LENGTH) if body.title else None,
            description=(
                _cut(expand(body.description), EMBED_DESCRIPTION_LENGTH)
                if body.description
                else None
            ),
            url=body.url,
            color=body.color,
        )
        if body.image:
            embed.set_image(url=body.image)
        if body.thumbnail:
            embed.set_thumbnail(url=body.thumbnail)
        if body.footer:
            embed.set_footer(text=_cut(expand(body.footer), EMBED_FOOTER_LENGTH))
        for name, value in body.embed_fields:
            embed.add_field(
                name=_text_or(_cut(expand(name), EMBED_FIELD_NAME_LENGTH), "\u200b"),
                value=_text_or(_cut(expand(value), EMBED_FIELD_VALUE_LENGTH), "\u200b"),
                inline=False,
            )
        return None, embed, None

    if body.kind == "container":
        container = discord.ui.Container(accent_color=body.color)
        budget = CONTAINER_TEXT_LENGTH
        for block in body.blocks:
            text = _cut(expand(block), budget)
            budget -= len(text)
            container.add_item(discord.ui.TextDisplay(_text_or(text, "\u200b")))
        if body.buttons:
            container.add_item(
                discord.ui.ActionRow(
                    *(
                        discord.ui.Button(
                            label=_text_or(
                                _cut(expand(label), BUTTON_LABEL_LENGTH),
                                "Link",
                            ),
                            style=discord.ButtonStyle.link,
                            url=url,
                        )
                        for label, url in body.buttons
                    ),
                ),
            )
        view = discord.ui.LayoutView()
        view.add_item(container)
        return None, None, view

    return _text_or(expand(body.text)[:MESSAGE_TEXT_LENGTH], "\u200b"), None, None


def _send_kwargs(
    rendered: tuple[str | None, discord.Embed | None, discord.ui.LayoutView | None],
) -> dict:
    content, embed, view = rendered
    if view is not None:
        return {"view": view}
    kwargs: dict = {"content": content}
    if embed is not None:
        kwargs["embed"] = embed
    return kwargs


def _can_manage(interaction: discord.Interaction) -> bool:
    member = interaction.user
    return (
        isinstance(member, discord.Member) and member.guild_permissions.manage_messages
    )


async def _respond(interaction: discord.Interaction, view: discord.ui.LayoutView) -> None:
    await interaction.response.send_message(view=view, ephemeral=True)


class TagEditModal(discord.ui.Modal):
    def __init__(self, cog: "TagsCog", name: str, raw: str) -> None:
        super().__init__(title=f"Edit {name}"[:45])
        self.cog = cog
        self.tag = name
        self.body_input = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=raw,
            max_length=TAG_CONTENT_LENGTH,
            required=False,
        )
        self.add_item(
            discord.ui.Label(
                text="Content",
                description="Leave empty and tick Delete to remove the tag.",
                component=self.body_input,
            ),
        )
        self.delete_box = discord.ui.Checkbox()
        self.add_item(
            discord.ui.Label(text="Delete this tag", component=self.delete_box),
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _can_manage(interaction):
            await _respond(
                interaction,
                ErrorUI("**You need Manage Messages to edit tags.**"),
            )
            return

        if self.delete_box.value:
            deleted = await self.cog.delete_tag(interaction.guild_id, self.tag)
            if not deleted:
                await _respond(interaction, ErrorUI(f"**There is no tag called `{self.tag}`.**"))
                return
            await _respond(
                interaction,
                PositiveUI(title="Tag deleted", subtitle=f"**`{self.tag}`** has been removed."),
            )
            return

        raw = self.body_input.value.strip()
        error = check_tag(raw) if raw else "A tag needs some text. Tick Delete to remove it."
        if error:
            await _respond(interaction, ErrorUI(f"**{error}**"))
            return

        await self.cog.update_tag(interaction.guild_id, self.tag, raw)
        await _respond(
            interaction,
            PositiveUI(title="Tag updated", subtitle=f"**`{self.tag}`** has new text."),
        )


class TagPicker(discord.ui.Select):
    def __init__(self, cog: "TagsCog", names: list[str]) -> None:
        self.cog = cog
        super().__init__(
            placeholder="Edit a tag",
            options=[
                discord.SelectOption(label=name, value=name)
                for name in names[:EDITABLE_LIMIT]
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _can_manage(interaction):
            await _respond(
                interaction,
                ErrorUI("**You need Manage Messages to edit tags.**"),
            )
            return

        name = self.values[0]
        raw = await self.cog.lookup(interaction.guild_id, name)
        if raw is None:
            await _respond(interaction, ErrorUI("**That tag no longer exists.**"))
            return
        await interaction.response.send_modal(TagEditModal(self.cog, name, raw))


@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class TagsCog(
    commands.GroupCog,
    name="tags",
    description="Saved text snippets for this server.",
):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.db_path = DATA_DIR / "tags.db"

    async def cog_load(self) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tags (
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    content TEXT NOT NULL,
                    author_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (guild_id, name)
                )
                """,
            )
            await conn.commit()

    async def lookup(self, guild_id: int, tag: str) -> str | None:
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT content FROM tags WHERE guild_id = ? AND name = ?",
                (guild_id, tag),
            ) as cursor:
                row = await cursor.fetchone()
        return row[0] if row else None

    async def update_tag(self, guild_id: int, tag: str, content: str) -> bool:
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "UPDATE tags SET content = ?, updated_at = ? WHERE guild_id = ? AND name = ?",
                (content, time.time(), guild_id, tag),
            ) as cursor:
                changed = cursor.rowcount
            await conn.commit()
        return bool(changed)

    async def delete_tag(self, guild_id: int, tag: str) -> bool:
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "DELETE FROM tags WHERE guild_id = ? AND name = ?",
                (guild_id, tag),
            ) as cursor:
                deleted = cursor.rowcount
            await conn.commit()
        return bool(deleted)

    async def _prefix(self, guild_id: int) -> str:
        value = await module_config.get(guild_id, "tags", "prefix") or DEFAULT_PREFIX
        if 1 <= len(value) <= PREFIX_LENGTH and not any(c.isspace() for c in value):
            return value
        return DEFAULT_PREFIX

    async def _name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT name FROM tags
                WHERE guild_id = ? AND instr(name, ?) = 1
                ORDER BY name
                LIMIT ?
                """,
                (interaction.guild_id, current.lower(), TAG_AUTOCOMPLETE_LIMIT),
            ) as cursor:
                rows = await cursor.fetchall()
        return [app_commands.Choice(name=name, value=name) for (name,) in rows]

    @app_commands.command(name="use", description="Post a saved tag.")
    @app_commands.describe(
        name="The tag to post.",
        args="Values for $1, $2 and so on, separated by spaces.",
    )
    async def use(
        self,
        interaction: discord.Interaction,
        name: str,
        args: str = "",
    ) -> None:
        tag = _tag_name(name)
        raw = await self.lookup(interaction.guild_id, tag) if tag else None
        if raw is None:
            await _respond(interaction, ErrorUI(f"**There is no tag called `{name}`.**"))
            return

        rendered = render(parse_tag(raw), split_args(args), int(time.time()))
        await interaction.response.send_message(
            **_send_kwargs(rendered),
            allowed_mentions=NO_MENTIONS,
        )

    @use.autocomplete("name")
    async def use_name(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self._name_autocomplete(interaction, current)

    @app_commands.command(name="create", description="Save a new tag.")
    @app_commands.describe(
        name="Letters, numbers, - or _, up to 32 characters.",
        content="What the tag says. See the tags docs for variables and embeds.",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        content: app_commands.Range[str, 1, TAG_CONTENT_LENGTH],
    ) -> None:
        tag = _tag_name(name)
        if tag is None:
            await _respond(
                interaction,
                ErrorUI(
                    "**Tag names use letters, numbers, hyphens, and underscores, "
                    "up to 32 characters.**",
                ),
            )
            return

        error = check_tag(content)
        if error:
            await _respond(interaction, ErrorUI(f"**{error}**"))
            return

        now = time.time()
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO tags (guild_id, name, content, author_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        interaction.guild_id,
                        tag,
                        content.strip(),
                        interaction.user.id,
                        now,
                        now,
                    ),
                )
                await conn.commit()
        except sqlite3.IntegrityError:
            await _respond(
                interaction,
                ErrorUI(f"**A tag called `{tag}` already exists. Use /tags edit to change it.**"),
            )
            return

        await _respond(
            interaction,
            PositiveUI(title="Tag created", subtitle=f"**`{tag}`** can now be used with /tags use."),
        )

    @app_commands.command(name="edit", description="Change the text of a tag.")
    @app_commands.describe(
        name="The tag to change.",
        content="The new text for the tag.",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def edit(
        self,
        interaction: discord.Interaction,
        name: str,
        content: app_commands.Range[str, 1, TAG_CONTENT_LENGTH],
    ) -> None:
        tag = _tag_name(name)
        error = check_tag(content)
        if error:
            await _respond(interaction, ErrorUI(f"**{error}**"))
            return

        if tag is None or not await self.update_tag(interaction.guild_id, tag, content.strip()):
            await _respond(interaction, ErrorUI(f"**There is no tag called `{name}`.**"))
            return
        await _respond(
            interaction,
            PositiveUI(title="Tag updated", subtitle=f"**`{tag}`** has new text."),
        )

    @edit.autocomplete("name")
    async def edit_name(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self._name_autocomplete(interaction, current)

    @app_commands.command(name="delete", description="Delete a tag.")
    @app_commands.describe(name="The tag to delete.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def delete(self, interaction: discord.Interaction, name: str) -> None:
        tag = _tag_name(name)
        if tag is None or not await self.delete_tag(interaction.guild_id, tag):
            await _respond(interaction, ErrorUI(f"**There is no tag called `{name}`.**"))
            return
        await _respond(
            interaction,
            PositiveUI(title="Tag deleted", subtitle=f"**`{tag}`** has been removed."),
        )

    @delete.autocomplete("name")
    async def delete_name(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self._name_autocomplete(interaction, current)

    @app_commands.command(name="list", description="List this server's tags.")
    async def list_tags(self, interaction: discord.Interaction) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT name FROM tags WHERE guild_id = ? ORDER BY name",
                (interaction.guild_id,),
            ) as cursor:
                names = [name for (name,) in await cursor.fetchall()]

        if not names:
            await _respond(
                interaction,
                InfoUI(title="Tags", subtitle="**This server has no tags yet.**"),
            )
            return

        title = f"### {MELVIN_EMOJI} Tags"
        if len(names) > EDITABLE_LIMIT:
            title += f"\n-# Edit picks from the first {EDITABLE_LIMIT} tags."
        view = Paginator(
            title,
            names,
            data_name="Tags",
            per_page=TAGS_PER_PAGE,
            container=True,
            timeout=300,
        )
        view.add_over(discord.ui.ActionRow(TagPicker(self, names)))
        await interaction.response.send_message(view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="config", description="Change tag settings for this server.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction) -> None:
        await open_config(interaction, "tags")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not await module_settings.is_enabled(message.guild.id, "tags"):
            return

        prefix = await self._prefix(message.guild.id)
        if not message.content.startswith(prefix):
            return
        parts = message.content[len(prefix) :].split(maxsplit=1)
        tag = _tag_name(parts[0]) if parts else None
        if tag is None:
            return
        raw = await self.lookup(message.guild.id, tag)
        if raw is None:
            return

        args = split_args(parts[1]) if len(parts) > 1 else []
        try:
            rendered = render(parse_tag(raw), args, int(time.time()))
        except TagFormatError:
            log.warning("tag %s in guild %s no longer parses", tag, message.guild.id)
            return

        try:
            await message.reply(
                **_send_kwargs(rendered),
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            log.warning("failed to post tag %s in channel %s", tag, message.channel.id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("DELETE FROM tags WHERE guild_id = ?", (guild.id,))
                await conn.commit()
        except Exception:
            log.exception("failed to clean up tags for departed guild %s", guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TagsCog(bot))
