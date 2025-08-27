import os
import requests
from dotenv import load_dotenv
load_dotenv()

# Quick helper script for me to dump field metadata when I'm confused.
domain = os.getenv("FRESHDESK_DOMAIN")
api_key = os.getenv("FRESHDESK_API_KEY")

url = f"https://{domain}.freshdesk.com/api/v2/admin/ticket_fields"
resp = requests.get(url, auth=(api_key, "X"))
resp.raise_for_status()

with open("ticket_fields.json", "w", encoding="utf-8") as f:
    f.write(resp.text)

print("✅ Saved to ticket_fields.json")
