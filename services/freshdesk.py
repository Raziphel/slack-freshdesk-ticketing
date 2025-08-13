from __future__ import annotations
import requests
import logging
from config import FRESHDESK_DOMAIN, FRESHDESK_API_KEY, HTTP_TIMEOUT

log = logging.getLogger(__name__)

def fd_get(path: str):
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    r = requests.get(url, auth=(FRESHDESK_API_KEY, "X"), timeout=HTTP_TIMEOUT)
    if not r.ok:
        log.error("❌ FD GET %s -> %s", path, r.text[:800])
    r.raise_for_status()
    return r.json()

def fd_post(path: str, payload: dict):
    url = f"https://{FRESHDESK_DOMAIN}.freshdesk.com{path}"
    r = requests.post(url, auth=(FRESHDESK_API_KEY, "X"), json=payload, timeout=HTTP_TIMEOUT)
    if not r.ok:
        log.error("❌ FD POST %s -> %s", path, r.text[:800])
    r.raise_for_status()
    return r.json()

def get_form_detail(form_id: int):
    return fd_get(f"/api/v2/ticket-forms/{form_id}")

def get_sections(field_id: int):
    try:
        return fd_get(f"/api/v2/admin/ticket_fields/{field_id}/sections") or []
    except Exception as e:
        logging.info("No sections for field %s (%s)", field_id, e)
        return []

def fetch_field_detail(field_id: int) -> dict | None:
    try:
        return fd_get(f"/api/v2/admin/ticket_fields/{field_id}")
    except Exception as e:
        logging.info("No detail for field %s (%s)", field_id, e)
        return None
