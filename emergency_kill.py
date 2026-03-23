import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("DEVIN_API_KEY")
ORG_ID = os.getenv("DEVIN_ORG_ID", "org-0e2b658611d540688a1cba439bc03d04")
BASE_URL = f"https://api.devin.ai/v3/organizations/{ORG_ID}/sessions"

headers = {"Authorization": f"Bearer {API_KEY}"}

print("🚨 EMERGENCY ABORT: Fetching active sessions...")
resp = requests.get(BASE_URL, headers=headers).json()
sessions = resp if isinstance(resp, list) else resp.get('data', [])

killed = 0
for s in sessions:
    if s.get("status") in ["running", "starting", "queued"]:
        sid = s.get("session_id")
        print(f"🔪 Killing session: {sid}")
        requests.delete(f"{BASE_URL}/{sid}", headers=headers)
        killed += 1

print(f"\n✅ Crisis averted. Killed {killed} rogue sessions.")
