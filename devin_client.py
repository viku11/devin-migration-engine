import asyncio
import aiohttp
import os
import time
from dotenv import load_dotenv

load_dotenv()

# Configuration
DEVIN_API_KEY = os.getenv("DEVIN_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
ORG_ID = "org-0e2b658611d540688a1cba439bc03d04"
REPO_OWNER = "viku11"
REPO_NAME = "idurar-erp-crm"
BASE_URL = "https://api.devin.ai/v3/organizations"

headers = {
    "Authorization": f"Bearer {DEVIN_API_KEY}",
    "Content-Type": "application/json"
}

gh_headers = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}


async def create_devin_session(file_path, prompt):
    """Dispatches the agent via V3 API."""
    url = f"{BASE_URL}/{ORG_ID}/sessions"
    payload = {
        "prompt": prompt,
        "snapshot_id": None  # Uses latest repo state
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data['session_id']
            elif resp.status == 429:
                return "RATE_LIMIT"
            return None


async def stop_devin_session(session_id):
    """Explicitly kills the container to free the slot."""
    url = f"{BASE_URL}/{ORG_ID}/sessions/{session_id}/stop"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers) as resp:
            return resp.status == 200


async def check_github_for_pr(branch_name):
    """Polls GitHub to see if the PR for the specific branch exists."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls"
    params = {"head": f"{REPO_OWNER}:{branch_name}", "state": "open"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=gh_headers, params=params) as resp:
            if resp.status == 200:
                prs = await resp.json()
                return prs[0] if prs else None
            return None


async def merge_github_pr(pr_number):
    """The 'Principal Move': Automatically merges the PR once detected."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls/{pr_number}/merge"
    payload = {"merge_method": "squash",
               "commit_title": f"Auto-merge PR #{pr_number}"}

    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=gh_headers, json=payload) as resp:
            return resp.status == 200
