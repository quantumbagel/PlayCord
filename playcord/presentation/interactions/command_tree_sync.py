"""Programmatic app-command tree builder."""

from __future__ import annotations

import inspect
from typing import Any

import discord
from discord import app_commands
from discord.app_commands.models import AppCommand, AppCommandGroup, Argument

from playcord.games import GAME_BY_KEY, GAMES
from playcord.infrastructure.locale import fmt, get
from playcord.presentation.cogs.games import handle_autocomplete, handle_move
from playcord.presentation.cogs.general import command_play
from playcord.presentation.ui.containers import CustomContainer


def _annotation_for_parameter(parameter: Any) -> Any:
    if parameter.kind.value == "integer":
        if parameter.min_value is not None and parameter.max_value is not None:
            return app_commands.Range[int, parameter.min_value, parameter.max_value]
        return int
    return str


def _choices_decorator(command: app_commands.Command[Any, ..., Any], move: Any) -> None:
    for parameter in move.options:
        command._params[parameter.name].description = parameter.description  # type: ignore[attr-defined]
        if not parameter.choices:
            continue
        choices = [
            app_commands.Choice(name=label, value=value)
            for label, value in parameter.choices
        ]
        command._params[parameter.name].choices = choices  # type: ignore[attr-defined]


def _build_move_callback(plugin_key: str, move: Any):
    async def _callback(interaction: discord.Interaction, **kwargs: Any) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        arguments = {"ctx": interaction, **kwargs}
        await handle_move(
            ctx=interaction,
            name=move.name,
            arguments=arguments,
        )

    parameters = [
        inspect.Parameter(
            "interaction",
            kind=inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=discord.Interaction,
        ),
    ]
    annotations = {"interaction": discord.Interaction}
    for parameter in move.options:
        default = None if parameter.optional else inspect.Parameter.empty
        annotation = _annotation_for_parameter(parameter)
        annotations[parameter.name] = annotation
        parameters.append(
            inspect.Parameter(
                parameter.name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            ),
        )

    _callback.__name__ = f"{plugin_key}_{move.name}"
    _callback.__qualname__ = _callback.__name__
    _callback.__signature__ = inspect.Signature(parameters=parameters)
    _callback.__annotations__ = annotations
    return _callback


def _register_autocomplete(
    command: app_commands.Command[Any, ..., Any],
    move: Any,
) -> None:
    def _make_autocomplete(move_name: str, argument_name: str):
        async def _autocomplete(
            interaction: discord.Interaction,
            current: str,
        ):
            return await handle_autocomplete(
                interaction,
                move_name,
                current,
                argument_name,
            )

        return _autocomplete

    for parameter in move.options:
        if not parameter.autocomplete:
            continue

        command.autocomplete(parameter.name)(
            _make_autocomplete(move.name, parameter.name),
        )


def build_game_group(game: Any) -> app_commands.Group:
    metadata = game.metadata()
    group = app_commands.Group(
        name=game.key,
        description=metadata.move_group_description,
        guild_only=True,
    )
    for move in metadata.moves:
        callback = _build_move_callback(game.key, move)
        command = app_commands.command(
            name=move.name,
            description=move.description,
        )(callback)
        _choices_decorator(command, move)
        _register_autocomplete(command, move)
        group.add_command(command)
    return group


def build_tree(bot: discord.Client) -> list[app_commands.Group]:
    """Return all top-level groups built without `exec`."""
    built_groups = [build_game_group(game) for game in GAMES]
    bot.tree.add_command(command_play)
    for group in built_groups:
        if game := GAME_BY_KEY.get(group.name):
            _ = game
        bot.tree.add_command(group)
    return built_groups


"""
Compare locally registered app commands to Discord's API (``tree.fetch_commands``).
"""


def _collect_local_leaves(
    cmd: app_commands.Command | app_commands.Group | app_commands.ContextMenu,
    prefix: tuple[str, ...],
) -> dict[str, app_commands.Command | app_commands.ContextMenu]:
    if isinstance(cmd, app_commands.Group):
        new_p = (*prefix, cmd.name)
        out: dict[str, app_commands.Command | app_commands.ContextMenu] = {}

        # FIX: Iterate directly over the list, do not use .values()
        for child in cmd.commands:
            out.update(_collect_local_leaves(child, new_p))
        return out

    path = " ".join((*prefix, cmd.name))
    return {path: cmd}


