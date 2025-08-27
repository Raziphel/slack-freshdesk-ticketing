import hashlib, logging
from config import FRESHDESK_EMAIL, IT_GROUP_ID, FORM_NAME_TO_TYPE
from services.freshdesk import fd_get, get_form_detail
from logic.mapping import (
    extract_input,
    ensure_choices,
    get_field_choices,
    iter_choice_items,
)

log = logging.getLogger(__name__)

def resolve_proxy_value(field_name: str, proxy_or_value: str) -> str:
    # A hash for long values, this resolves it back if possible.
    val = str(proxy_or_value or "")
    if not val.startswith("hash:"):
        return val

    fd_fields = fd_get("/api/v2/admin/ticket_fields")
    target = next((f for f in fd_fields if f.get("name") == field_name), None)
    if not target:
        log.warning("Could not resolve proxy for field %s: field not found", field_name)
        return val

    ensure_choices(target)
    choices = get_field_choices(target) or {}
    items = list(iter_choice_items(choices))

    want = val[5:]
    for raw_value, _lbl in items:
        h = hashlib.sha1(str(raw_value).encode("utf-8")).hexdigest()
        if h == want:
            return str(raw_value)

    log.warning("Could not resolve proxy for field %s: no choice matched hash", field_name)
    return val

def modal_values_to_fd_ticket(values: dict, ticket_form_id: int | None, requester_email: str | None = None):
    subject = None
    description = None
    type_field = None
    custom_fields = {}

    for block_id, entry in values.items():
        val = extract_input(entry)
        if block_id == "subject":
            subject = val
        elif block_id == "description":
            description = val
        elif block_id in {"type", "ticket_type", "default_ticket_type"}:
            type_field = val
        else:
            if val is not None and val != "__noop__":
                if isinstance(val, str) and val.startswith("hash:"):
                    val = resolve_proxy_value(block_id, val)
                custom_fields[block_id] = val

    if not type_field and ticket_form_id:
        try:
            form = get_form_detail(int(ticket_form_id))
            if isinstance(form, dict):
                name = form.get("name")
                type_field = FORM_NAME_TO_TYPE.get(name, name)
        except Exception as e:
            log.warning("Could not derive type from form %s: %s", ticket_form_id, e)

    ticket = {
        "subject": subject or "New ticket",
        "description": description or "(no description)",
        "status": 2,
        "priority": 2,
        "email": requester_email or FRESHDESK_EMAIL,
        "tags": ["slack", "it-ticket"],
    }
    if IT_GROUP_ID:
        try:
            ticket["group_id"] = int(IT_GROUP_ID)
        except ValueError:
            log.warning("⚠️ IT_GROUP_ID not an int; skipping")

    if type_field:
        ticket["type"] = type_field

    if custom_fields:
        ticket["custom_fields"] = custom_fields
    # FD ignores ticket_form_id, so tags and field values do the routing.
    return ticket
