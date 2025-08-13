from __future__ import annotations
import time, uuid, logging
from config import MAX_BLOCKS, WIZARD_CROSS_SECTION_CHILDREN
from services.freshdesk import get_form_detail, fd_get
from services.slack import slack_api
from logic.forms import normalize_id_list
from logic.mapping import to_slack_block, normalize_blocks, ensure_choices
from logic.branching import get_sections_cached, activator_values, selected_value_for

log = logging.getLogger(__name__)

WIZARD_SESSIONS: dict[str, dict] = {}  # token -> {"ticket_form_id":int, "page":int, "values":dict}

def _field_in_section(field_obj: dict, section_id: int) -> bool:
    maps = field_obj.get("section_mappings") or []
    return any(m.get("section_id") == section_id for m in maps)

def _field_has_section(field_obj: dict) -> bool:
    maps = field_obj.get("section_mappings") or []
    return bool(maps)

def compute_pages(form: dict, all_fields: list):
    form_detail = get_form_detail(int(form["id"]))
    ordered_section_ids = [s["id"] for s in sorted(form_detail.get("sections", []),
                                                   key=lambda x: x.get("position", 9999))]
    if not ordered_section_ids:
        pages = ["core","general"]
        log.info("Wizard pages for %s: %s (no sections; forcing General)", form.get("name") or form.get("id"), pages)
        return pages

    raw = form_detail.get("fields") or form.get("fields") or []
    id_order = normalize_id_list(raw); by_id = {f["id"]: f for f in all_fields}
    has_unsectioned = False
    for fid in id_order:
        f = by_id.get(fid)
        if not f or f.get("type") in {"default_subject","default_description"}:
            continue
        if not f.get("section_mappings"):
            has_unsectioned = True; break

    pages = ["core"]
    if has_unsectioned:
        pages.append("general")
    pages.extend(ordered_section_ids)
    log.info("Wizard pages for %s: %s", form.get("name") or form.get("id"), pages)
    return pages

def build_fields_for_page(form: dict, all_fields: list, state_values: dict, section_id: int | str):
    by_id = {f["id"]: f for f in all_fields}
    form_detail = get_form_detail(int(form["id"]))
    sections_by_id = {s["id"]: s for s in form_detail.get("sections", [])}
    raw = form_detail.get("fields") or form.get("fields") or []
    id_order = normalize_id_list(raw)

    if section_id == "core":
        blocks = []
        subj = next((f for f in all_fields if f.get("type") == "default_subject"), None)
        desc = next((f for f in all_fields if f.get("type") == "default_description"), None)
        for core in (subj, desc):
            if core:
                blocks.extend(normalize_blocks(to_slack_block(core)))
        if not blocks:
            blocks.append({"type":"section","text":{"type":"mrkdwn","text":"_No core fields._"}})
        return blocks[:MAX_BLOCKS]

    def in_this_page(f) -> bool:
        if section_id == "general":
            return not _field_has_section(f)
        return _field_in_section(f, section_id)

    dependent_ids: set[int] = set()
    for fid in id_order:
        for sec in get_sections_cached(fid):
            for cid in sec.get("fields") or []:
                child = by_id.get(cid)
                if child and in_this_page(child):
                    dependent_ids.add(cid)

    fields_here = []
    for fid in id_order:
        f = by_id.get(fid)
        if not f or f.get("type") in {"default_subject", "default_description"}:
            continue
        if in_this_page(f):
            pos = 9999
            maps = f.get("section_mappings") or []
            if maps:
                pos = sorted(maps, key=lambda m: m.get("position", 9999))[0].get("position", 9999)
            fields_here.append((pos, f))
    fields_here.sort(key=lambda t: t[0])

    blocks = []
    if section_id == "general":
        blocks.append({"type":"header","text":{"type":"plain_text","text":"General"}})
    else:
        sec = sections_by_id.get(section_id)
        if sec:
            blocks.append({"type":"header","text":{"type":"plain_text","text":sec.get("name","Section")[:150]}})

    added: set[str] = set()

    def _append_field_tree_in_page(field_obj: dict):
        nonlocal blocks, added
        bid = field_obj.get("name")
        if bid and bid in added:
            return
        ensure_choices(field_obj)
        fb = normalize_blocks(to_slack_block(field_obj))
        for bb in fb:
            if bb and bb.get("type") == "input" and bb.get("block_id"):
                added.add(bb["block_id"])
        blocks.extend(fb)

        secs = get_sections_cached(field_obj["id"])
        if not secs:
            return

        selected = selected_value_for(field_obj, state_values)
        if selected is None:
            return

        sel_str = str(selected)
        for sec in secs:
            if sel_str not in activator_values(sec):
                continue
            for child_id in sec.get("fields") or []:
                child = by_id.get(child_id)
                if not child:
                    continue
                belongs_here = in_this_page(child) or (WIZARD_CROSS_SECTION_CHILDREN and section_id != "core")
                if belongs_here:
                    _append_field_tree_in_page(child)

    for _, f in fields_here:
        if f["id"] in dependent_ids:
            continue
        _append_field_tree_in_page(f)

    if not [b for b in blocks if b.get("type") == "input"]:
        blocks.append({"type":"section","text":{"type":"mrkdwn","text":"_No visible fields on this step._"}})

    return blocks[:MAX_BLOCKS]

def build_wizard_page_modal(form: dict, all_fields: list, token: str, page: int, state_values: dict):
    pages = compute_pages(form, all_fields)
    total = len(pages)
    page = max(0, min(page, total-1))
    section_id = pages[page]

    fields_blocks = build_fields_for_page(form, all_fields, state_values, section_id)

    nav_elems = []
    if page > 0:
        nav_elems.append({"type":"button","action_id":"wizard_prev","text":{"type":"plain_text","text":"Back"},"value":token})
    if page < total-1:
        nav_elems.append({"type":"button","action_id":"wizard_next","text":{"type":"plain_text","text":"Next"},"style":"primary","value":token})

    blocks = [{"type":"section","text":{"type":"mrkdwn","text":f"*Step {page+1} of {total}*"}}]
    blocks.extend(fields_blocks)
    if nav_elems:
        blocks.append({"type":"actions","block_id":"wizard_nav","elements":nav_elems})

    view = {
        "type":"modal",
        "callback_id":"wizard_submit" if page == total-1 else "wizard_page",
        "title":{"type":"plain_text","text":(form.get("name") or "New IT Ticket")[:24]},
        "close":{"type":"plain_text","text":"Cancel" if page==0 else "Close"},
        "blocks":blocks,
        "private_metadata": _json_dumps({"ticket_form_id": form["id"], "wizard_token": token, "page_index": page})
    }
    if page == total-1:
        view["submit"] = {"type":"plain_text","text":"Create"}
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
