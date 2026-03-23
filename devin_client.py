import aiohttp
import os
import logging
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class DevinClient:
    """Manages API connections strictly to Devin using the V3 Enterprise API."""

    def __init__(self):
        self.api_key = os.getenv("DEVIN_API_KEY")
        self.org_id = os.getenv(
            "DEVIN_ORG_ID", "org-0e2b658611d540688a1cba439bc03d04")

        if not self.api_key:
            raise EnvironmentError(
                "CRITICAL: DEVIN_API_KEY missing from environment.")

        # Base URL for creating and listing sessions
        self.base_url = f"https://api.devin.ai/v3/organizations/{self.org_id}/sessions"

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def create_devin_session(self, file_path: str, prompt: str) -> Optional[str]:
        """Dispatches the agent via V3 API."""
        payload = {"prompt": prompt}
        session = await self._get_session()

        async with session.post(self.base_url, headers=self.headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get('session_id')
            elif resp.status == 429:
                return "RATE_LIMIT"

            logger.error(f"Devin API Error {resp.status} for {file_path}")
            return None

    async def get_session_status(self, session_id: str) -> dict:
        """Directly queries the Devin API using the required devin- prefix."""
        devin_id = f"devin-{session_id}" if not session_id.startswith(
            "devin-") else session_id
        url = f"{self.base_url}/{devin_id}"
        session = await self._get_session()

        async with session.get(url, headers=self.headers) as resp:
            if resp.status == 200:
                return await resp.json()
            logger.error(f"Status read failed for {devin_id}: {resp.status}")
            return {}

    async def delete_devin_session(self, session_id: str) -> bool:
        """Kills the session using the documented DELETE method to save ACUs."""
        devin_id = f"devin-{session_id}" if not session_id.startswith(
            "devin-") else session_id
        url = f"{self.base_url}/{devin_id}"
        session = await self._get_session()

        async with session.delete(url, headers=self.headers) as resp:
            success = resp.status == 200
            if not success:
                logger.error(
                    f"[ACU LEAK WARNING] Failed to delete {devin_id}. Status: {resp.status}")
            return success

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
