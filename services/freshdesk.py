from __future__ import annotations
import time
import json
from pathlib import Path
import requests
import logging
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from config import FRESHDESK_DOMAIN, FRESHDESK_API_KEY, HTTP_TIMEOUT, PORTAL_TICKET_FORM_URL

log = logging.getLogger(__name__)


_session = requests.Session()
_session.auth = (FRESHDESK_API_KEY, "X")
_session.mount("https://", HTTPAdapter(pool_connections=20, pool_maxsize=20))


def fd_get(path: str):
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    r = _session.get(url, timeout=HTTP_TIMEOUT)
    if not r.ok:
        log.error("❌ FD GET %s -> %s", path, r.text[:800])
    r.raise_for_status()
    return r.json()


def fd_post(path: str, payload: dict):
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    r = _session.post(url, json=payload, timeout=HTTP_TIMEOUT)
    if not r.ok:
        log.error("❌ FD POST %s -> %s", path, r.text[:800])
    r.raise_for_status()
    return r.json()


# --- Portal scraping ------------------------------------------------------

def _scrape_portal_fields() -> list[dict]:
    """Parse ticket fields and conditional sections from the portal form.

    Freshdesk's public portal renders dynamic forms entirely in HTML. Drop-down
    choices that reveal additional questions store the dependent section HTML in
    hidden ``textarea.picklist_section_*`` elements. This helper fetches the
    portal form, walks the DOM and reconstructs a simplified representation of
    the question flow including any nested sections.
    """

    def parse_container(container: BeautifulSoup) -> list[dict]:
        results: list[dict] = []
        for group in container.find_all("div", class_="control-group", recursive=False):
            label = group.find("label")
            if not label:
                continue
            field_id = label.get("for")
            if not field_id:
                continue
            input_el = group.find(id=field_id)
            if input_el is None:
                continue

            name = input_el.get("name") or field_id
            field_type = input_el.name
            required = "required" in (label.get("class") or []) or "required" in (
                input_el.get("class") or []
            )

            field: dict[str, object] = {
                "id": name,
                "label": label.get_text(strip=True),
                "type": field_type,
                "required": required,
            }

            if field_type == "select":
                choices = []
                for opt in input_el.find_all("option"):
                    value = opt.get("value")
                    if value is None:
                        continue
                    choice = {
                        "value": value,
                        "label": opt.get_text(strip=True),
                    }
                    data_id = opt.get("data-id")
                    if data_id:
                        textarea = container.find(
                            "textarea", class_=f"picklist_section_{data_id}"
                        )
                        if textarea and textarea.text:
                            inner = BeautifulSoup(textarea.text, "html.parser")
                            nested = parse_container(inner)
                            if nested:
                                choice["section"] = nested
                    choices.append(choice)
                field["choices"] = choices

            results.append(field)

        return results

    try:
        resp = requests.get(PORTAL_TICKET_FORM_URL, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log.error("❌ Failed to fetch portal form %s (%s)", PORTAL_TICKET_FORM_URL, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form")
    if not form:
        return []

    return parse_container(form)


# --- Cached helpers ------------------------------------------------------
_FORMS_CACHE: dict[str, object] = {"expires": 0, "data": []}
_FIELDS_CACHE: dict[str, object] = {"expires": 0, "data": []}

# Attempt to warm the fields cache from the bundled JSON snapshot. If the
# file exists we load it as a starting point but still refresh from the live
# API on first use so that updates in Freshdesk are reflected in the question
# flow. Missing or malformed files simply fall back to the live API.
_FIELDS_FILE = Path(__file__).resolve().parent.parent / "ticket_fields.json"
if _FIELDS_FILE.exists():
    try:
        with _FIELDS_FILE.open("r", encoding="utf-8") as fh:
            _FIELDS_CACHE["data"] = json.load(fh)
            # expire immediately so fresh data is fetched on first access
            _FIELDS_CACHE["expires"] = 0
            log.info("Loaded %d ticket fields from %s", len(_FIELDS_CACHE["data"]), _FIELDS_FILE)
    except Exception as e:  # pragma: no cover - best effort only
        log.warning("Failed to load %s: %s", _FIELDS_FILE, e)


def get_ticket_forms_cached(ttl: int = 300):
    now = time.time()
    if now >= _FORMS_CACHE["expires"]:
        _FORMS_CACHE["data"] = fd_get("/api/v2/ticket-forms")
        _FORMS_CACHE["expires"] = now + ttl
    return _FORMS_CACHE["data"]


def get_ticket_fields_cached(ttl: int = 300):
    now = time.time()
    if now >= _FIELDS_CACHE["expires"]:
        try:
            _FIELDS_CACHE["data"] = fd_get("/api/v2/admin/ticket_fields")
        except Exception as e:
            log.warning("Ticket fields API failed (%s); falling back to portal scrape", e)
            _FIELDS_CACHE["data"] = _scrape_portal_fields()
        _FIELDS_CACHE["expires"] = now + ttl
    return _FIELDS_CACHE["data"]


def get_form_detail(form_id: int):
    return fd_get(f"/api/v2/ticket-forms/{form_id}")


def get_sections(field_id: int):
    path = f"/api/v2/admin/ticket_fields/{field_id}/sections"
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    try:
        r = _session.get(url, timeout=HTTP_TIMEOUT)
        if not r.ok:
            # Freshdesk returns 400/404/422 when a field has no
            # conditional sections configured. These responses are
            # expected during wizard traversal so we quietly treat
            # them as "no sections" instead of logging noisy errors.
            log.debug("No sections for field %s (%s)", field_id, r.status_code)
            return []
        return r.json() or []
    except Exception as e:
        log.debug("No sections for field %s (%s)", field_id, e)
        return []


def fetch_field_detail(field_id: int) -> dict | None:
    try:
        return fd_get(f"/api/v2/admin/ticket_fields/{field_id}")
    except Exception as e:
        logging.info("No detail for field %s (%s)", field_id, e)
        return None
