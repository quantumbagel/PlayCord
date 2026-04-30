import os
import random
from collections.abc import Iterable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import discord

from playcord.infrastructure.constants import (
    ERROR_COLOR,
    MATCHMAKING_COLOR,
    SUCCESS_COLOR,
    WARNING_COLOR,
)
from playcord.infrastructure.locale import fmt, get, get_dict
from playcord.presentation.interactions.contextify import contextify
from playcord.presentation.ui.emojis import get_emoji_string
from playcord.presentation.ui.exceptions import ContainerValidationError
from playcord.presentation.ui.formatting import (
    column_elo,
    column_names,
    column_turn,
)

_TEXT_DISPLAY_MAX = 4000
# Discord TextDisplay / message content limit
# (public alias for callers outside this module)
TEXT_DISPLAY_MAX = _TEXT_DISPLAY_MAX
_FIELD_VALUE_MAX = 1024
_FIELD_LINE_SAFE_MAX = 500
# Embed field value max minus small safety margin for markdown/formatting overhead
_FIELD_VALUE_SAFE = _FIELD_VALUE_MAX - 7

# Discord's maximum embed fields
MAX_EMBED_FIELDS = 25


def _chunk_text(text: str, *, max_len: int = _TEXT_DISPLAY_MAX) -> list[str]:
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_len and current:
            chunks.append(current.rstrip("\n"))
            current = line
        else:
            current += line
    if current:
        chunks.append(current.rstrip("\n"))
    return chunks or [text[:max_len]]


def chunk_text_display_lines(
    text: str,
    *,
    max_len: int = TEXT_DISPLAY_MAX,
) -> list[str]:
    """Split content into Discord TextDisplay-sized chunks (newline-aware)."""
    return _chunk_text(text, max_len=max_len)


def _build_container_view(
    body_text: str,
    *,
    accent_color: discord.Color | int | None = None,
    media_urls: Iterable[str] | None = None,
    thumbnail_url: str | None = None,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_color=accent_color)
    body_text = (body_text or "").strip()
    if body_text:
        for chunk in _chunk_text(body_text):
            container.add_item(discord.ui.TextDisplay(chunk))
    urls = [u for u in (media_urls or []) if u]
    if thumbnail_url:
        urls.insert(0, thumbnail_url)
    if urls:
        if body_text:
            container.add_item(discord.ui.Separator())
        items = [discord.MediaGalleryItem(url) for url in urls]
        container.add_item(discord.ui.MediaGallery(*items))
    view.add_item(container)
    return view


def container_to_markdown(card: "CustomContainer | str | None") -> str:
    if card is None:
        return ""
    if isinstance(card, str):
        return card.strip()
    to_markdown = getattr(card, "to_markdown", None)
    if callable(to_markdown):
        return str(to_markdown()).strip()
    return str(card).strip()


