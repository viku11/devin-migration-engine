"""
worker_pool.py
--------------
Architecturally sound worker pool with Active Lifecycle Management.
- Concurrency: 10 simultaneous polling workers.
- Staggering: 1-by-1 session creation with a 15s cooldown to prevent 403 locks.
- Resource Ownership: Each worker is responsible for Birth, Life, and Death (stop) of its session.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from state_store import StateStore, FileStatus
from devin_client import create_session, poll_until_terminal, extract_pr_url

# Strictly enforced organization limits
MAX_CONCURRENT_SESSIONS = 2
MAX_ATTEMPTS = 3
STAGGER_COOLDOWN_SECONDS = 5

# Sized to prevent thread starvation
executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_SESSIONS)


def get_relative_path(filepath: str, src_dir: str) -> str:
    """Standardized relative path for Devin's environment."""
    rel = os.path.relpath(filepath, os.path.dirname(src_dir))
    return rel.replace("\\", "/")


async def process_single_file(
    filepath: str,
    src_dir: str,
    store: StateStore,
    semaphore: asyncio.Semaphore,
    api_creation_lock: asyncio.Lock,
    db_lock: asyncio.Lock,
    results: list,
):
    """
    Manages the full lifecycle of a migration task.
    """
    async with semaphore:
        filename = os.path.basename(filepath)

        # 1. IDEMPOTENCY CHECK
        async with db_lock:
            current_status = store.get_status(filepath)

        if current_status in (FileStatus.COMPLETED, FileStatus.DLQ):
            return

        relative_path = get_relative_path(filepath, src_dir)
        loop = asyncio.get_running_loop()

        # 2. STAGGERED CREATION (The 403 Shield)
        # Only one worker can hit the creation endpoint at a time.
        async with api_creation_lock:
            try:
                print(f"    [DISPATCH] Creating session for {filename}...")
                session_info = await loop.run_in_executor(
                    executor, create_session, filepath, relative_path
                )
                # Force a 15s gap before the NEXT worker can attempt a POST request
                await asyncio.sleep(STAGGER_COOLDOWN_SECONDS)
            except Exception as e:
                async with db_lock:
                    store.mark_dlq(
                        filepath, reason=f"Dispatch Failed: {str(e)}")
                return

        session_id = session_info["session_id"]
        session_url = session_info["url"]

        async with db_lock:
            store.mark_in_progress(filepath, session_id, session_url)

        # 3. PROACTIVE CONCURRENT POLLING
        # No lock needed here; all 10 workers poll their respective sessions simultaneously.
        final_session = await loop.run_in_executor(
            executor, poll_until_terminal, session_id
        )

        final_status = final_session.get("status", "").lower()

        # 4. TERMINAL STATE PERSISTENCE
        async with db_lock:
            if final_status in ("finished", "suspended"):
                pr_url = extract_pr_url(final_session)
                store.conn.execute(
                    "UPDATE file_migrations SET error_reason=NULL WHERE filepath=?", (filepath,))
                store.mark_completed(filepath, pr_url=pr_url)
                results.append(
                    {"file": filename, "status": "COMPLETED", "pr_url": pr_url})
            else:
                reason = f"Session failed with status: {final_status}"
                store.mark_dlq(filepath, reason=reason)
                results.append(
                    {"file": filename, "status": "DLQ", "reason": reason})


async def run_batch(
    batch: list[str],
    batch_number: int,
    src_dir: str,
    store: StateStore,
    limit: int = None,
) -> list[dict]:
    """
    Orchestrates the batch execution with cross-worker locks.
    """
    files_to_process = batch[:limit] if limit else batch

    # Shared synchronization primitives for the batch
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)
    api_creation_lock = asyncio.Lock()
    db_lock = asyncio.Lock()
    results = []

    tasks = [
        process_single_file(filepath, src_dir, store, semaphore,
                            api_creation_lock, db_lock, results)
        for filepath in files_to_process
    ]

    await asyncio.gather(*tasks, return_exceptions=True)
    return results


def run_batch_sync(
    batch: list[str],
    batch_number: int,
    src_dir: str,
    store: StateStore,
    limit: int = None,
) -> list[dict]:
    """Synchronous entry point for main.py."""
    return asyncio.run(run_batch(batch, batch_number, src_dir, store, limit))
