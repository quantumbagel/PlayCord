"""Centralized interaction and runtime error reporting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

import discord
from discord import app_commands
from discord.app_commands import CheckFailure

from playcord.application.errors import ApplicationError, ForbiddenError, NotFoundError
from playcord.application.runtime_context import try_get_container
from playcord.application.services.match_interrupt import interrupt_match
from playcord.core.errors import DomainError
from playcord.infrastructure.constants import (
    BUTTON_PREFIX_GAME_MOVE,
    BUTTON_PREFIX_GAME_SELECT,
    BUTTON_PREFIX_PEEK,
    BUTTON_PREFIX_SPECTATE,
    EPHEMERAL_DELETE_AFTER,
)
from playcord.infrastructure.database.implementation.core.exceptions import (
    DatabaseConnectionError,
)
from playcord.infrastructure.locale import Translator
from playcord.infrastructure.locale import get as locale_get
from playcord.infrastructure.logging import get_logger
from playcord.presentation.interactions.helpers import (
    followup_send,
    response_send_message,
)
from playcord.presentation.interactions.respond import CustomId
from playcord.presentation.ui.containers import (
    ErrorContainer,
    UserErrorContainer,
    container_edit_kwargs,
    container_send_kwargs,
)

if TYPE_CHECKING:
    from logging import Logger

    from playcord.application.services.game_manager import GameManager

log = get_logger("presentation.error_reporter")


def _games_by_thread(
    interaction: discord.Interaction | None,
) -> dict[Any, Any]:
    if interaction is not None:
        container = getattr(getattr(interaction, "client", None), "container", None)
        if container is not None:
            return container.registry.games_by_thread_id
    bound = try_get_container()
    if bound is not None:
        return bound.registry.games_by_thread_id
    return {}


@dataclass(frozen=True, slots=True)
class ErrorMapping:
    exception_type: type[BaseException]
    locale_key: str
    title: str


class ErrorSurface(StrEnum):
    SLASH = "slash"
    COMPONENT = "component"
    MOVE = "move"
    RUNTIME = "runtime"


ERROR_MAPPINGS = (
    ErrorMapping(DatabaseConnectionError, "errors.database_error", "Database Error"),
    ErrorMapping(ForbiddenError, "errors.generic", "Permission Denied"),
    ErrorMapping(NotFoundError, "errors.generic", "Not Found"),
    ErrorMapping(DomainError, "errors.generic", "Invalid Action"),
    ErrorMapping(ApplicationError, "errors.generic", "Application Error"),
)

_CUSTOM_ID_GAME_PREFIXES = (
    BUTTON_PREFIX_GAME_MOVE,
    BUTTON_PREFIX_GAME_SELECT,
    "game:",
    BUTTON_PREFIX_SPECTATE,
    BUTTON_PREFIX_PEEK,
)


class _EditableMessage(Protocol):
    async def edit(self, **kwargs: Any) -> Any: ...


class _SendableChannel(Protocol):
    async def send(self, **kwargs: Any) -> Any: ...


class _StatusMessage(_EditableMessage, Protocol):
    channel: _SendableChannel | None


def _unwrap_error(error: BaseException) -> BaseException:
    if (
        isinstance(error, app_commands.CommandInvokeError)
        and error.original is not None
    ):
        return error.original
    return error


def _translator_get(
    translator: Translator | None,
    key: str,
    default: str,
) -> str:
    if translator is None:
        return locale_get(key, default)
    return translator.get(key, default)


def _contextify_what_failed(error: BaseException, surface: ErrorSurface) -> str:
    if hasattr(error, "__module__") and "discord" in error.__module__:
        return f"Discord API ({surface.value}): {type(error).__name__}"
    return f"Game Engine ({surface.value}): {type(error).__name__}"


def _append_trace_footer(card: ErrorContainer, trace_id: str) -> ErrorContainer:
    footer = locale_get("system_error.footer", "")
    card.set_footer(text=f"{footer} Trace ID: {trace_id}")
    return card


def _mapped_card(
    mapping: ErrorMapping,
    *,
    translator: Translator | None,
) -> UserErrorContainer:
    return UserErrorContainer(
        title=mapping.title,
        description=_translator_get(
            translator,
            mapping.locale_key,
            "[errors.generic]",
        ),
    )


def build_error_card(
    error: BaseException,
    *,
    surface: ErrorSurface,
    trace_id: str,
    interaction: discord.Interaction | None = None,
) -> ErrorContainer:
    card = ErrorContainer(
        ctx=interaction,
        what_failed=_contextify_what_failed(error, surface),
        reason=str(error),
    )
    return _append_trace_footer(card, trace_id)


async def send_interaction_card(
    interaction: discord.Interaction,
    card: ErrorContainer | UserErrorContainer,
    *,
    ephemeral: bool = True,
    delete_after: float | None = EPHEMERAL_DELETE_AFTER,
) -> Any:
    kwargs = {**container_send_kwargs(card), "ephemeral": ephemeral}
    if interaction.response.is_done():
        return await followup_send(interaction, **kwargs, delete_after=delete_after)
    return await response_send_message(interaction, **kwargs, delete_after=delete_after)


async def notify_error_destinations(
    card: ErrorContainer | UserErrorContainer,
    *,
    logger: Logger | None = None,
    game_message: _EditableMessage | None = None,
    thread: _SendableChannel | None = None,
    status_message: _StatusMessage | None = None,
) -> bool:
    logger = logger or log

    try:
        if game_message is not None:
            await game_message.edit(**container_edit_kwargs(card, attachments=None))
            return True
    except Exception:
        logger.exception("Failed to edit game message with error container")

    try:
        if thread is not None:
            await thread.send(**container_send_kwargs(card))
            return True
    except Exception:
        logger.exception("Failed to send error container to thread")

    try:
        channel = getattr(status_message, "channel", None)
        if channel is not None:
            await channel.send(**container_send_kwargs(card))
            return True
    except Exception:
        logger.exception("Failed to send error container to status channel")

    try:
        if status_message is not None:
            await status_message.edit(**container_edit_kwargs(card, attachments=None))
            return True
    except Exception:
        logger.exception("Failed to edit status message with error container")

    return False


def resolve_game_interface(
    interaction: discord.Interaction | None,
    *,
    interface: GameManager | None = None,
) -> GameManager | None:
    if interface is not None:
        return interface
    if interaction is None:
        return None

    channel = getattr(interaction, "channel", None)
    channel_id = getattr(channel, "id", None)
    games = _games_by_thread(interaction)
    if channel_id in games:
        return games[channel_id]

    data = interaction.data if isinstance(interaction.data, dict) else {}
    custom_id = data.get("custom_id")
    if not isinstance(custom_id, str):
        return None

    for prefix in _CUSTOM_ID_GAME_PREFIXES:
        if not custom_id.startswith(prefix):
            continue
        if prefix in (BUTTON_PREFIX_GAME_MOVE, BUTTON_PREFIX_GAME_SELECT):
            tail = custom_id[len(prefix) :]
            token = tail.split("/", 1)[0]
            try:
                thread_id = int(token)
            except (TypeError, ValueError):
                return None
            return games.get(thread_id)
        if prefix == "game:":
            try:
                parsed = CustomId.decode(custom_id)
            except Exception:
                return None
            return games.get(parsed.resource_id)
        tail = custom_id[len(prefix) :]
        token = tail.split("/", 1)[0]
        try:
            thread_id = int(token)
        except (TypeError, ValueError):
            return None
        return games.get(thread_id)
    return None


def _translator_from_interaction(
    interaction: discord.Interaction | None,
    translator: Translator | None,
) -> Translator | None:
    if translator is not None or interaction is None:
        return translator
    container = getattr(getattr(interaction, "client", None), "container", None)
    resolved = getattr(container, "translator", None)
    return resolved if isinstance(resolved, Translator) else None


async def report(
    interaction: discord.Interaction,
    error: BaseException,
    *,
    surface: ErrorSurface,
    translator: Translator | None = None,
    delete_after: float | None = EPHEMERAL_DELETE_AFTER,
    interface: GameManager | None = None,
    game_message: _EditableMessage | None = None,
    thread: _SendableChannel | None = None,
    status_message: _StatusMessage | None = None,
) -> str:
    translator = _translator_from_interaction(interaction, translator)
    original = _unwrap_error(error)

    for mapping in ERROR_MAPPINGS:
        if isinstance(original, mapping.exception_type):
            card = _mapped_card(mapping, translator=translator)
            await send_interaction_card(
                interaction,
                card,
                delete_after=delete_after,
            )
            if any(item is not None for item in (game_message, thread, status_message)):
                await notify_error_destinations(
                    card,
                    logger=log,
                    game_message=game_message,
                    thread=thread,
                    status_message=status_message,
                )
            return ""

    trace_id = uuid4().hex[:8]
    card = build_error_card(
        original,
        surface=surface,
        trace_id=trace_id,
        interaction=interaction,
    )

    log.exception(
        "Unhandled %s error trace_id=%s command=%r",
        surface.value,
        trace_id,
        getattr(getattr(interaction, "command", None), "name", "unknown"),
        exc_info=original,
    )

    try:
        await send_interaction_card(
            interaction,
            card,
            delete_after=delete_after,
        )
    except Exception:
        log.exception("Failed to send interaction error card trace_id=%s", trace_id)

    resolved_interface = resolve_game_interface(interaction, interface=interface)
    if resolved_interface is not None:
        await interrupt_match(
            resolved_interface,
            original,
            trace_id=trace_id,
            logger=log,
        )
        await notify_error_destinations(
            card,
            logger=log,
            game_message=getattr(resolved_interface, "game_message", None)
            or game_message,
            thread=getattr(resolved_interface, "thread", None) or thread,
            status_message=getattr(resolved_interface, "status_message", None)
            or status_message,
        )
        return trace_id

    await notify_error_destinations(
        card,
        logger=log,
        game_message=game_message,
        thread=thread,
        status_message=status_message,
    )
    return trace_id


async def report_runtime_error(
    error: BaseException,
    *,
    surface: ErrorSurface,
    interface: GameManager | None = None,
    logger: Logger | None = None,
    game_message: _EditableMessage | None = None,
    thread: _SendableChannel | None = None,
    status_message: _StatusMessage | None = None,
) -> str:
    trace_id = uuid4().hex[:8]
    logger = logger or log
    card = build_error_card(error, surface=surface, trace_id=trace_id)
    logger.exception(
        "Unhandled runtime error trace_id=%s surface=%s",
        trace_id,
        surface.value,
        exc_info=error,
    )
    if interface is not None:
        await interrupt_match(
            interface,
            error,
            trace_id=trace_id,
            logger=logger,
        )
        await notify_error_destinations(
            card,
            logger=logger,
            game_message=getattr(interface, "game_message", None) or game_message,
            thread=getattr(interface, "thread", None) or thread,
            status_message=getattr(interface, "status_message", None) or status_message,
        )
        return trace_id
    await notify_error_destinations(
        card,
        logger=logger,
        game_message=game_message,
        thread=thread,
        status_message=status_message,
    )
    return trace_id


async def command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
    *,
    translator: Translator,
    delete_after: float = 10,
) -> None:
    if isinstance(error, CheckFailure):
        return

    await report(
        interaction,
        error,
        surface=ErrorSurface.SLASH,
        translator=translator,
        delete_after=delete_after,
    )