def container_send_kwargs(
    card: "CustomContainer | str",
    *,
    files: list[discord.File] | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    if isinstance(card, CustomContainer):
        body = card.to_markdown()
        accent = card.color
        media = card.media_urls()
        thumbnail = card.thumbnail_url
    else:
        body = container_to_markdown(card)
        accent = None
        media = []
        thumbnail = None
    kwargs: dict[str, Any] = {
        "view": _build_container_view(
            body,
            accent_color=accent,
            media_urls=media,
            thumbnail_url=thumbnail,
        ),
    }
    if files:
        kwargs["files"] = files
    if content is not None:
        kwargs["content"] = content
    return kwargs


def container_edit_kwargs(
    card: "CustomContainer | str",
    *,
    attachments: list[discord.File] | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    if isinstance(card, CustomContainer):
        body = card.to_markdown()
        accent = card.color
        media = card.media_urls()
        thumbnail = card.thumbnail_url
    else:
        body = container_to_markdown(card)
        accent = None
        media = []
        thumbnail = None
    kwargs: dict[str, Any] = {
        "view": _build_container_view(
            body,
            accent_color=accent,
            media_urls=media,
            thumbnail_url=thumbnail,
        ),
    }
    if attachments is not None:
        kwargs["attachments"] = attachments
    if content is not None:
        kwargs["content"] = content
    return kwargs


def lines_to_container_sections(
    lines: list[str],
    *,
    value_max: int = _FIELD_VALUE_MAX,
    line_max: int = _FIELD_LINE_SAFE_MAX,
) -> list[str]:
    safe: list[str] = []
    for ln in lines:
        if len(ln) <= line_max:
            safe.append(ln)
        else:
            safe.append(ln[: line_max - 1] + "…")

    chunks: list[str] = []
    bucket: list[str] = []
    size = 0
    for line in safe:
        add = len(line) + (1 if bucket else 0)
        if bucket and size + add > value_max:
            chunks.append("\n".join(bucket))
            bucket = [line]
            size = len(line)
        else:
            bucket.append(line)
            size += add
    if bucket:
        chunks.append("\n".join(bucket))
    return chunks


def append_container_sections(
    card: "CustomContainer",
    chunks: list[str],
    *,
    first_name: str,
    more_name: str = "\u200b",
    truncated_note: str | None = None,
    max_fields: int = 24,
) -> None:
    vm = _FIELD_VALUE_MAX
    for i, chunk in enumerate(chunks):
        if len(card.fields) >= max_fields:
            if truncated_note:
                card.add_field(name="\u200b", value=truncated_note, inline=False)
            return
        name = (first_name if i == 0 else more_name)[:256]
        val = chunk if len(chunk) <= vm else chunk[: vm - 1] + "…"
        card.add_field(name=name, value=val or "\u200b", inline=False)


@dataclass(slots=True)
class ContainerField:
    name: str
    value: str
    inline: bool = True


class CustomContainer:
    def __init__(self, **kwargs) -> None:
        self.title: str | None = kwargs.get("title")
        self.description: str | None = kwargs.get("description")
        self.color: discord.Color | int | None = kwargs.get("color")
        self.fields: list[ContainerField] = []
        self.footer_text: str | None = None
        self.footer_icon_url: str | None = None
        self.image_url: str | None = None
        self.thumbnail_url: str | None = None

    @property
    def footer(self):
        if self.footer_text is None:
            return None
        return SimpleNamespace(text=self.footer_text, icon_url=self.footer_icon_url)

    def remove_footer(self):
        self.footer_text = None
        self.footer_icon_url = None
        return self

    def add_field(self, *, name: str, value: Any, inline: bool = True):
        if len(self.fields) >= MAX_EMBED_FIELDS:
            msg = (
                f"Cannot add field: container already has {MAX_EMBED_FIELDS} fields (Discord's limit). "
                f"Field name: {name[:50]}"
            )
            raise ContainerValidationError(
                msg,
            )
        self.fields.append(ContainerField(str(name), str(value), inline))
        return self

    def set_footer(self, *, text: str | None = None, icon_url: str | None = None):
        self.footer_text = text
        self.footer_icon_url = icon_url
        return self

    def set_image(self, *, url: str):
        self.image_url = url
        return self

    def set_thumbnail(self, *, url: str):
        self.thumbnail_url = url
        return self

    def media_urls(self) -> list[str]:
        out: list[str] = []
        if self.image_url:
            out.append(self.image_url)
        return out

    def validate(self) -> None:
        """Validate container doesn't exceed Discord's limits."""
        if len(self.fields) > MAX_EMBED_FIELDS:
            msg = f"Container has {len(self.fields)} fields, exceeds limit of {MAX_EMBED_FIELDS}"
            raise ContainerValidationError(
                msg,
            )

    def to_markdown(self) -> str:
        parts: list[str] = []
        if self.title:
            parts.append(f"## {self.title}")
        if self.description:
            parts.append(str(self.description))
        for field in self.fields:
            parts.append(f"**{field.name}**\n{field.value}")
        if self.footer_text:
            parts.append(f"_{self.footer_text}_")
        return "\n\n".join(p for p in parts if p).strip()

    def to_send_kwargs(
        self,
        *,
        files: list[discord.File] | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        return container_send_kwargs(self, files=files, content=content)

    def to_edit_kwargs(
        self,
        *,
        attachments: list[discord.File] | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        return container_edit_kwargs(self, attachments=attachments, content=content)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.title is not None:
            result["title"] = self.title
        if self.description is not None:
            result["description"] = self.description
        if self.footer_text is not None:
            result["footer"] = {"text": self.footer_text}
        if self.fields:
            result["fields"] = [
                {"name": f.name, "value": f.value, "inline": f.inline}
                for f in self.fields
            ]
        return result


class SuccessContainer(CustomContainer):
    def __init__(
        self, title: str | None = None, description: str | None = None, **kwargs,
    ) -> None:
        kwargs["color"] = SUCCESS_COLOR
        kwargs["title"] = f"✅ {title or get('success.default_title')}"
        if description:
            kwargs["description"] = description
        super().__init__(**kwargs)


class WarningContainer(CustomContainer):
    def __init__(
        self, title: str | None = None, description: str | None = None, **kwargs,
    ) -> None:
        kwargs["color"] = WARNING_COLOR
        kwargs["title"] = f"⚠️ {title or get('warnings.default_title')}"
        if description:
            kwargs["description"] = description
        super().__init__(**kwargs)


class UserErrorContainer(CustomContainer):
    def __init__(
        self, description: str | None = None, suggestion: str | None = None, **kwargs,
    ) -> None:
        kwargs["color"] = ERROR_COLOR
        super().__init__(**kwargs)
        if description:
            self.description = description
        if suggestion:
            self.add_field(
                name=get("embeds.user_error.suggestion_field"),
                value=suggestion,
                inline=False,
            )


class LoadingContainer(CustomContainer):
    def __init__(self, message: str | None = None, **kwargs) -> None:
        kwargs["title"] = f"{message or get('loading.default')}"
        # Get random tagline from the list
        loading_dict = get_dict("loading")
        taglines = loading_dict.get("description_options", [])
        if taglines:
            description = random.choice(taglines)
        else:
            description = "{no_taglines}"  # Make it clear it's missing data
        kwargs["description"] = description
        super().__init__(**kwargs)


class ErrorContainer(CustomContainer):
    def __init__(self, ctx=None, what_failed=None, reason=None) -> None:
        current_directory = os.path.dirname(__file__).rstrip("utils")
        super().__init__(
            title=f"{get_emoji_string('facepalm')} {get('system_error.title')}",
            color=ERROR_COLOR,
        )
        self.add_field(
            name=f"{get_emoji_string('github')} {get('system_error.report_field')}",
            value=fmt(
                "system_error.report_value",
                github_issues_url=get("brand.github_url") + "/issues",
            ),
            inline=False,
        )
        if ctx is not None:
            self.add_field(
                name=f"{get_emoji_string('clueless')} {get('system_error.context_field')}",
                value=f"```{contextify(ctx)}```",
                inline=False,
            )
        if what_failed is not None:
            self.add_field(
                name=f"{get_emoji_string('explosion')} {get('system_error.what_failed_field')}",
                value=f"```{what_failed}```",
                inline=False,
            )
        if reason is not None:
            reason = reason.replace(current_directory, "")
            text_fields = lines_to_container_sections(
                reason.split("\n"),
                value_max=_FIELD_VALUE_SAFE,
                line_max=_FIELD_LINE_SAFE_MAX,
            )
            for i, section in enumerate(text_fields):
                self.add_field(
                    name=f"{get_emoji_string('hmm')} {fmt('system_error.reason_field', part=i + 1, total=len(text_fields))}",
                    value=f"```{section}```",
                    inline=False,
                )
        self.set_footer(text=get("system_error.footer"))


class GameOverviewContainer(CustomContainer):
    def __init__(self, game_name, game_type, rated, players, turn) -> None:
        """Embed overview; ``turn`` is one player, several eligible players, or None."""
        title_key = (
            "embeds.game_overview.title_rated"
            if rated
            else "embeds.game_overview.title_unrated"
        )
        super().__init__(
            title=fmt(title_key, game_name=game_name),
            description=get("embeds.game_overview.description"),
        )
        self.add_field(
            name=get("embeds.game_overview.field_players"),
            value=column_names(players),
            inline=True,
        )
        self.add_field(
            name=get("embeds.game_overview.field_ratings"),
            value=column_elo(players, game_type),
            inline=True,
        )
        self.add_field(
            name=get("embeds.game_overview.field_turn"),
            value=column_turn(players, turn),
            inline=True,
        )


def _outcome_summaries_value(players, summaries: dict[int, str]) -> str:
    lines: list[str] = []
    for p in players:
        text = summaries.get(p.id)
        if text:
            lines.append(f"{p.mention} — {text}")
    return "\n".join(lines)


class GameOverContainer(CustomContainer):
    def __init__(
        self,
        rankings,
        game_name,
        players=None,
        outcome_summaries: dict[int, str] | None = None,
        outcome_global_summary: str | None = None,
        replay_id: str | int | None = None,
        forfeited_player_ids: set[int] | None = None,
    ) -> None:
        super().__init__(
            title=fmt("embeds.game_over.title", game_name=game_name),
            description=get("embeds.game_over.description"),
        )
        if outcome_global_summary:
            self.add_field(
                name=get("embeds.game_over.field_global_summary"),
                value=outcome_global_summary[:_FIELD_VALUE_MAX],
                inline=False,
            )
        self.add_field(
            name=get("embeds.game_over.field_rankings"),
            value=rankings,
            inline=True,
        )
        if outcome_summaries and players:
            block = _outcome_summaries_value(players, outcome_summaries)
            if block:
                self.add_field(
                    name=get("embeds.game_over.field_summary"),
                    value=block,
                    inline=False,
                )

        # Show forfeit notice if applicable
        if forfeited_player_ids and players:
            forfeited_mentions = [
                p.mention for p in players if p.id in forfeited_player_ids
            ]
            if forfeited_mentions:
                self.add_field(
                    name=get("embeds.game_over.field_forfeits"),
                    value=", ".join(forfeited_mentions),
                    inline=False,
                )

        # Add replay ID footer
        if replay_id:
            footer_text = fmt("embeds.game_over.footer_with_id", replay_id=replay_id)
            self.set_footer(text=footer_text)


class MatchmakingContainer(CustomContainer):
    def __init__(
        self,
        game_name: str,
        game_id: str,
        creator,
        players: list,
        min_players: int,
        max_players: int,
        rated: bool = True,
        private: bool = False,
    ) -> None:
        status = (
            get("embeds.matchmaking.status_private")
            if private
            else get("embeds.matchmaking.status_public")
        )
        rating_status = (
            get("embeds.matchmaking.rated")
            if rated
            else get("embeds.matchmaking.unrated")
        )
        super().__init__(
            title=fmt("embeds.matchmaking.title", game_name=game_name),
            description=fmt(
                "embeds.matchmaking.description",
                creator=creator.display_name,
            ),
            color=MATCHMAKING_COLOR,
        )
        if players:
            player_list = "\n".join(
                [
                    f"• {getattr(p, 'display_name', None) or getattr(p, 'name', str(p))}"
                    for p in players
                ],
            )
        else:
            player_list = get("embeds.matchmaking.no_players")
        self.add_field(
            name=fmt(
                "embeds.matchmaking.field_players",
                current=len(players),
                max=max_players,
            ),
            value=player_list,
            inline=True,
        )
        self.add_field(
            name=get("embeds.matchmaking.field_game_info"),
            value=fmt(
                "embeds.matchmaking.game_info_value",
                status=status,
                rating_status=rating_status,
                min=min_players,
                max=max_players,
            ),
            inline=True,
        )
        if len(players) >= min_players:
            self.add_field(
                name=get("embeds.matchmaking.field_ready"),
                value=get("embeds.matchmaking.ready_value"),
                inline=False,
            )
        else:
            needed = min_players - len(players)
            plural_s = "s" if needed > 1 else ""
            self.add_field(
                name=fmt("embeds.matchmaking.field_waiting", needed=needed, s=plural_s),
                value=get("embeds.matchmaking.waiting_value"),
                inline=False,
            )
