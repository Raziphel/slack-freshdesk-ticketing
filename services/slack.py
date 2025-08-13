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
        log.error("❌ Slack %s error: %s", method, data)
        raise RuntimeError(data)
    return data
