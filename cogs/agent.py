import asyncio
import logging
import os
import time
import warnings
from datetime import UTC, datetime

import aiosqlite
import discord
from ddgs import DDGS
from discord import app_commands
from discord.ext import commands
from groq import AsyncGroq

from globals import DATA_DIR, MELVIN_EMOJI
from module_registry import Module
from ui import ErrorUI, Paginator

MODULE = Module(
    "ai",
    "AI",
    "Ask a free AI model questions.",
)

warnings.filterwarnings("ignore", category=DeprecationWarning)

log = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "ai_usage.db"
RATE_LIMIT = 10
RATE_PERIOD = 60 * 60
GROQ_MODEL = "openai/gpt-oss-20b"
PAGE_SIZE = 2000

SYSTEM_PROMPT = (
    "Today's date is {date}. "
    "Try to keep responses tidy and brief, unless otherwise requested by the user. "
    "Contain responses in short, yet informative paragraphs, rather than graphs or tables or bulletpoints. "
    "Refrain from using emojis unless told to. "
)
SEARCH_PROMPT = "Use the provided search context to ground your answer relative to today's date. "


def paginate(text: str, size: int = PAGE_SIZE) -> list[str]:
    pages: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if len(candidate) > size:
            if current:
                pages.append(current)
            current = word
        else:
            current = candidate
    if current:
        pages.append(current)
    return pages or [""]


def _web_context(query: str, max_results: int = 5) -> str:
    try:
        results = list(DDGS().text(query, max_results=max_results))
    except Exception:
        return "Could not fetch search context."
    if not results:
        return "No search context available."
    return "\n---\n".join(
        f"Source {i}:\nTitle: {r.get('title')}\nURL: {r.get('href')}\nSnippet: {r.get('body')}\n"
        for i, r in enumerate(results, 1)
    )


class AgentCog(
    commands.GroupCog,
    name="ai",
    description="Self explanatory, ask a free AI model some stupid stuff.",
):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.client = AsyncGroq(api_key=os.getenv("GROQ"))

    async def cog_load(self) -> None:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_requests (
                    user_id INTEGER NOT NULL,
                    timestamp INTEGER NOT NULL
                )
                """,
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ai_requests_user_ts "
                "ON ai_requests (user_id, timestamp)",
            )
            await db.commit()

    async def _check_rate_limit(self, user_id: int) -> tuple[bool, int, int]:
        now = int(time.time())
        async with (
            aiosqlite.connect(DB_PATH) as db,
            db.execute(
                "SELECT COUNT(*), MIN(timestamp) FROM ai_requests "
                "WHERE user_id = ? AND timestamp > ?",
                (user_id, now - RATE_PERIOD),
            ) as cursor,
        ):
            count, oldest = await cursor.fetchone()

        if count < RATE_LIMIT:
            return True, count, 0
        return False, count, max(0, oldest + RATE_PERIOD - now)

    async def _record_request(self, user_id: int) -> None:
        now = int(time.time())
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO ai_requests (user_id, timestamp) VALUES (?, ?)",
                (user_id, now),
            )
            await db.execute(
                "DELETE FROM ai_requests WHERE timestamp <= ?",
                (now - RATE_PERIOD,),
            )
            await db.commit()

    async def cog_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        view = ErrorUI(
            "**You are being rate limited.**"
            if isinstance(error, app_commands.CommandOnCooldown)
            else "**Something went wrong.**",
        )
        if interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)

    async def query_groq(self, prompt: str, *, use_search: bool = False) -> str:
        date = datetime.now(UTC).strftime("%B %d, %Y")
        system = SYSTEM_PROMPT.format(date=date)
        context = ""
        if use_search:
            system += SEARCH_PROMPT
            search = await asyncio.to_thread(_web_context, prompt)
            context = f"--- SEARCH CONTEXT ---\n{search}\n--- END CONTEXT ---\n"

        try:
            response = await self.client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": f"--- CURRENT DATE: {date} ---\n{context}\nUser Question: {prompt}",
                    },
                ],
                temperature=0.7,
            )
            text = response.choices[0].message.content
        except Exception as e:
            error = f"**Groq API Error: {e!s}.**"
            raise RuntimeError(error) from e

        if not text:
            raise RuntimeError("**Groq returned an empty response.**")
        return text

    @app_commands.command(
        name="ask",
        description="Ask a free AI model some stupid stuff.",
    )
    @app_commands.describe(
        prompt="The question or prompt to ask the AI model.",
        search="Whether to perform a web search for grounding context.",
    )
    @app_commands.checks.cooldown(2, 60)
    async def ask(
        self,
        interaction: discord.Interaction,
        prompt: str,
        *,
        search: bool = False,
    ) -> None:
        await interaction.response.defer()

        allowed, used, retry_after = await self._check_rate_limit(interaction.user.id)
        if not allowed:
            minutes = max(1, retry_after // 60)
            await interaction.edit_original_response(
                view=ErrorUI(
                    f"**You've hit the limit of {RATE_LIMIT} requests per hour, try again in about {minutes} minute(s).**",
                ),
            )
            return

        try:
            start = time.perf_counter()
            ai_response = await self.query_groq(prompt, use_search=search)
            elapsed = time.perf_counter() - start
            await self._record_request(interaction.user.id)

            view = Paginator(
                f"# Prompt: `{prompt.replace('`', '')}`",
                paginate(ai_response),
                data_name="Pages",
                per_page=1,
                container=True,
                timeout=300,
            )
            view.set_title_button(
                discord.ui.Button(
                    label="Model",
                    style=discord.ButtonStyle.link,
                    url=f"https://huggingface.co/{GROQ_MODEL}",
                ),
            )
            grounding = (
                "Grounded using DDGS web search context"
                if search
                else "Generated without web search"
            )
            remaining = max(0, RATE_LIMIT - used - 1)
            view.add_under(
                discord.ui.TextDisplay(
                    f"-# {MELVIN_EMOJI} **Took {elapsed:.1f}s. {remaining}/{RATE_LIMIT} requests left this hour.**\n"
                    f"-# **{grounding} using {GROQ_MODEL}.**",
                ),
            )
            view.message = await interaction.edit_original_response(view=view)
        except Exception:
            log.exception("Failure in agent command")
            await interaction.edit_original_response(
                view=ErrorUI("**Something went wrong. This is likely an API error.**"),
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AgentCog(bot))