def collect_local_tree(
    tree: app_commands.CommandTree,
    *,
    guild: discord.abc.Snowflake | None,
) -> dict[str, app_commands.Command]:
    merged: dict[str, app_commands.Command] = {}
    for top in tree.get_commands(guild=guild):
        merged.update(_collect_local_leaves(top, ()))
    return merged


def _collect_remote_leaves(ac: AppCommand) -> dict[str, dict[str, Any]]:
    """Map qualified slash path -> {description, arguments} for leaf commands."""
    out: dict[str, dict[str, Any]] = {}

    def walk(node: AppCommand | AppCommandGroup, parts: tuple[str, ...]) -> None:
        options = list(getattr(node, "options", None) or [])
        if not options or all(isinstance(opt, Argument) for opt in options):
            out[" ".join(parts)] = {
                "description": (getattr(node, "description", "") or "").strip(),
                "arguments": [opt for opt in options if isinstance(opt, Argument)],
            }
            return
        for opt in options:
            if (
                isinstance(opt, AppCommandGroup)
                or getattr(opt, "options", None) is not None
            ):
                walk(opt, (*parts, opt.name))

    walk(ac, (ac.name,))
    return out


def collect_remote_tree(commands: list[AppCommand]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for ac in commands:
        merged.update(_collect_remote_leaves(ac))
    return merged


def _deep_compare_leaf(
    local_cmd: app_commands.Command,
    remote_description: str,
    remote_args: list[Argument],
) -> list[str]:
    """Compare a local leaf Command to API description + option list."""
    differences: list[str] = []
    loc_desc = (local_cmd.description or "").strip()
    if loc_desc != remote_description:
        differences.append(get("commands.treediff.diff.command_description_modified"))

    lparams = {p.name: p for p in local_cmd.parameters}
    ropts = {a.name: a for a in remote_args}

    if set(lparams.keys()) != set(ropts.keys()):
        differences.append(
            fmt(
                "commands.treediff.diff.parameter_set_mismatch",
                local_names=", ".join(sorted(lparams.keys())) or "—",
                remote_names=", ".join(sorted(ropts.keys())) or "—",
            ),
        )
        return differences

    for name in sorted(lparams):
        p = lparams[name]
        r = ropts[name]
        if (p.description or "").strip() != (r.description or "").strip():
            differences.append(
                fmt("commands.treediff.diff.param_description_modified", name=name),
            )
        if p.required != r.required:
            differences.append(
                fmt("commands.treediff.diff.param_required_modified", name=name),
            )
        if p.type.value != r.type.value:
            differences.append(
                fmt("commands.treediff.diff.param_type_modified", name=name),
            )

    return differences


def analyze_command_tree_drift(
    *,
    local_leaves: dict[str, app_commands.Command],
    remote_leaves: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    added = sorted(set(local_leaves.keys()) - set(remote_leaves.keys()))
    removed = sorted(set(remote_leaves.keys()) - set(local_leaves.keys()))
    modified: dict[str, list[str]] = {}
    for name in sorted(set(local_leaves.keys()) & set(remote_leaves.keys())):
        lc = local_leaves[name]
        rnode = remote_leaves[name]
        rdesc = rnode.get("description") or ""
        rargs = rnode.get("arguments") or []
        diffs = _deep_compare_leaf(lc, rdesc, rargs)
        if diffs:
            modified[name] = diffs
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "local_all": sorted(local_leaves.keys()),
        "remote_all": sorted(remote_leaves.keys()),
    }


def format_drift_report(drift: dict[str, Any], *, max_lines: int = 40) -> str:
    lines: list[str] = []
    none = get("common.empty_markdown")
    local_all = drift.get("local_all") or []
    remote_all = drift.get("remote_all") or []
    lines.append(fmt("commands.treediff.report.all_local", count=len(local_all)))
    if local_all:
        lines.extend(f"- `{n}`" for n in local_all)
    else:
        lines.append(none)
    lines.append(fmt("commands.treediff.report.all_remote", count=len(remote_all)))
    if remote_all:
        lines.extend(f"- `{n}`" for n in remote_all)
    else:
        lines.append(none)
    lines.append(get("commands.treediff.report.separator"))
    lines.append(get("commands.treediff.report.section_diff"))
    lines.append(get("commands.treediff.report.added_header"))
    if drift["added"]:
        lines.extend(f"- `{n}`" for n in drift["added"])
    else:
        lines.append(none)
    lines.append(get("commands.treediff.report.removed_header"))
    if drift["removed"]:
        lines.extend(f"- `{n}`" for n in drift["removed"])
    else:
        lines.append(none)
    mod = drift.get("modified") or {}
    if not mod:
        lines.append(get("commands.treediff.report.modified_none"))
    else:
        lines.append(get("commands.treediff.report.modified_header"))
        line_count = 0
        truncated = False
        for cmd_name, changes in mod.items():
            if line_count >= max_lines:
                truncated = True
                break
            lines.append(f"- `{cmd_name}`")
            line_count += 1
            for c in changes:
                if line_count >= max_lines:
                    truncated = True
                    break
                lines.append(f"  - `{c}`")
                line_count += 1
            if truncated:
                break
        if truncated:
            lines.append(get("commands.treediff.report.truncated"))
    return "\n".join(lines)


_ZWSP = "\u200b"


def drift_to_container(
    drift: dict[str, Any],
    *,
    color: discord.Color | None,
    title: str,
    inline_column_limit: int = 340,
    max_modified_sections: int = 14,
) -> CustomContainer:
    """Build one embed: summary row, three-column drift (added / removed / modified names),
    then non-inline fields per modified command (diff lines).
    """
    local_all = list(drift.get("local_all") or [])
    remote_all = list(drift.get("remote_all") or [])
    added = list(drift.get("added") or [])
    removed = list(drift.get("removed") or [])
    modified: dict[str, list[str]] = dict(drift.get("modified") or {})

    container = CustomContainer(title=title[:256], color=color)
    container.description = fmt(
        "commands.treediff.embed_description_stats",
        local_n=len(local_all),
        remote_n=len(remote_all),
        n_add=len(added),
        n_rem=len(removed),
        n_mod=len(modified),
    )[:4096]

    def short_list(names: list[str], lim: int) -> str:
        if not names:
            return get("common.empty_markdown")
        parts = [f"`{n}`" for n in names]
        s = ", ".join(parts)
        if len(s) <= lim:
            return s
        acc: list[str] = []
        total = 0
        for p in parts:
            sep = 2 if acc else 0
            if total + sep + len(p) > lim - 3:
                break
            acc.append(p)
            total += sep + len(p)
        return ", ".join(acc) + "\n…" if acc else "…"

    row_counts = [
        ("commands.treediff.field_local_leaves", str(len(local_all))),
        ("commands.treediff.field_remote_leaves", str(len(remote_all))),
        (
            "commands.treediff.field_drift_totals",
            f"`+{len(added)}` / `−{len(removed)}` / `~{len(modified)}`",
        ),
    ]
    for locale_key, value in row_counts:
        container.add_field(name=get(locale_key), value=value[:1024], inline=True)

    row_lists = [
        ("commands.treediff.field_added", short_list(added, inline_column_limit)),
        ("commands.treediff.field_removed", short_list(removed, inline_column_limit)),
        (
            "commands.treediff.field_modified_cmds",
            short_list(list(modified.keys()), inline_column_limit),
        ),
    ]
    for locale_key, value in row_lists:
        container.add_field(name=get(locale_key), value=value[:1024], inline=True)

    mod_sorted = sorted(modified.items())
    shown = 0
    for cmd_name, changes in mod_sorted:
        if len(container.fields) >= 24 or shown >= max_modified_sections:
            break
        body = "\n".join(f"• {c}" for c in changes)
        if len(body) > 1024:
            body = body[:1021] + "…"
        container.add_field(
            name=f"`{cmd_name}`"[:256],
            value=body or _ZWSP,
            inline=False,
        )
        shown += 1

    if shown < len(mod_sorted):
        container.add_field(
            name=get("commands.treediff.field_more_modified"),
            value=fmt(
                "commands.treediff.more_modified_detail",
                n=len(mod_sorted) - shown,
            ),
            inline=False,
        )

    return container


async def fetch_and_analyze_tree(
    tree: app_commands.CommandTree,
    *,
    guild: discord.abc.Snowflake | None = None,
) -> dict[str, Any]:
    remote = await tree.fetch_commands(guild=guild)
    local_leaves = collect_local_tree(tree, guild=guild)
    remote_leaves = collect_remote_tree(list(remote))
    return analyze_command_tree_drift(
        local_leaves=local_leaves,
        remote_leaves=remote_leaves,
    )
