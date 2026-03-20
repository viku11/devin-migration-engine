import requests
import os
from dotenv import load_dotenv
load_dotenv()

key = os.getenv('DEVIN_API_KEY')
org = os.getenv('DEVIN_ORG_ID')

resp = requests.get(
    f'https://api.devin.ai/v3/organizations/{org}/sessions',
    headers={'Authorization': f'Bearer {key}'}
)
data = resp.json()
print(f'Total sessions: {data["total"]}')
for s in data['items'][:10]:
    print(f'{s["status"]:12} https://app.devin.ai/sessions/{s["session_id"]}')
