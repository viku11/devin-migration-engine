import asyncio
import time
import logging
from devin_client import DevinClient
from state_store import StateStore

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SESSIONS = 7
PR_TIMEOUT = 600  # 10 Minutes


class MigrationOrchestrator:
    """Manages async dispatch, rate limiting, and lifecycle of Devin sessions."""

    def __init__(self, state_store: StateStore, devin_client: DevinClient):
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)
        self.store = state_store
        self.client = devin_client

    async def process_file(self, file_path: str, branch_name: str, prompt: str) -> bool:
        """Processes a single file with strict concurrency limits and backoff logic."""
        async with self.semaphore:
            # 1. Dispatch with Exponential Backoff
            session_id = None
            base_delay = 5
            max_delay = 60
            attempt = 0

            while not session_id:
                session_id = await self.client.create_devin_session(file_path, prompt)

                if session_id == "RATE_LIMIT":
                    delay = min(max_delay, base_delay * (2 ** attempt))
                    logger.warning(
                        f"[429] Rate limited on {file_path}. Backing off {delay}s...")
                    await asyncio.sleep(delay)
                    attempt += 1
                    session_id = None
                elif not session_id:
                    logger.error(
                        f"[ERROR] Failed to start {file_path}. Retrying in 5s...")
                    await asyncio.sleep(5)

            session_url = f"https://app.devin.ai/sessions/{session_id}"
            logger.info(
                f"[DISPATCH] Session active for {file_path}: {session_url}")
            self.store.mark_in_progress(file_path, session_id, session_url)

            # 2. Closed-Loop Polling
            start_time = time.time()
            while time.time() - start_time < PR_TIMEOUT:
                pr_data = await self.client.check_github_for_pr(branch_name)

                if pr_data:
                    pr_url = pr_data.get('html_url', '')
                    logger.info(
                        f"[REVIEW REQUIRED] PR opened for {file_path}: {pr_url}")

                    # Agent finished successfully. Kill the container to free slot.
                    await self.client.stop_devin_session(session_id)
                    self.store.mark_completed(file_path, pr_url)
                    return True

                await asyncio.sleep(30)
                elapsed = int(time.time() - start_time)
                if elapsed % 60 == 0:
                    logger.debug(
                        f"[POLLING] {file_path} - {elapsed}s/{PR_TIMEOUT}s elapsed...")

            # 3. Timeout Handling
            logger.warning(
                f"[TIMEOUT] Abandoning {file_path} after 10 mins. Freeing slot.")
            await self.client.stop_devin_session(session_id)
            self.store.mark_dlq(file_path, "Timeout waiting for PR creation")
            return False
