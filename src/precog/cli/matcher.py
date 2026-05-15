"""Canonical-event matcher CLI commands (Cohort 5+ Slot B).

Provides operator-driven commands for the matcher service:

Commands:
    backfill   - One-time backfill of canonical_events from existing
                 upstream source state (games + platform_events).
    status     - Print matcher status: pending queue depth, last
                 heartbeat, recent rate metrics.

Usage:
    precog matcher backfill --all
    precog matcher backfill --batch-size 500 --dry-run
    precog matcher status

Per session-92 4-agent Cohort 5+ Slot B design council + parent spec
``memory/build_spec_slot_a_matcher_pm_memo.md`` § File 5 + session-107
rebase addendum.

Backfill mode is opt-in (--all flag required) per parent spec D2 user
adjudication.  Default behavior is no-op (prints help).

Reference:
    - ``src/precog/matching/canonical_event_matcher.py`` (matcher
      module; this CLI is a thin operator-facing wrapper)
    - ``docs/operations/canonical_event_matcher_runbook.md`` (operator
      runbook with full activation procedure)
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()


app = typer.Typer(
    name="matcher",
    help="Canonical-event matcher operations (Cohort 5+ Slot B)",
    no_args_is_help=True,
)


@app.command()
def backfill(
    all_flag: bool = typer.Option(
        False,
        "--all",
        help="Backfill ALL existing games + platform_events (one-time operation).",
    ),
    batch_size: int = typer.Option(
        1000,
        "--batch-size",
        min=1,
        max=10000,
        help="Rows per transaction (default 1000; smaller values reduce lock duration).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print planned actions without writing; transactions are rolled back.",
    ),
    max_batches: int | None = typer.Option(
        None,
        "--max-batches",
        help="Optional bound on batch count (defensive against runaway loops).",
    ),
) -> None:
    """One-time backfill of canonical_events from existing upstream source state.

    Idempotent via ``uq_canonical_events_nk`` UNIQUE constraint;
    re-running the backfill skips rows that already have canonical
    events.  Receipted: prints per-action counts at end + writes a
    single audit-log summary row.

    Estimated load (per parent spec § File 5):
        ~5,234 games -> ~5,234 canonical_events;
        ~3,574 platform_events with game_id -> ~3,574
        canonical_event_links + matcher audit rows;
        Total ~25-50K row writes at ~1K rows/sec = ~5-15 min wall-clock.
        Off-peak scheduling recommended.

    Example:
        precog matcher backfill --all                           # full backfill
        precog matcher backfill --batch-size 500                # smaller batches
        precog matcher backfill --all --dry-run                 # preview only
    """
    if not all_flag:
        console.print(
            "[yellow]--all flag required to run backfill "
            "(safety guard per parent spec D2 adjudication).[/yellow]"
        )
        console.print(
            "Run with [bold]--all[/bold] to proceed, or [bold]--dry-run[/bold] to preview."
        )
        raise typer.Exit(code=1)

    # Import here to avoid heavy import cost when CLI runs --help.
    from precog.matching.canonical_event_matcher import backfill_all

    mode = "DRY-RUN" if dry_run else "LIVE"
    console.print(
        f"[bold cyan]Matcher backfill starting[/bold cyan] "
        f"(mode={mode}, batch_size={batch_size}, max_batches={max_batches})"
    )

    receipt = backfill_all(
        batch_size=batch_size,
        dry_run=dry_run,
        max_batches=max_batches,
    )

    # Receipt summary table.
    table = Table(title="Matcher Backfill Receipt", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_row("Created (new canonical_events + links)", str(receipt.created))
    table.add_row("Conflicts (idempotent skip)", str(receipt.conflicts))
    table.add_row("Queued for review (sub-threshold)", str(receipt.queued_for_review))
    table.add_row("Errors", str(receipt.errors))
    table.add_row("Batches completed", str(receipt.batches_completed))
    if receipt.finished_at is not None:
        elapsed = (receipt.finished_at - receipt.started_at).total_seconds()
        table.add_row("Elapsed wall-clock (sec)", f"{elapsed:.1f}")
    console.print(table)

    if receipt.error_excerpts:
        console.print("[bold red]Error excerpts (first 10):[/bold red]")
        for excerpt in receipt.error_excerpts:
            console.print(f"  [red]- {excerpt}[/red]")

    console.print(f"\n[bold]{receipt.summary_text()}[/bold]")

    if dry_run:
        console.print(
            "[yellow]DRY-RUN mode: no rows committed. Re-run without --dry-run to apply.[/yellow]"
        )

    # Exit code: 1 if any errors recorded, 0 if clean.  Caller can
    # script around the exit code for CI/automation safety.
    if receipt.errors > 0:
        raise typer.Exit(code=1)


@app.command()
def status() -> None:
    """Print matcher status: pending queue, last heartbeat, recent rate.

    Reads from system_health + canonical_event_match_log; works
    regardless of whether the matcher service is currently running.

    Output:
        - Last heartbeat timestamp + health status (from system_health).
        - Pending queue depth (platform_events with game_id but no
          active canonical_event_link).
        - 24-hour action counts (create / retire / quarantine).
        - Matcher algorithm_id (cohort5_event_matcher_v1 row from
          match_algorithm).

    Example:
        precog matcher status
    """
    # Import here to defer DB connection until command invocation.
    from precog.matching.canonical_event_matcher import get_matcher_status_summary

    summary = get_matcher_status_summary()

    table = Table(title="Canonical Event Matcher Status", show_header=True)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")

    table.add_row(
        "Matcher algorithm_id",
        str(summary.get("matcher_algorithm_id") or "[red](not seeded; check Migration 0091)[/red]"),
    )

    last_hb = summary.get("last_heartbeat")
    if last_hb is None:
        table.add_row(
            "Last heartbeat", "[red](no row in system_health; matcher not running?)[/red]"
        )
    else:
        table.add_row("Last heartbeat", str(last_hb))
        hb_status = summary.get("heartbeat_status")
        if hb_status:
            table.add_row("Heartbeat status", str(hb_status))

    table.add_row(
        "Pending queue (unlinked platform_events)", str(summary["unlinked_platform_events"])
    )
    table.add_row("Creates in last 24h", str(summary["recent_creates_24h"]))
    table.add_row("Retires/quarantines in last 24h", str(summary["recent_retires_24h"]))

    console.print(table)

    pending = summary["unlinked_platform_events"]
    if pending == 0:
        console.print("[green]Pending queue is empty -- matcher fully caught up.[/green]")
    elif pending > 1000:
        console.print(
            f"[yellow]Pending queue depth {pending} is high. "
            "Consider running [bold]precog matcher backfill --all[/bold] "
            "or check that the matcher service is running.[/yellow]"
        )
    else:
        console.print(
            f"[blue]Pending queue depth {pending} -- "
            f"matcher will process at ~{summary.get('matcher_algorithm_id', 'cohort5_event_matcher_v1')} cadence.[/blue]"
        )
