from dotenv import load_dotenv
import os, requests
load_dotenv()

FRESHDESK_DOMAIN = os.getenv("FRESHDESK_DOMAIN")
FRESHDESK_API_KEY = os.getenv("FRESHDESK_API_KEY")

def fd_get(path):
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    r = requests.get(url, auth=(FRESHDESK_API_KEY, "X"))
    print(r.status_code, r.text[:500])
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    print(fd_get("/api/v2/ticket-forms"))
    print(fd_get("/api/v2/admin/ticket_fields"))
