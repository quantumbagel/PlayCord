"""Game registration and metadata validation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from playcord.api import (
    GameMetadata,
    HandlerRef,
    HandlerSpec,
    PlayerOrder,
    RoleMode,
    RuntimeGame,
)
from playcord.core.errors import ConfigurationError


def resolve_player_count(game_class: type[RuntimeGame]) -> int | list[int] | None:
    player_count = game_class.metadata.player_count
    if isinstance(player_count, int):
        return player_count
    if isinstance(player_count, tuple):
        return list(player_count)
    return None


@dataclass(frozen=True, slots=True)
class RegisteredGame:
    key: str
    game_class: type[RuntimeGame]

    @property
    def module_name(self) -> str:
        return self.game_class.__module__

    @property
    def class_name(self) -> str:
        return self.game_class.__name__

    def load(self) -> type[RuntimeGame]:
        return self.game_class

    def metadata(self) -> GameMetadata:
        return self.game_class.metadata


_REGISTRY: dict[str, RegisteredGame] = {}


def _handler_name(spec: HandlerSpec) -> str | None:
    if spec is None:
        return None
    if isinstance(spec, HandlerRef):
        return spec.name
    if isinstance(spec, str):
        return spec
    return None


def _validate_handler(
    game_class: type[RuntimeGame],
    spec: HandlerSpec,
    *,
    label: str,
    default_attr: str | None = None,
) -> None:
    if spec is None and default_attr is not None:
        spec = default_attr

    if spec is None:
        return

    if callable(spec):
        return

    name = _handler_name(spec)
    if not name:
        msg = f"Invalid handler for {label} on {game_class.__name__}: {spec!r}"
        raise ConfigurationError(
            msg,
        )
    resolved = getattr(game_class, name, None)
    if not callable(resolved):
        msg = (
            f"Configured handler {name!r} for {label} is missing on"
            f" {game_class.__name__}"
        )
        raise ConfigurationError(
            msg,
        )


def validate_game_registration(game_class: type[RuntimeGame]) -> None:
    metadata = game_class.metadata
    main = getattr(game_class, "main", None)
    if not callable(main):
        msg = f"{game_class.__name__} must define async main(self)"
        raise ConfigurationError(msg)

    for move in metadata.moves:
        for option in move.options:
            if option.autocomplete is None:
                continue
            _validate_handler(
                game_class,
                option.autocomplete,
                label=f"autocomplete:{move.name}.{option.name}",
            )

    for difficulty, definition in metadata.bots.items():
        _validate_handler(
            game_class,
            definition.callback,
            label=f"bot:{difficulty}",
            default_attr="bot_move",
        )

    if metadata.peek_callback is not None:
        _validate_handler(
            game_class,
            metadata.peek_callback,
            label="peek",
        )


def register_game(
    game_class: type[RuntimeGame],
    *,
    key: str | None = None,
) -> RegisteredGame:
    validate_game_registration(game_class)
    resolved_key = key or game_class.metadata.key
    if resolved_key != game_class.metadata.key:
        msg = (
            f"Registered key {resolved_key!r} must match metadata.key"
            f" {game_class.metadata.key!r}"
        )
        raise ConfigurationError(
            msg,
        )

    existing = _REGISTRY.get(resolved_key)
    if existing is not None and existing.game_class is not game_class:
        msg = (
            f"Game key {resolved_key!r} is already registered by {existing.class_name}"
        )
        raise ConfigurationError(
            msg,
        )

    registered = RegisteredGame(resolved_key, game_class)
    _REGISTRY[resolved_key] = registered
    return registered


def iter_registered_games() -> tuple[RegisteredGame, ...]:
    return tuple(_REGISTRY.values())


def get_registered_game(key: str) -> RegisteredGame | None:
    return _REGISTRY.get(key)


def clear_registry() -> None:
    _REGISTRY.clear()


__all__ = [
    "PlayerOrder",
    "RegisteredGame",
    "RoleMode",
    "clear_registry",
    "get_registered_game",
    "iter_registered_games",
    "register_game",
    "resolve_player_count",
    "validate_game_registration",
]
