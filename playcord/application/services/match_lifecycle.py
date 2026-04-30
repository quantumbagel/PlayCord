"""Match start/finish orchestration for GameManager."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from playcord.application.runtime_context import get_container
from playcord.application.services.game_manager import GameManager
from playcord.application.services.rating import (
    rated_results_for_placements,
    unrated_results_for_placements,
)
from playcord.application.services.role_management import (
    assign_roles,
    reorder_players_by_roles,
    role_assignments_to_db_tuples,
)
from playcord.infrastructure.db_thread import run_in_thread
from playcord.infrastructure.locale import get
from playcord.infrastructure.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

log = get_logger("match.lifecycle")


async def start_match_from_lobby(
    interface: Any,
    plugin_class: type[Any],
    *,
    rematch_view_factory: Callable[[int, str], Any] | None = None,
) -> GameManager:
    message = interface.message
    players = (
        interface.all_players()
        if callable(getattr(interface, "all_players", None))
        else list(interface.queued_players)
    )
    has_bots = any(getattr(player, "is_bot", False) for player in players)
    reg = get_container().registry
    for player in list(interface.queued_players):
        reg.user_to_matchmaking.pop(player, None)
        player_id = getattr(player, "id", None)
        if player_id is not None:
            reg.user_to_matchmaking.pop(int(player_id), None)
    reg.matchmaking_by_message_id.pop(message.id, None)

    match_options = dict(getattr(interface, "match_settings", {}) or {})
    matches = get_container().matches_repository

    game_instance = plugin_class(players)
    role_selections = getattr(interface, "role_selections", {})
    role_assignments = assign_roles(game_instance, role_selections)

    if role_assignments:
        players = reorder_players_by_roles(players, role_assignments)

    match_code = await run_in_thread(matches.ensure_unique_match_code)
    thread = await message.create_thread(
        name=f"{game_instance.metadata.name} - {match_code}",
    )

    match_id, match_code = await run_in_thread(
        matches.create_game,
        interface.game_type,
        message.guild.id,
        [player.id for player in players],
        bool(interface.rated and not has_bots),
        message.channel.id,
        thread.id,
        {"match_options": match_options},
        match_id=thread.id,
        preset_match_code=match_code,
    )

    if role_assignments:
        db_tuples = role_assignments_to_db_tuples(role_assignments)
        await run_in_thread(
            get_container().roles_repository.save_role_assignments,
            match_id,
            db_tuples,
        )
        log.debug(
            "Saved %d role assignments for match %s",
            len(role_assignments),
            match_id,
        )

    runtime = GameManager(
        game_type=interface.game_type,
        plugin_class=plugin_class,
        overview_message=message,
        creator=interface.creator,
        players=players,
        rated=bool(interface.rated and not has_bots),
        match_id=match_id,
        match_public_code=match_code,
        match_options=match_options,
        thread=thread,
    )
    runtime.rematch_view_factory = rematch_view_factory
    await runtime.setup()
    return runtime


async def finish_match(runtime: GameManager, outcome: Any) -> None:
    placements = getattr(outcome, "placements", []) or []
    players = list(runtime.players)
    if runtime.rated:
        results = rated_results_for_placements(players, runtime.game_type, placements)
    else:
        results = unrated_results_for_placements(players, runtime.game_type, placements)

    final_state = {
        "outcome": getattr(outcome, "kind", "winner"),
        "reason": getattr(outcome, "reason", None),
        "placements": [
            [getattr(player, "id", None) for player in group] for group in placements
        ],
    }
    matches = get_container().matches_repository
    await run_in_thread(
        matches.end_match,
        runtime.game_id,
        final_state,
        results,
    )

    global_summary: str | None = None
    summaries: dict[int, str] | None = None
    mg = getattr(runtime.plugin, "match_global_summary", None)
    if callable(mg):
        try:
            global_summary = mg(outcome)
        except Exception:
            log.exception("match_global_summary failed match_id=%s", runtime.game_id)
    ms = getattr(runtime.plugin, "match_summary", None)
    if callable(ms):
        try:
            raw = ms(outcome)
        except Exception:
            log.exception("match_summary failed match_id=%s", runtime.game_id)
            raw = None
        if isinstance(raw, dict) and raw:
            summaries = {}
            for key, text in raw.items():
                try:
                    uid = int(key)
                except (TypeError, ValueError):
                    continue
                summaries[uid] = str(text)

    if (global_summary and str(global_summary).strip()) or summaries:
        try:
            await run_in_thread(
                matches.merge_match_metadata_outcome_display,
                runtime.game_id,
                summaries=summaries,
                global_summary=global_summary,
            )
        except Exception:
            log.exception(
                "merge_match_metadata_outcome_display failed match_id=%s",
                runtime.game_id,
            )

    reg = get_container().registry
    for player in players:
        player_id = getattr(player, "id", None)
        if player_id is not None:
            reg.user_to_game.pop(int(player_id), None)
    if runtime.thread is not None:
        tid = runtime.thread.id
        reg.discard_thread_cache(tid)
        reg.games_by_thread_id.pop(tid, None)

    summary = _summary_text(runtime, outcome, results)
    if runtime.thread is not None:
        await runtime.thread.send(summary)
        await runtime.thread.edit(
            locked=True,
            archived=True,
            reason=get("threads.game_over"),
        )
    rematch_view_factory = getattr(runtime, "rematch_view_factory", None)
    rematch_view = (
        rematch_view_factory(runtime.game_id, summary)
        if callable(rematch_view_factory)
        else None
    )
    safe_edit = getattr(runtime, "_safe_edit_message", None)
    if callable(safe_edit):
        await safe_edit(
            runtime.status_message,
            content=summary,
            view=rematch_view,
            attachments=[],
        )
    else:
        await runtime.status_message.edit(
            content=summary,
            view=rematch_view,
            attachments=[],
        )


def _summary_text(
    runtime: GameManager,
    outcome: Any,
    results: dict[int, dict[str, Any]],
) -> str:
    lines = [f"**{runtime.plugin.metadata.name}** finished."]
    if getattr(outcome, "kind", None) == "winner" and getattr(
        outcome,
        "placements",
        None,
    ):
        winner = outcome.placements[0][0]
        lines.append(f"Winner: {winner.mention}")
    elif getattr(outcome, "kind", None) == "draw":
        lines.append("Result: Draw")
    if runtime.rated:
        lines.append("")
        for player in runtime.players:
            result = results[int(player.id)]
            before_cr = float(result["mu_before"]) - (3 * float(result["sigma_before"]))
            after_cr = float(result["new_mu"]) - (3 * float(result["new_sigma"]))
            delta = round(after_cr - before_cr)
            delta_text = f"{delta:+d}"
            lines.append(f"{player.mention}: {round(before_cr)} ({delta_text})")
    return "\n".join(lines)
