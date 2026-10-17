# ruff: file-ignore[private-member-access]
from __future__ import annotations

# pyright: reportPrivateUsage = false
import contextlib
from collections.abc import Sequence

import discord

from .views import ErrorUI, ExceptionUI, LargeSeparator

__all__ = ["Paginator"]

type _Item = discord.ui.Item[discord.ui.LayoutView]
type _ItemsList = list[_Item]
type _ItemsOrStrList = Sequence[str | _Item]
type _TitleButton = discord.ui.Button[Paginator]


class _PageJumpModal(discord.ui.Modal, title="Jump to Page"):
    def __init__(self, paginator: Paginator) -> None:
        super().__init__()
        self.paginator = paginator
        self._page_input = discord.ui.TextInput(
            placeholder="ex: 5",
            min_length=1,
            max_length=len(str(len(paginator.pages))),
        )
        self.page_input = discord.ui.Label(
            text="Enter a page number.",
            description="Enter a positive integer greater than or equal to one.",
            component=self._page_input,
        )
        self.add_item(self.page_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        total = len(self.paginator.pages)
        try:
            page = int(self._page_input.value) - 1
        except ValueError:
            message = "Enter a positive integer greater than or equal to one."
        else:
            if page == self.paginator.current_page:
                message = "You are already viewing this page."
            elif 0 <= page < total:
                await self.paginator._turn(interaction, page)
                return
            else:
                message = f"Enter a page between 1 and {total}."

        await interaction.response.send_message(view=ErrorUI(message), ephemeral=True)


class _PageRow(discord.ui.ActionRow["Paginator"]):
    def __init__(self, paginator: Paginator) -> None:
        super().__init__()
        self.paginator = paginator

        total = len(paginator.pages)
        if total == 2:
            self.remove_item(self.btn_first)
            self.remove_item(self.btn_last)
        if total <= 3:
            self.remove_item(self.btn_page)

        self.update_states()

    def update_states(self) -> None:
        current = self.paginator.current_page
        total = len(self.paginator.pages)
        is_first = current == 0
        is_last = current == total - 1

        self.btn_first.disabled = self.btn_backward.disabled = is_first
        self.btn_last.disabled = self.btn_forward.disabled = is_last
        self.btn_page.label = f"{current + 1} / {total}"

    @discord.ui.button(label="<<")
    async def btn_first(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[discord.ui.LayoutView],
    ) -> None:
        await self.paginator._turn(interaction, 0)

    @discord.ui.button(label="<")
    async def btn_backward(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[discord.ui.LayoutView],
    ) -> None:
        await self.paginator._turn(interaction, self.paginator.current_page - 1)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.green)
    async def btn_page(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[discord.ui.LayoutView],
    ) -> None:
        await interaction.response.send_modal(_PageJumpModal(self.paginator))

    @discord.ui.button(label=">")
    async def btn_forward(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[discord.ui.LayoutView],
    ) -> None:
        await self.paginator._turn(interaction, self.paginator.current_page + 1)

    @discord.ui.button(label=">>")
    async def btn_last(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[discord.ui.LayoutView],
    ) -> None:
        await self.paginator._turn(interaction, len(self.paginator.pages) - 1)


class Paginator(discord.ui.LayoutView):
    def __init__(
        self,
        title: str,
        data: _ItemsOrStrList,
        /,
        *,
        data_name: str,
        per_page: int = 5,
        color: discord.Color | None = None,
        container: bool = False,
        force: bool = False,
        timeout: int | None = 600,
    ) -> None:
        if color and not container:
            error = "color is dependent on container"
            raise ValueError(error)

        super().__init__(timeout=timeout)
        self.message: discord.Message | None = None

        self._data_name = data_name
        self._per_page = per_page
        self._color = color
        self._container = container
        self._force = force

        self._above_items: _ItemsList = []
        self._over_items: _ItemsList = []
        self._under_items: _ItemsList = []
        self._below_items: _ItemsList = []
        self._title_button: _TitleButton | None = None

        self._set_data(title, data)

    def _set_data(self, title: str, data: _ItemsOrStrList) -> None:
        self._title = title
        self._data = data
        self.current_page = 0
        self.pages: list[_ItemsOrStrList] = [
            data[i : i + self._per_page] for i in range(0, len(data), self._per_page)
        ] or [["No content available."]]
        self._page_row = _PageRow(self) if len(self.pages) >= 2 else None
        self._render()

    async def on_timeout(self) -> None:
        for item in self.walk_children():
            if isinstance(item, discord.ui.Button | discord.ui.Select):
                item.disabled = True

        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass
            except discord.HTTPException:
                with contextlib.suppress(discord.HTTPException):
                    await self.message.delete()

    def add_above(self, *items: _Item) -> None:
        self._above_items.extend(items)
        self._render()

    def add_over(self, *items: _Item) -> None:
        self._over_items.extend(items)
        self._render()

    def add_under(self, *items: _Item) -> None:
        self._under_items.extend(items)
        self._render()

    def add_below(self, *items: _Item) -> None:
        self._below_items.extend(items)
        self._render()

    def set_title_button(self, button: _TitleButton | None, /) -> None:
        self._title_button = button
        self._render()

    def update_data(
        self,
        title: str,
        data: _ItemsOrStrList,
        *,
        keep_page: bool = False,
    ) -> None:
        page = self.current_page
        self._set_data(title, data)
        if keep_page:
            self.current_page = min(page, len(self.pages) - 1)
            self._render()

    def _page_items(self) -> _ItemsList:
        page = self.pages[self.current_page]
        if self._force:
            return [
                discord.ui.TextDisplay(item) if isinstance(item, str) else item
                for item in page
            ]

        items: _ItemsList = []
        text: list[str] = []
        for item in page:
            if isinstance(item, str):
                text.append(item)
                continue
            if text:
                items.append(discord.ui.TextDisplay("\n".join(text)))
                text = []
            items.append(item)
        if text:
            items.append(discord.ui.TextDisplay("\n".join(text)))
        return items

    def _render(self) -> None:
        self.clear_items()

        title = (
            discord.ui.Section(self._title, accessory=self._title_button)
            if self._title_button is not None
            else discord.ui.TextDisplay(self._title)
        )
        items: _ItemsList = [
            *self._over_items,
            title,
            LargeSeparator(),
            *self._page_items(),
            LargeSeparator(),
            discord.ui.TextDisplay(
                f"-# Page {self.current_page + 1} of {len(self.pages)} | {len(self._data)} {self._data_name}",
            ),
        ]
        if self._page_row:
            self._page_row.update_states()
            items.append(self._page_row)
        items.extend(self._under_items)

        if self._container:
            items = [discord.ui.Container(*items, accent_color=self._color)]

        for item in (*self._above_items, *items, *self._below_items):
            self.add_item(item)

    async def _turn(self, interaction: discord.Interaction, target: int) -> None:
        if not 0 <= target < len(self.pages):
            return

        previous = self.current_page
        self.current_page = target
        self._render()

        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            self.current_page = previous
            self._render()
            await interaction.response.send_message(view=ExceptionUI(), ephemeral=True)
            raise
