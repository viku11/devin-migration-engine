import requests
import os
from dotenv import load_dotenv

load_dotenv()

key = os.getenv('DEVIN_API_KEY')
org = os.getenv('DEVIN_ORG_ID')
headers = {'Authorization': f'Bearer {key}'}


def terminate_zombies():
    all_sessions = []
    cursor = None
    print("Fetching sessions from Devin API...")

    while True:
        params = {"limit": 50}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(
            f'https://api.devin.ai/v3/organizations/{org}/sessions',
            headers=headers, params=params
        )
        data = resp.json()
        all_sessions.extend(data.get('items', []))
        if not data.get('has_next_page'):
            break
        cursor = data.get('end_cursor')

    running = [s for s in all_sessions if s['status'].lower() == 'running']
    print(f"Found {len(running)} zombie sessions. Terminating...")

    for s in running:
        sid = s['session_id']
        # The standard command to stop an active agent session
        stop_url = f'https://api.devin.ai/v3/organizations/{org}/sessions/{sid}/stop'
        requests.post(stop_url, headers=headers)
        print(f"  [STOPPED] {sid[:8]}")

    print("Zombies cleared. Concurrency slots are now free.")


if __name__ == "__main__":
    terminate_zombies()
