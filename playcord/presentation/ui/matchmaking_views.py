"""Matchmaking lobby LayoutView (Join/Leave/Ready + optional selects)."""

from __future__ import annotations

import discord
from discord import SelectOption

from playcord.infrastructure.constants import (
    BUTTON_PREFIX_LOBBY_OPT,
    BUTTON_PREFIX_LOBBY_ROLE,
)
from playcord.infrastructure.locale import get
from playcord.presentation.ui.containers import TEXT_DISPLAY_MAX


class MatchmakingLobbyView(discord.ui.LayoutView):
    """Join / leave / optional Ready, optional string selects for per-game lobby settings (creator-only),
    and optional per-player role selects for games with CHOSEN role mode. (Game starts when all humans ready.).
    """

    async def _route_to_cog(self, interaction: discord.Interaction) -> None:
        """Persistent components are handled in MatchmakingCog.on_interaction."""

    def __init__(
        self,
        join_button_id: str,
        leave_button_id: str,
        ready_button_id: str | None,
        ready_button_label: str,
        lobby_message_id: int,
        option_specs: tuple = (),
        current_values: dict[str, str | int] | None = None,
        role_specs: list[tuple[int, str, tuple[str, ...]]] | None = None,
        current_role_values: dict[int, str] | None = None,
        assign_roles_button_id: str | None = None,
        summary_text: str | None = None,
        table_image_url: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        current_values = dict(current_values) if current_values else {}
        current_role_values = dict(current_role_values) if current_role_values else {}
        role_specs = role_specs or []

        container = discord.ui.Container()
        if summary_text:
            container.add_item(discord.ui.TextDisplay(summary_text[:TEXT_DISPLAY_MAX]))
        if table_image_url:
            if summary_text:
                container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem(table_image_url),
                ),
            )
        if summary_text or table_image_url:
            container.add_item(discord.ui.Separator())

        action_row = discord.ui.ActionRow()
        join_btn = discord.ui.Button(
            label=get("buttons.join"),
            style=discord.ButtonStyle.gray,
            custom_id=join_button_id,
        )
        join_btn.callback = self._route_to_cog
        action_row.add_item(join_btn)

        leave_btn = discord.ui.Button(
            label=get("buttons.leave"),
            style=discord.ButtonStyle.gray,
            custom_id=leave_button_id,
        )
        leave_btn.callback = self._route_to_cog
        action_row.add_item(leave_btn)

        if ready_button_id is not None:
            ready_btn = discord.ui.Button(
                label=ready_button_label,
                style=discord.ButtonStyle.success,
                custom_id=ready_button_id,
            )
            ready_btn.callback = self._route_to_cog
            action_row.add_item(ready_btn)

        if assign_roles_button_id is not None:
            assign_btn = discord.ui.Button(
                label=get("buttons.assign_roles", default="Assign Roles"),
                style=discord.ButtonStyle.primary,
                custom_id=assign_roles_button_id,
            )
            assign_btn.callback = self._route_to_cog
            action_row.add_item(assign_btn)

        container.add_item(action_row)

        if option_specs:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("### Match Options"))

        for spec in option_specs:
            cur = current_values.get(spec.key, spec.default)
            options: list[SelectOption] = []
            for label, value, _is_def in spec.select_options():
                options.append(
                    SelectOption(
                        label=label[:100],
                        value=value[:100],
                        default=(str(value) == str(cur)),
                    ),
                )
            sel = discord.ui.Select(
                custom_id=f"{BUTTON_PREFIX_LOBBY_OPT}{lobby_message_id}/{spec.key}",
                placeholder=spec.label[:150],
                min_values=1,
                max_values=1,
                options=options,
            )
            sel.callback = self._route_to_cog
            option_row = discord.ui.ActionRow()
            option_row.add_item(sel)
            container.add_item(option_row)

        if role_specs:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay("### Role Selection"))
        for player_id, display_name, avail_roles in role_specs:
            cur = current_role_values.get(player_id)
            roptions: list[SelectOption] = []
            for r in avail_roles:
                rv = str(r)[:100]
                roptions.append(
                    SelectOption(
                        label=str(r).replace("_", " ").title()[:100],
                        value=rv,
                        default=(cur is not None and str(cur) == str(r)),
                    ),
                )
            placeholder = f"{display_name[:80]}: role"
            rsel = discord.ui.Select(
                custom_id=f"{BUTTON_PREFIX_LOBBY_ROLE}{lobby_message_id}/{player_id}",
                placeholder=placeholder[:150],
                min_values=1,
                max_values=1,
                options=roptions,
            )
            rsel.callback = self._route_to_cog
            role_row = discord.ui.ActionRow()
            role_row.add_item(rsel)
            container.add_item(role_row)

        self.add_item(container)
