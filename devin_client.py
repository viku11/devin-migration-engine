import aiohttp
import os
import logging
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class DevinClient:
    """Manages API connections to Devin and GitHub using a shared connection pool."""

    def __init__(self):
        self.api_key = os.getenv("DEVIN_API_KEY")
        self.gh_token = os.getenv("GITHUB_TOKEN")

        if not self.api_key or not self.gh_token:
            raise EnvironmentError(
                "CRITICAL: DEVIN_API_KEY or GITHUB_TOKEN missing from environment.")

        self.org_id = "org-0e2b658611d540688a1cba439bc03d04"
        self.repo_owner = "viku11"
        self.repo_name = "idurar-erp-crm"
        self.base_url = f"https://api.devin.ai/v3/organizations/{self.org_id}/sessions"

        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        self.gh_headers = {
            "Authorization": f"token {self.gh_token}",
            "Accept": "application/vnd.github.v3+json"
        }
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Returns the active HTTP session or creates a new one."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def create_devin_session(self, file_path: str, prompt: str) -> Optional[str]:
        """Dispatches the agent via V3 API."""
        payload = {"prompt": prompt, "snapshot_id": None}
        session = await self._get_session()

        async with session.post(self.base_url, headers=self.headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get('session_id')
            elif resp.status == 429:
                return "RATE_LIMIT"

            logger.error(f"Devin API Error {resp.status} for {file_path}")
            return None

    async def stop_devin_session(self, session_id: str) -> bool:
        """Explicitly kills the container to free the slot."""
        url = f"{self.base_url}/{session_id}/stop"
        session = await self._get_session()

        async with session.post(url, headers=self.headers) as resp:
            return resp.status == 200

    async def check_github_for_pr(self, branch_name: str) -> Optional[Dict[str, Any]]:
        """Polls GitHub to see if the PR for the specific branch exists."""
        url = f"https://api.github.com/repos/{self.repo_owner}/{self.repo_name}/pulls"

        # 'state': 'all' catches PRs even if a human already reviewed and merged them
        params = {"head": f"{self.repo_owner}:{branch_name}", "state": "all"}
        session = await self._get_session()

        async with session.get(url, headers=self.gh_headers, params=params) as resp:
            if resp.status == 200:
                prs = await resp.json()
                return prs[0] if prs else None
            return None

    async def close(self):
        """Cleanly closes the network socket."""
        if self.session and not self.session.closed:
            await self.session.close()
