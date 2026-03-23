import asyncio
import logging
from devin_client import DevinClient

logger = logging.getLogger(__name__)


class MigrationOrchestrator:
    def __init__(self, devin_client: DevinClient):
        # BURST CONTROL: Prevents HTTP 429 DDoS blocks from Devin's ingress
        self.semaphore = asyncio.Semaphore(15)
        self.client = devin_client

    async def process_file(self, file_path: str, branch_name: str, prompt: str) -> bool:
        async with self.semaphore:
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
                f"[DISPATCHED] Session active for {file_path}: {session_url}")

            return True
