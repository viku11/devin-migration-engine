"""
dashboard.py
------------
Rich terminal dashboard for live migration progress.

This is what you show in the Loom video — a real-time
pane of glass showing the VP of Engineering exactly what's
happening without requiring anyone to babysit the process.
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box
from state_store import StateStore

console = Console()

STATUS_DISPLAY = {
    "PENDING":     ("[dim]⚪ Queued[/dim]",       "white"),
    "IN_PROGRESS": ("[yellow]🟡 Migrating...[/yellow]", "yellow"),
    "COMPLETED":   ("[green]🟢 PR Opened[/green]",  "green"),
    "DLQ":         ("[red]🔴 Needs Review[/red]",   "red"),
}


def render_summary_panel(store: StateStore) -> Panel:
    summary = store.get_summary()
    total = sum(summary.values())
    completed = summary.get("COMPLETED", 0)
    in_progress = summary.get("IN_PROGRESS", 0)
    dlq = summary.get("DLQ", 0)
    pending = summary.get("PENDING", 0)

    pct = int((completed / total * 100)) if total > 0 else 0

    text = Text()
    text.append(f"  Total Files: {total}   ", style="bold white")
    text.append(f"✅ Done: {completed}   ", style="bold green")
    text.append(f"🟡 Active: {in_progress}   ", style="bold yellow")
    text.append(f"🔴 DLQ: {dlq}   ", style="bold red")
    text.append(f"⚪ Queued: {pending}   ", style="dim")
    text.append(f"  [{pct}% complete]", style="bold cyan")

    return Panel(
        text,
        title="[bold blue]ShopDirect × IDURAR — TypeScript Migration Engine[/bold blue]",
        border_style="blue",
    )


def render_migration_table(store: StateStore, max_rows: int = 40) -> Table:
    rows = store.get_all_rows()

    table = Table(
        box=box.ROUNDED,
        border_style="dim",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )

    table.add_column("Batch", width=6, justify="center")
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Status", width=20)
    table.add_column("Attempts", width=9, justify="center")
    table.add_column("PR / Session", style="dim", no_wrap=True)

    for row in rows[:max_rows]:
        status_key = row["status"]
        status_display, _ = STATUS_DISPLAY.get(
            status_key, (status_key, "white")
        )

        # Show PR url if completed, session url if in progress
        link = row.get("pr_url") or row.get("session_url") or row.get("error_reason") or "—"
        if len(link) > 55:
            link = link[:52] + "..."

        table.add_row(
            str(row["batch_number"] + 1),
            row["filename"],
            status_display,
            str(row["attempts"]),
            link,
        )

    if len(rows) > max_rows:
        table.add_row("...", f"[dim]+{len(rows)-max_rows} more files[/dim]", "", "", "")

    return table


def print_batch_header(batch_number: int, batch_files: list[str]):
    """Print a clear header when starting a new batch."""
    filenames = [f.split("/")[-1].split("\\")[-1] for f in batch_files]
    console.print()
    console.rule(
        f"[bold yellow]▶ Dispatching Batch {batch_number + 1} "
        f"— {len(batch_files)} files[/bold yellow]"
    )
    for f in filenames:
        console.print(f"  [cyan]→[/cyan] {f}")
    console.print()


def print_batch_complete(batch_number: int, results: list[dict]):
    """Print a summary after a batch finishes."""
    completed = [r for r in results if r.get("status") == "COMPLETED"]
    dlq = [r for r in results if r.get("status") == "DLQ"]

    console.print()
    console.rule(
        f"[bold green]✓ Batch {batch_number + 1} Complete[/bold green]"
    )
    console.print(f"  ✅ [green]{len(completed)} PRs opened[/green]")
    if dlq:
        console.print(f"  🔴 [red]{len(dlq)} routed to DLQ (human review needed)[/red]")
        for r in dlq:
            console.print(f"     [dim]• {r['file']}: {r.get('reason', '')}[/dim]")
    console.print()


def print_final_report(store: StateStore):
    """Print the full summary at the end of all batches."""
    console.print()
    console.rule("[bold blue]Migration Complete — Final Report[/bold blue]")
    console.print(render_summary_panel(store))
    console.print(render_migration_table(store))

    dlq_rows = [r for r in store.get_all_rows() if r["status"] == "DLQ"]
    if dlq_rows:
        console.print(
            Panel(
                "\n".join(
                    f"  • [cyan]{r['filename']}[/cyan]: {r.get('error_reason', 'unknown')}"
                    for r in dlq_rows
                ),
                title="[red]🔴 Dead Letter Queue — Files Requiring Human Review[/red]",
                border_style="red",
            )
        )
