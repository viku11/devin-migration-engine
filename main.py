"""
main.py
-------
Entry point for the ShopDirect TypeScript Migration Engine.

Usage:
    python main.py --src ../idurar-erp-crm/frontend/src --dry-run
    python main.py --src ../idurar-erp-crm/frontend/src --batch 0 --limit 10
    python main.py --src ../idurar-erp-crm/frontend/src
    python main.py --src ../idurar-erp-crm/frontend/src --status
"""

import argparse
import os
import sys
from dotenv import load_dotenv
from rich.prompt import Confirm

from dependency_graph import build_dependency_graph, topological_sort_batches, print_dependency_report
from state_store import StateStore, FileStatus
from worker_pool import run_batch_sync, MAX_CONCURRENT_SESSIONS
from dashboard import (
    print_batch_header,
    print_batch_complete,
    print_final_report,
    render_summary_panel,
    render_migration_table,
    console,
)

load_dotenv()


def validate_env():
    missing = []
    for var in ["DEVIN_API_KEY", "DEVIN_ORG_ID", "GITHUB_REPO"]:
        if not os.getenv(var):
            missing.append(var)
    if missing:
        console.print(
            f"[red]❌ Missing environment variables: {', '.join(missing)}[/red]")
        console.print(
            "[dim]Copy .env.example to .env and fill in your values.[/dim]")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="ShopDirect TypeScript Migration Engine — powered by Devin API"
    )
    parser.add_argument(
        "--src",
        default="../idurar-erp-crm/frontend/src",
        help="Path to the frontend/src directory of the target repo",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze and print the migration plan without dispatching Devin sessions",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        help="Run only a specific batch number (0-indexed). Useful for resuming.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of files per batch. Useful for demos and pilots.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print current migration status and exit",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.status:
        validate_env()

    src_dir = os.path.abspath(args.src)
    if not os.path.isdir(src_dir):
        console.print(f"[red]❌ Source directory not found: {src_dir}[/red]")
        sys.exit(1)

    # --- Step 1: Dependency analysis ---
    console.print(f"\n[bold blue]🔍 Analyzing dependency graph...[/bold blue]")
    graph, all_files = build_dependency_graph(src_dir)
    batches = topological_sort_batches(graph, all_files)

    console.print(f"  Found [cyan]{len(all_files)}[/cyan] JSX files")
    console.print(
        f"  Organized into [cyan]{len(batches)}[/cyan] dependency-aware batches")
    console.print(
        f"  Running [cyan]{MAX_CONCURRENT_SESSIONS}[/cyan] parallel Devin sessions\n")

    # --- Step 2: Initialize state store ---
    store = StateStore()
    store.initialize_files(batches)

    # --- Status check mode ---
    if args.status:
        console.print(render_summary_panel(store))
        console.print(render_migration_table(store))
        return

    # --- Dry run mode ---
    if args.dry_run:
        print_dependency_report(src_dir)
        console.print(
            "[yellow]DRY RUN — no Devin sessions dispatched.[/yellow]")
        return

    # --- Main migration loop ---
    console.print(render_summary_panel(store))

    if args.limit:
        console.print(
            f"[yellow]⚡ Pilot mode: processing first {args.limit} files per batch[/yellow]\n"
        )

    batches_to_run = (
        [batches[args.batch]] if args.batch is not None else batches
    )
    batch_offset = args.batch if args.batch is not None else 0

    for i, batch in enumerate(batches_to_run):
        actual_batch_num = batch_offset + i

        # BUG FIX: Only skip batch if ALL files are COMPLETED
        # DLQ files still need human attention — don't auto-skip
        pending = [
            f for f in batch
            if store.get_status(f) not in (FileStatus.COMPLETED,)
        ]

        completed_count = sum(
            1 for f in batch
            if store.get_status(f) == FileStatus.COMPLETED
        )
        dlq_count = sum(
            1 for f in batch
            if store.get_status(f) == FileStatus.DLQ
        )

        if not pending:
            console.print(
                f"[dim]⏭  Batch {actual_batch_num + 1} already complete — skipping[/dim]"
            )
            continue

        # If all pending files are DLQ, ask human before proceeding
        truly_pending = [
            f for f in batch
            if store.get_status(f) == FileStatus.PENDING
        ]

        if not truly_pending and dlq_count > 0:
            console.print(
                f"\n[bold yellow]⚠️  Batch {actual_batch_num + 1} has {dlq_count} files in DLQ "
                f"and {completed_count} completed.[/bold yellow]"
            )
            console.print(
                f"[dim]DLQ files failed to create sessions (likely rate limiting).[/dim]"
            )
            if not Confirm.ask(f"   Reset DLQ files to PENDING and retry Batch {actual_batch_num + 1}?"):
                if not Confirm.ask("   Skip this batch and continue to next?"):
                    console.print("[dim]Migration paused.[/dim]")
                    break
                continue
            else:
                # Reset DLQ files to PENDING for retry
                for f in batch:
                    if store.get_status(f) == FileStatus.DLQ:
                        store.conn.execute(
                            "UPDATE file_migrations SET status='PENDING', attempts=0 WHERE filepath=?",
                            (f,)
                        )
                store.conn.commit()
                pending = [f for f in batch if store.get_status(
                    f) != FileStatus.COMPLETED]

        print_batch_header(
            actual_batch_num, pending[:args.limit] if args.limit else pending)
        results = run_batch_sync(
            pending, actual_batch_num, src_dir, store, limit=args.limit)
        print_batch_complete(actual_batch_num, results)

        # After each batch (except the last), pause for PR review
        if i < len(batches_to_run) - 1:
            console.print(
                "[bold yellow]⏸  Pause:[/bold yellow] Review and merge the PRs above before continuing.\n"
                "   This ensures the next batch's files can import typed dependencies.\n"
            )
            if not Confirm.ask("   Merge complete? Continue to next batch?"):
                console.print(
                    "[dim]Migration paused. Re-run with --status to check progress.[/dim]")
                break

    print_final_report(store)


if __name__ == "__main__":
    main()
