import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DEVIN_API_KEY")
ORG_ID = os.getenv("DEVIN_ORG_ID")
GITHUB_REPO = os.getenv("GITHUB_REPO")

if not API_KEY or not ORG_ID:
    raise RuntimeError("❌ Missing DEVIN_API_KEY or DEVIN_ORG_ID in .env")

BASE_URL = f"https://api.devin.ai/v3/organizations/{ORG_ID}/sessions"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}


def cleanup_inactive_sessions():
    print(f"🔍 Fetching Devin sessions for organization: {ORG_ID} (repo: {GITHUB_REPO or 'ALL'})...")

    response = requests.get(BASE_URL, headers=headers)
    if response.status_code != 200:
        print(f"❌ API Error {response.status_code}: {response.text}")
        return

    data = response.json()
    # Handle standard list or wrapped data array
    sessions = data if isinstance(data, list) else data.get('data', [])

    if not sessions:
        print("⚪ No sessions found.")
        return

    print(f"📊 Found {len(sessions)} total sessions. Analyzing statuses...")
    deleted_count = 0

    for session in sessions:
        session_id = session.get("session_id")
        status = session.get("status", "unknown")

        # We only want to delete sessions that are no longer actively running
        if status in ["stopped", "error", "succeeded", "sleeping"]:
            print(f"🗑️ Deleting session {session_id} (Status: {status})...")

            delete_url = f"{BASE_URL}/{session_id}"
            del_response = requests.delete(delete_url, headers=headers)

            if del_response.status_code == 200:
                deleted_count += 1
            else:
                print(
                    f"⚠️ Failed to delete {session_id}: {del_response.status_code}")

    print(
        f"\n✅ Cleanup Complete! Successfully removed {deleted_count} inactive sessions.")


if __name__ == "__main__":
    # WARNING: This will delete all non-running sessions in your Devin account.
    user_input = input(
        "Are you sure you want to delete all inactive sessions? (y/n): ")
    if user_input.lower() == 'y':
        cleanup_inactive_sessions()
    else:
        print("Cleanup aborted.")
