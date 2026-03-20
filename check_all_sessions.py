import requests
import os
from dotenv import load_dotenv

load_dotenv()

key = os.getenv('DEVIN_API_KEY')
org = os.getenv('DEVIN_ORG_ID')

# Fetch all sessions with pagination
all_sessions = []
cursor = None

while True:
    params = {"limit": 50}
    if cursor:
        params["cursor"] = cursor
    
    resp = requests.get(
        f'https://api.devin.ai/v3/organizations/{org}/sessions',
        headers={'Authorization': f'Bearer {key}'},
        params=params
    )
    data = resp.json()
    all_sessions.extend(data.get('items', []))
    
    if not data.get('has_next_page'):
        break
    cursor = data.get('end_cursor')

# Group by status
from collections import defaultdict
by_status = defaultdict(list)
for s in all_sessions:
    by_status[s['status']].append(s)

print(f"Total sessions: {len(all_sessions)}")
for status, sessions in sorted(by_status.items()):
    print(f"\n{status.upper()} ({len(sessions)}):")
    for s in sessions[:5]:
        print(f"  https://app.devin.ai/sessions/{s['session_id']}")
    if len(sessions) > 5:
        print(f"  ... and {len(sessions)-5} more")
