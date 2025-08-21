import requests
import logging
from config import SLACK_BOT_TOKEN, HTTP_TIMEOUT

log = logging.getLogger(__name__)

def slack_api(method: str, payload: dict):
    r = requests.post(
        f"https://slack.com/api/{method}",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=HTTP_TIMEOUT
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        if data.get("error") == "hash_conflict":
            # Another process updated the view; caller will retry without hash.
            log.debug("Slack %s hash_conflict: %s", method, data)
        else:
            log.error("❌ Slack %s error: %s", method, data)
        raise RuntimeError(data)
    return data
