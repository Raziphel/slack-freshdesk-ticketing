import requests
import logging
from functools import lru_cache
from requests.adapters import HTTPAdapter
from config import SLACK_BOT_TOKEN, HTTP_TIMEOUT

log = logging.getLogger(__name__)

# Reusing one session so Slack isn't opening a new connection each time.
_session = requests.Session()
_session.headers.update({"Authorization": f"Bearer {SLACK_BOT_TOKEN}", "Content-Type": "application/json"})
_session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))


def slack_api(method: str, payload: dict):
    # My thin wrapper around Slack's API; keeps things consistent.
    r = _session.post(
        f"https://slack.com/api/{method}",
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


@lru_cache(maxsize=512)
def get_user_email(user_id: str) -> str | None:
    """Return the email address for a Slack user, if available.

    Slack workspaces can hide email addresses unless the app has the
    ``users:read.email`` scope.  Some workspaces, however, expose the email via
    ``users.profile.get`` even when ``users.info`` omits it.  To maximize the
    chances of retrieving the address we try ``users.info`` first and fall back
    to ``users.profile.get`` if necessary.
    """
    try:
        info = slack_api("users.info", {"user": user_id})
        email = ((info.get("user") or {}).get("profile") or {}).get("email")
        if email:
            return email
        profile = slack_api("users.profile.get", {"user": user_id})
        return (profile.get("profile") or {}).get("email")
    except Exception as e:
        log.warning("Could not fetch email for %s: %s", user_id, e)
        return None
