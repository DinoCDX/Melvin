import logging
import math
import random
import re
import shlex
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

import module_config
import module_settings
from globals import DATA_DIR, MELVIN_EMOJI
from helpers.commands import (
    MAX_COOLDOWN,
    CommandError,
    access_error,
    bind_args,
    clean_actions,
    clean_args,
    clean_cooldown,
    clean_permission,
    describe_permission,
    parse_actions,
    parse_args,
    snowflake,
)
from module_registry import Module, Setting
from ui import (
    ErrorUI,
    InfoUI,
    LargeSeparator,
    Paginator,
    PositiveUI,
    SmallSeparator,
    open_config,
)

MODULE = Module(
    "tags",
    "Tags",
    "Saved snippets and custom commands, with arguments, permissions, cooldowns, and role actions.",
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
TAG_COLUMNS = (
    ("args", "TEXT NOT NULL DEFAULT ''"),
    ("permission", "TEXT NOT NULL DEFAULT 'none'"),
    ("cooldown", "INTEGER NOT NULL DEFAULT 0"),
    ("actions", "TEXT NOT NULL DEFAULT ''"),
)

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
    r"|\$pick\[(?P<options>[^\]\n]*)\]"
    r"|\$shuffle\[(?P<shuffle>[^\]\n]*)\]"
    r"|\$roll\[(?P<count>\d{1,2})d(?P<sides>\d{1,4})\]"
    r"|\$(?P<case>upper|lower)\[(?P<cased>[^\]\n]*)\]"
    r"|\$user\b|\$server\b"
)
HEX_COLOR = re.compile(r"#?([0-9a-fA-F]{6})")
SEPARATOR = re.compile(r"\[separator(?::(?P<size>small|large))?\]", re.IGNORECASE)
ROLL_MAX_DICE = 20
ROLL_MAX_SIDES = 1000


class TagFormatError(ValueError):
    pass


@dataclass(frozen=True)
class Tag:
    name: str
    content: str
    args: str
    permission: str
    cooldown: int
    actions: str
    author_id: int
    created_at: float


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
    items: list[tuple[str, str]] = field(default_factory=list)
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
            body.items.append(("text", text))
        block.clear()

    for line in lines:
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if separator := SEPARATOR.fullmatch(line.strip()):
            flush()
            size = (separator.group("size") or "small").lower()
            body.items.append((size, ""))
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

    texts = [text for kind, text in body.items if kind == "text"]
    if not texts:
        msg = "A container needs some text."
        raise TagFormatError(msg)
    if sum(len(text) for text in texts) > CONTAINER_TEXT_LENGTH:
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


def expand_variables(
    text: str,
    args: list[str],
    now: int,
    context: dict[str, str],
) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "$$":
            return "$"
        if token == "$*":
            return " ".join(args)
        if token == "$user":
            return context.get("user", "")
        if token == "$server":
            return context.get("server", "")
        if token.startswith("$time"):
            return f"<t:{now}:{match.group('style') or 'f'}>"
        if token.startswith("$rand"):
            low, high = sorted((int(match.group("low")), int(match.group("high"))))
            return str(random.randint(low, high))
        if token.startswith("$pick"):
            options = [option.strip() for option in match.group("options").split("|")]
            options = [option for option in options if option]
            return random.choice(options) if options else ""
        if token.startswith("$shuffle"):
            options = [option.strip() for option in match.group("shuffle").split("|")]
            options = [option for option in options if option]
            random.shuffle(options)
            return ", ".join(options)
        if token.startswith("$roll"):
            count = min(max(int(match.group("count")), 1), ROLL_MAX_DICE)
            sides = min(max(int(match.group("sides")), 2), ROLL_MAX_SIDES)
            return str(sum(random.randint(1, sides) for _ in range(count)))
        if match.group("case"):
            inner = expand_variables(match.group("cased"), args, now, context)
            return inner.upper() if match.group("case") == "upper" else inner.lower()
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
    context: dict[str, str] | None = None,
) -> tuple[str | None, discord.Embed | None, discord.ui.LayoutView | None]:
    values = context or {}

    def expand(value: str) -> str:
        return expand_variables(value, args, now, values)

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
        for kind, value in body.items:
            if kind == "small":
                container.add_item(SmallSeparator())
            elif kind == "large":
                container.add_item(LargeSeparator())
            else:
                text = _cut(expand(value), budget)
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


