from __future__ import annotations
import time, uuid, logging
from config import MAX_BLOCKS
from services.freshdesk import get_form_detail, fd_get
from services.slack import slack_api
from logic.forms import normalize_id_list
from logic.mapping import to_slack_block, normalize_blocks, ensure_choices
from logic.branching import get_sections_cached, activator_values, selected_value_for

log = logging.getLogger(__name__)

WIZARD_SESSIONS: dict[str, dict] = {}  # token -> {"ticket_form_id":int, "page":int, "values":dict}

def compute_pages(form: dict, all_fields: list, state_values: dict):
    """Compute the sequence of wizard pages.

    Pages are generated dynamically based on answered values. Each
    field occupies its own page and any conditional children are added
    after their parent once the triggering value has been supplied. A
    trailing ``None`` sentinel marks the final submission step.
    """

    form_detail = get_form_detail(int(form["id"]))
    raw = form_detail.get("fields") or form.get("fields") or []
    id_order = normalize_id_list(raw)
    by_id = {f["id"]: f for f in all_fields}

    pages: list[int | str | None] = ["core"]
    visited: set[int] = set()

    def add_field_and_children(fid: int):
        if fid in visited:
            return
        visited.add(fid)
        f = by_id.get(fid)
        if not f or f.get("type") in {"default_subject","default_description"}:
            return
        ensure_choices(f)
        if not normalize_blocks(to_slack_block(f)):
            return
        pages.append(fid)
        selected = selected_value_for(f, state_values)
        if selected is None:
            return
        sel = str(selected)
        for sec in get_sections_cached(fid):
            if sel not in activator_values(sec):
                continue
            for child_id in normalize_id_list(sec.get("fields") or []):
                add_field_and_children(child_id)

    for fid in id_order:
        add_field_and_children(fid)

    pages.append(None)
    log.info("Wizard pages for %s: %s", form.get("name") or form.get("id"), pages)
    return pages

def build_fields_for_page(form: dict, all_fields: list, state_values: dict, page_item: int | str | None):
    """Build Slack blocks for a given page item.

    ``page_item`` may be ``"core"`` for the subject/description step, an
    integer field id for a single question, or ``None`` for the final
    submission step.
    """

    by_id = {f["id"]: f for f in all_fields}

    if page_item == "core":
        blocks = []
        subj = next((f for f in all_fields if f.get("type") == "default_subject"), None)
        desc = next((f for f in all_fields if f.get("type") == "default_description"), None)
        for core in (subj, desc):
            if core:
                blocks.extend(normalize_blocks(to_slack_block(core)))
        if not blocks:
            blocks.append({"type":"section","text":{"type":"mrkdwn","text":"_No core fields._"}})
        return blocks[:MAX_BLOCKS]

    if page_item is None:
        return [{"type":"section","text":{"type":"mrkdwn","text":"_No more questions._"}}]

    field_obj = by_id.get(page_item)
    if not field_obj:
        return [{"type":"section","text":{"type":"mrkdwn","text":"_Field not found._"}}]

    ensure_choices(field_obj)
    return normalize_blocks(to_slack_block(field_obj))[:MAX_BLOCKS]

def build_wizard_page_modal(form: dict, all_fields: list, token: str, page: int, state_values: dict):
    pages = compute_pages(form, all_fields, state_values)
    total = len(pages)
    page = max(0, min(page, total - 1))
    page_item = pages[page]

    fields_blocks = build_fields_for_page(form, all_fields, state_values, page_item)

    nav_elems = []
    if page > 0:
        nav_elems.append({
            "type": "button",
            "action_id": "wizard_prev",
            "text": {"type": "plain_text", "text": "Back"},
            "value": token,
        })
    if page < total - 1:
        nav_elems.append({
            "type": "button",
            "action_id": "wizard_next",
            "text": {"type": "plain_text", "text": "Next"},
            "style": "primary",
            "value": token,
        })

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*Step {page+1} of {total}*"}}]
    blocks.extend(fields_blocks)
    if nav_elems:
        blocks.append({"type": "actions", "block_id": "wizard_nav", "elements": nav_elems})

    view = {
        "type": "modal",
        "callback_id": "wizard_submit" if page_item is None else "wizard_page",
        "title": {"type": "plain_text", "text": (form.get("name") or "New IT Ticket")[:24]},
        "close": {"type": "plain_text", "text": "Cancel" if page == 0 else "Close"},
        "blocks": blocks,
        "private_metadata": _json_dumps({"ticket_form_id": form["id"], "wizard_token": token, "page_index": page}),
    }
    if page_item is None:
        view["submit"] = {"type": "plain_text", "text": "Create"}
    return view

# helpers used by routes (async flows)
def open_wizard_first_page(view_id: str, ticket_form_id: int, view_hash: str | None):
    try:
        forms = fd_get("/api/v2/ticket-forms")
        fd_fields = fd_get("/api/v2/admin/ticket_fields")
        form = next((f for f in forms if str(f["id"]) == str(ticket_form_id)), None)
        if not form:
            raise RuntimeError(f"Form {ticket_form_id} not found")

        token = uuid.uuid4().hex
        WIZARD_SESSIONS[token] = {"ticket_form_id": ticket_form_id, "page": 0, "values": {}}

        view = build_wizard_page_modal(form, fd_fields, token, 0, {})
        try:
            slack_api("views.update", {"view_id": view_id, "hash": view_hash, "view": view})
        except RuntimeError as e:
            data = e.args[0] if e.args else {}
            if isinstance(data, dict) and data.get("error") == "hash_conflict":
                time.sleep(0.15)
                slack_api("views.update", {"view_id": view_id, "view": view})
            else:
                raise
    except Exception as e:
        log.exception("Wizard open failed: %s", e)

def update_wizard(view_id: str, token: str, view_hash: str | None, new_state_values: dict | None, nav: str | None = None):
    try:
        sess = WIZARD_SESSIONS.get(token)
        if not sess:
            raise RuntimeError("Wizard session expired")
        sess["values"] = {**(sess.get("values") or {}), **(new_state_values or {})}
        page = int(sess.get("page", 0))
        if nav == "next": page += 1
        elif nav == "prev": page -= 1
        sess["page"] = max(0, page)

        forms = fd_get("/api/v2/ticket-forms")
        fd_fields = fd_get("/api/v2/admin/ticket_fields")
        form = next((f for f in forms if str(f["id"]) == str(sess["ticket_form_id"])), None)
        if not form:
            raise RuntimeError("Form not found for wizard session")

        view = build_wizard_page_modal(form, fd_fields, token, sess["page"], sess["values"])
        try:
            slack_api("views.update", {"view_id": view_id, "hash": view_hash, "view": view})
        except RuntimeError as e:
            data = e.args[0] if e.args else {}
            if isinstance(data, dict) and data.get("error") == "hash_conflict":
                time.sleep(0.15)
                slack_api("views.update", {"view_id": view_id, "view": view})
            else:
                raise
    except Exception as e:
        log.exception("Wizard update failed: %s", e)

def _json_dumps(obj):
    import json
    return json.dumps(obj)
