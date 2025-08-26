from __future__ import annotations
import time, uuid, logging
from config import MAX_BLOCKS
from services.freshdesk import (
    get_form_detail,
    get_ticket_forms_cached,
    get_ticket_fields_cached,
)
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

    pages: list[int | str | None] = []
    visited: set[int] = set()
    active_sections: set[int] = set()

    def add_field_and_children(fid: int) -> bool:
        if fid in visited:
            return True
        f = by_id.get(fid)
        if not f or f.get("type") in {"default_subject", "default_description"}:
            visited.add(fid)
            return True
        ensure_choices(f)
        sec_ids = {
            int(m.get("section_id"))
            for m in (f.get("section_mappings") or [])
            if m.get("section_id")
        }
        if sec_ids and not sec_ids.issubset(active_sections):
            return True
        if not normalize_blocks(to_slack_block(f)):
            visited.add(fid)
            return True
        visited.add(fid)
        pages.append(fid)
        # ``nested_field`` objects act as containers that render their
        # dependent fields but do not store an answer themselves. Waiting for
        # a value that never arrives causes the wizard to stop early after the
        # nested block. We therefore skip the "expect an answer" step for these
        # container fields so that subsequent questions continue to appear.
        if f.get("type") != "nested_field":
            selected = selected_value_for(f, state_values)
            if selected is None:
                return False
            sel = str(selected)
            for sec in get_sections_cached(fid):
                if sel not in activator_values(sec):
                    continue
                sid = sec.get("id")
                if sid is not None:
                    try:
                        active_sections.add(int(sid))
                    except (TypeError, ValueError):
                        pass
                for child_id in normalize_id_list(sec.get("fields") or []):
                    if not add_field_and_children(child_id):
                        return False
                if sid is not None:
                    try:
                        active_sections.discard(int(sid))
                    except (TypeError, ValueError):
                        pass
            return True

        # Nested fields don't have conditional sections at this level; their
        # dependent inputs are already included in the blocks returned by
        # ``to_slack_block``.
        return True

    for fid in id_order:
        if not add_field_and_children(fid):
            break

    pages.append("core")
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
        forms = get_ticket_forms_cached()
        fd_fields = get_ticket_fields_cached()
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
        # Merge incoming view state
        sess["values"] = {**(sess.get("values") or {}), **(new_state_values or {})}

        forms = get_ticket_forms_cached()
        fd_fields = get_ticket_fields_cached()
        form = next((f for f in forms if str(f["id"]) == str(sess["ticket_form_id"])), None)
        if not form:
            raise RuntimeError("Form not found for wizard session")

        # Determine current page item and compute navigation relative to
        # the freshly generated page sequence. This avoids glitches where
        # unrelated fields appear or pages repeat when conditional
        # branches change.
        pages = compute_pages(form, fd_fields, sess["values"])
        # Drop stale answers for fields no longer in the current flow so
        # that unrelated branches are ignored. Recompute pages after
        # trimming to reflect any removed branches.
        by_id = {f.get("id"): f for f in fd_fields}
        valid_names = set()
        for item in pages:
            if isinstance(item, int):
                f = by_id.get(item)
                if f and f.get("name"):
                    valid_names.add(f["name"])
        sess["values"] = {k: v for k, v in sess["values"].items() if k in valid_names}
        pages = compute_pages(form, fd_fields, sess["values"])

        page = max(0, min(int(sess.get("page", 0)), len(pages) - 1))
        current_item = pages[page]

        if nav == "next":
            # Don't advance unless the current field has a value when the
            # page represents a specific field id.
            allow_advance = True
            if isinstance(current_item, int):
                field_obj = next((f for f in fd_fields if f.get("id") == current_item), None)
                if field_obj and selected_value_for(field_obj, sess["values"]) is None:
                    allow_advance = False
            if allow_advance:
                page = min(page + 1, len(pages) - 1)
        elif nav == "prev":
            page = max(page - 1, 0)

        sess["page"] = page

        view = build_wizard_page_modal(form, fd_fields, token, page, sess["values"])
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
