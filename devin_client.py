import requests
import time
import os
import re
import random
from dotenv import load_dotenv

load_dotenv()

# Configuration
DEVIN_API_KEY = os.getenv("DEVIN_API_KEY")
DEVIN_ORG_ID = os.getenv("DEVIN_ORG_ID")
GITHUB_REPO = os.getenv("GITHUB_REPO", "viku11/idurar-erp-crm")

BASE_URL = f"https://api.devin.ai/v3/organizations/{DEVIN_ORG_ID}"
HEADERS = {
    "Authorization": f"Bearer {DEVIN_API_KEY}",
    "Content-Type": "application/json"
}

TERMINAL_STATES = ["finished", "failed", "suspended", "stopped"]


def extract_pr_url(session_data: dict) -> str:
    """Safely extracts a GitHub PR URL from session metadata or structured output."""
    # Check structured PR field first
    pr_info = session_data.get("pull_request", {})
    if pr_info and pr_info.get("url"):
        return pr_info["url"]

    # Fallback: Regex scan through the session summary or last message
    text_to_scan = str(session_data.get("summary", "")) + \
        str(session_data.get("status_description", ""))
    match = re.search(
        r"https://github\.com/[^/]+/[^/]+/pull/\d+", text_to_scan)
    return match.group(0) if match else None


def stop_session(session_id: str):
    """
    Explicitly terminates a session. 
    Crucial for releasing organization concurrency slots immediately.
    """
    try:
        url = f"{BASE_URL}/sessions/{session_id}/stop"
        resp = requests.post(url, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            print(
                f"    [CLEANUP] Session {session_id[:8]} stopped successfully.")
        else:
            print(
                f"    [CLEANUP] Session {session_id[:8]} already terminal or not found.")
    except Exception as e:
        print(f"    [WARN] Resource cleanup failed for {session_id[:8]}: {e}")


def create_session(filepath: str, relative_path: str) -> dict:
    """Creates a session with jittered exponential backoff for 429 errors."""
    prompt = build_migration_prompt(filepath, relative_path)

    for attempt in range(5):
        try:
            resp = requests.post(
                f"{BASE_URL}/sessions",
                headers=HEADERS,
                json={"prompt": prompt},
                timeout=30
            )

            if resp.status_code == 429:
                # Calculate backoff: 8s, 16s, 32s... + random jitter
                wait = (2 ** (attempt + 3)) + random.uniform(1, 5)
                print(
                    f"    [RATE LIMIT] 429 received. Backing off {wait:.1f}s...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            return {
                "session_id": data.get("session_id"),
                "url": data.get("url")
            }
        except requests.exceptions.RequestException as e:
            if attempt == 4:
                raise RuntimeError(f"API Connection Failure: {e}")
            time.sleep(5)

    raise RuntimeError("Failed to create session after maximum retries.")


def poll_until_terminal(
    session_id: str,
    timeout_seconds: int = 3600,
    poll_interval: int = 30
) -> dict:
    """
    Proactive Polling: 
    Returns success the moment a PR URL is detected, then kills the session.
    """
    start_time = time.time()

    while (time.time() - start_time) < timeout_seconds:
        try:
            resp = requests.get(
                f"{BASE_URL}/sessions/{session_id}", headers=HEADERS, timeout=10)
            resp.raise_for_status()
            session = resp.json()

            status = session.get("status", "").lower()
            pr_url = extract_pr_url(session)

            # --- SUCCESS CONDITION ---
            if pr_url and "github.com" in pr_url:
                print(f"    [SUCCESS] PR detected: {pr_url}")
                # RESOURCE MANAGEMENT: Kill the VM now that the goal is met
                stop_session(session_id)
                session["status"] = "finished"
                return session

            # --- NATIVE TERMINAL CONDITION ---
            if status in TERMINAL_STATES:
                return session

        except Exception as e:
            print(f"    [POLL ERR] {session_id[:8]}: {e}")

        time.sleep(poll_interval)

    # --- TIMEOUT CONDITION ---
    print(
        f"    [TIMEOUT] {session_id[:8]} exceeded {timeout_seconds}s. Terminating.")
    stop_session(session_id)
    return {"status": "failed", "session_id": session_id}


def build_migration_prompt(filepath: str, relative_path: str) -> str:
    filename = os.path.basename(filepath)
    tsx_filename = filename.replace(".jsx", ".tsx")

    return f"""
# Task: Migrate React component to TypeScript
File: `{relative_path}`

## Instructions:
1. Rename `{filename}` to `{tsx_filename}`.
2. Add necessary TypeScript types/interfaces.
3. Ensure zero errors with `npx tsc --noEmit`.
4. Create a branch `migrate/{relative_path.replace('/', '-').replace('.jsx', '')}`.
5. Push changes and open a Pull Request to `master`.

## Exit Strategy (CRITICAL):
This is an automated batch task. Once you have opened the Pull Request:
- Do NOT wait for code review.
- Do NOT ask for further instructions.
- You MUST use the `finish` tool immediately to terminate the session.
"""


def get_session(session_id: str) -> dict:
    resp = requests.get(f"{BASE_URL}/sessions/{session_id}",
                        headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.json()