def send_kwargs(
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
    description="Saved text snippets and custom commands for this server.",
):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.db_path = DATA_DIR / "tags.db"
        self._cooldowns: dict[tuple[int, str, int], float] = {}

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
                    args TEXT NOT NULL DEFAULT '',
                    permission TEXT NOT NULL DEFAULT 'none',
                    cooldown INTEGER NOT NULL DEFAULT 0,
                    actions TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (guild_id, name)
                )
                """,
            )
            cursor = await conn.execute("PRAGMA table_info(tags)")
            existing = {row[1] for row in await cursor.fetchall()}
            for column, definition in TAG_COLUMNS:
                if column not in existing:
                    await conn.execute(f"ALTER TABLE tags ADD COLUMN {column} {definition}")
            await conn.commit()

    async def _fetch(self, guild_id: int, name: str) -> Tag | None:
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                """
                SELECT name, content, args, permission, cooldown, actions,
                    author_id, created_at
                FROM tags WHERE guild_id = ? AND name = ?
                """,
                (guild_id, name),
            ) as cursor:
                row = await cursor.fetchone()
        return Tag(*row) if row else None

    async def lookup(self, guild_id: int, tag: str) -> str | None:
        found = await self._fetch(guild_id, tag)
        return found.content if found else None

    async def update_tag(self, guild_id: int, tag: str, content: str) -> bool:
        return await self.update_fields(guild_id, tag, {"content": content})

    async def update_fields(
        self,
        guild_id: int,
        tag: str,
        changes: dict[str, str | int],
    ) -> bool:
        assignments = ", ".join(f"{column} = ?" for column in changes)
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                f"UPDATE tags SET {assignments}, updated_at = ? WHERE guild_id = ? AND name = ?",
                (*changes.values(), time.time(), guild_id, tag),
            )
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

    def _cooldown_left(self, key: tuple[int, str, int]) -> int:
        expires = self._cooldowns.get(key)
        if expires is None:
            return 0
        left = expires - time.monotonic()
        if left <= 0:
            del self._cooldowns[key]
            return 0
        return math.ceil(left)

    async def _run_actions(
        self,
        guild: discord.Guild,
        invoker: discord.Member,
        actions: list[tuple[str, str, str]],
        values: list[str],
        now: int,
        context: dict[str, str],
    ) -> None:
        for verb, target_token, role_token in actions:
            target_id = snowflake(expand_variables(target_token, values, now, context))
            role_id = snowflake(expand_variables(role_token, values, now, context))
            if target_id is None or role_id is None:
                continue

            role = guild.get_role(role_id)
            if role is None or role.managed or role.is_default():
                continue
            if role >= guild.me.top_role:
                continue
            if invoker.id != guild.owner_id and role >= invoker.top_role:
                continue

            target = guild.get_member(target_id)
            if target is None:
                try:
                    target = await guild.fetch_member(target_id)
                except discord.HTTPException:
                    continue

            reason = f"Tag run by {invoker}"
            try:
                if verb == "addrole":
                    await target.add_roles(role, reason=reason)
                else:
                    await target.remove_roles(role, reason=reason)
            except discord.HTTPException:
                log.warning("Couldn't %s %s for %s", verb, role.id, target.id)

    async def _execute(
        self,
        guild: discord.Guild | None,
        member: discord.abc.User,
        tag: Tag,
        raw: str,
        send: Callable[..., Awaitable[object]],
    ) -> None:
        if guild is None or not isinstance(member, discord.Member):
            msg = "Tags only work in servers."
            raise CommandError(msg)

        if error := access_error(member, tag.permission):
            raise CommandError(error)

        actions = parse_actions(tag.actions) if tag.actions else []
        if actions and not (
            member.guild_permissions.administrator or member.guild_permissions.manage_roles
        ):
            msg = "You need the Manage Roles permission to use this, since it gives or takes roles."
            raise CommandError(msg)

        key = (guild.id, tag.name, member.id)
        if left := self._cooldown_left(key):
            msg = f"You can use this again in {left} second{'s' if left != 1 else ''}."
            raise CommandError(msg)

        if tag.args:
            values = await bind_args(guild, parse_args(tag.args), raw)
        else:
            values = split_args(raw) if raw.strip() else []

        now = int(time.time())
        context = {"user": member.mention, "server": guild.name}
        try:
            body = parse_tag(tag.content)
        except TagFormatError as error:
            raise CommandError(str(error)) from error

        await send(**send_kwargs(render(body, values, now, context)))

        if tag.cooldown:
            self._cooldowns[key] = time.monotonic() + tag.cooldown
        if actions:
            await self._run_actions(guild, member, actions, values, now, context)

    @app_commands.command(name="use", description="Post a saved tag.")
    @app_commands.describe(
        name="The tag to post.",
        args="Values for the tag's arguments, separated by spaces.",
    )
    async def use(
        self,
        interaction: discord.Interaction,
        name: str,
        args: str = "",
    ) -> None:
        tag = _tag_name(name)
        row = await self._fetch(interaction.guild_id, tag) if tag else None
        if row is None:
            await _respond(interaction, ErrorUI(f"**There is no tag called `{name}`.**"))
            return

        async def send(**kwargs: object) -> None:
            await interaction.response.send_message(
                **kwargs,
                allowed_mentions=NO_MENTIONS,
            )

        try:
            await self._execute(interaction.guild, interaction.user, row, args, send)
        except CommandError as error:
            await _respond(interaction, ErrorUI(f"**{error}**"))

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
        args="Arguments like amount:number:1-100 or reason?:text",
        permission="none, a permission like manage_messages, or a role",
        cooldown="Seconds each member waits between uses",
        actions="Lines like addrole $user @Role, one per line",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        content: app_commands.Range[str, 1, TAG_CONTENT_LENGTH],
        args: str = "",
        permission: str = "none",
        cooldown: app_commands.Range[int, 0, MAX_COOLDOWN] = 0,
        actions: str = "",
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

        try:
            error = check_tag(content)
            if error:
                raise CommandError(error)
            cleaned = (
                content.strip(),
                clean_args(args),
                clean_permission(permission),
                clean_cooldown(cooldown),
                clean_actions(actions),
            )
        except CommandError as error:
            await _respond(interaction, ErrorUI(f"**{error}**"))
            return

        now = time.time()
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    """
                    INSERT INTO tags (
                        guild_id, name, content, author_id, created_at, updated_at,
                        args, permission, cooldown, actions
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        interaction.guild_id,
                        tag,
                        cleaned[0],
                        interaction.user.id,
                        now,
                        now,
                        *cleaned[1:],
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

    @app_commands.command(name="edit", description="Change parts of a tag.")
    @app_commands.describe(
        name="The tag to change.",
        content="New text for the tag.",
        args="New arguments. Empty removes them.",
        permission="New permission. none for everyone.",
        cooldown="New cooldown in seconds.",
        actions="New actions, one per line. Empty removes them.",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def edit(
        self,
        interaction: discord.Interaction,
        name: str,
        content: str | None = None,
        args: str | None = None,
        permission: str | None = None,
        cooldown: int | None = None,
        actions: str | None = None,
    ) -> None:
        tag = _tag_name(name)
        changes: dict[str, str | int] = {}
        try:
            if content is not None:
                error = check_tag(content)
                if error:
                    raise CommandError(error)
                changes["content"] = content.strip()
            if args is not None:
                changes["args"] = clean_args(args)
            if permission is not None:
                changes["permission"] = clean_permission(permission)
            if cooldown is not None:
                changes["cooldown"] = clean_cooldown(cooldown)
            if actions is not None:
                changes["actions"] = clean_actions(actions)
            if not changes:
                msg = "Pass at least one thing to change."
                raise CommandError(msg)
        except CommandError as error:
            await _respond(interaction, ErrorUI(f"**{error}**"))
            return

        if tag is None or not await self.update_fields(interaction.guild_id, tag, changes):
            await _respond(interaction, ErrorUI(f"**There is no tag called `{name}`.**"))
            return
        await _respond(
            interaction,
            PositiveUI(title="Tag updated", subtitle=f"**`{tag}`** has been changed."),
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

    @app_commands.command(name="rename", description="Rename a tag.")
    @app_commands.describe(name="The tag to rename.", new_name="The new name for the tag.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def rename(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: str,
    ) -> None:
        old = _tag_name(name)
        new = _tag_name(new_name)
        if new is None:
            await _respond(
                interaction,
                ErrorUI(
                    "**Tag names use letters, numbers, hyphens, and underscores, "
                    "up to 32 characters.**",
                ),
            )
            return
        if old is None or await self.lookup(interaction.guild_id, old) is None:
            await _respond(interaction, ErrorUI(f"**There is no tag called `{name}`.**"))
            return
        if old == new:
            await _respond(
                interaction,
                ErrorUI("**The new name is the same as the old one.**"),
            )
            return

        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute(
                    "UPDATE tags SET name = ?, updated_at = ? WHERE guild_id = ? AND name = ?",
                    (new, time.time(), interaction.guild_id, old),
                )
                await conn.commit()
        except sqlite3.IntegrityError:
            await _respond(
                interaction,
                ErrorUI(f"**A tag called `{new}` already exists.**"),
            )
            return

        await _respond(
            interaction,
            PositiveUI(title="Tag renamed", subtitle=f"**`{old}`** is now **`{new}`**."),
        )

    @rename.autocomplete("name")
    async def rename_name(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self._name_autocomplete(interaction, current)

    @app_commands.command(
        name="info",
        description="See how a tag is set up and who made it.",
    )
    @app_commands.describe(name="The tag to look up.")
    async def info(self, interaction: discord.Interaction, name: str) -> None:
        tag = _tag_name(name)
        row = await self._fetch(interaction.guild_id, tag) if tag else None
        if row is None:
            await _respond(interaction, ErrorUI(f"**There is no tag called `{name}`.**"))
            return

        try:
            kind = parse_tag(row.content).kind
        except TagFormatError:
            kind = "broken"
        created = discord.utils.format_dt(
            datetime.fromtimestamp(row.created_at, UTC),
            style="F",
        )
        actions = row.actions.replace("\n", " / ") or "None"
        await _respond(
            interaction,
            InfoUI(
                title=f"Tag {row.name}",
                subtitle=(
                    f"**Author:** <@{row.author_id}>\n"
                    f"**Created:** {created}\n"
                    f"**Type:** {kind.title()}\n"
                    f"**Length:** {len(row.content):,} characters\n"
                    f"**Arguments:** {row.args or 'None'}\n"
                    f"**Permission:** {describe_permission(row.permission)}\n"
                    f"**Cooldown:** {row.cooldown} seconds\n"
                    f"**Actions:** {actions}"
                ),
            ),
        )

    @info.autocomplete("name")
    async def info_name(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self._name_autocomplete(interaction, current)

    @app_commands.command(
        name="search",
        description="Find tags whose names contain some text.",
    )
    @app_commands.describe(query="Text to look for in tag names.")
    async def search(
        self,
        interaction: discord.Interaction,
        query: app_commands.Range[str, 1, 32],
    ) -> None:
        async with aiosqlite.connect(self.db_path) as conn:
            async with conn.execute(
                "SELECT name FROM tags WHERE guild_id = ? AND instr(name, ?) > 0 ORDER BY name",
                (interaction.guild_id, query.strip().lower()),
            ) as cursor:
                names = [name for (name,) in await cursor.fetchall()]

        shown = discord.utils.escape_markdown(query)
        if not names:
            await _respond(
                interaction,
                InfoUI(title="Tag search", subtitle=f"**No tags match `{shown}`.**"),
            )
            return

        view = Paginator(
            f"### {MELVIN_EMOJI} Tags matching `{shown}`",
            names,
            data_name="Tags",
            per_page=TAGS_PER_PAGE,
            container=True,
            timeout=300,
        )
        await interaction.response.send_message(view=view)
        view.message = await interaction.original_response()

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
        if (
            message.author.bot
            or message.guild is None
            or not isinstance(message.author, discord.Member)
            or not await module_settings.is_enabled(message.guild.id, "tags")
        ):
            return

        prefix = await self._prefix(message.guild.id)
        if not message.content.startswith(prefix):
            return
        parts = message.content[len(prefix) :].split(maxsplit=1)
        tag = _tag_name(parts[0]) if parts else None
        if tag is None:
            return
        row = await self._fetch(message.guild.id, tag)
        if row is None:
            return

        async def send(**kwargs: object) -> None:
            await message.reply(
                **kwargs,
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )

        try:
            await self._execute(
                message.guild,
                message.author,
                row,
                parts[1] if len(parts) > 1 else "",
                send,
            )
        except CommandError as error:
            await message.reply(
                view=ErrorUI(f"**{error}**"),
                mention_author=False,
                allowed_mentions=NO_MENTIONS,
            )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("DELETE FROM tags WHERE guild_id = ?", (guild.id,))
                await conn.commit()
            await module_config.clear_module(guild.id, "tags")
        except Exception:
            log.exception("failed to clean up tags for departed guild %s", guild.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TagsCog(bot))
