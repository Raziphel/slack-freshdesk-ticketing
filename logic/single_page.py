from __future__ import annotations
from config import MAX_BLOCKS
from services.freshdesk import get_form_detail
from logic.forms import normalize_id_list
from logic.mapping import to_slack_block, normalize_blocks, ensure_choices
from logic.branching import get_sections_cached, activator_values, selected_value_for

def build_fields_for_form(form: dict, all_fields: list, state_values: dict | None = None):
    state_values = state_values or {}
    by_id = {f["id"]: f for f in all_fields}

    form_detail = get_form_detail(int(form["id"]))
    sections_list = form_detail.get("sections", [])
    sections = {s["id"]: s for s in sections_list}

    raw = form_detail.get("fields") or form.get("fields") or []
    id_order = normalize_id_list(raw)
    # children map
    dependent_ids: set[int] = set()
    for fid in id_order:
        for sec in get_sections_cached(fid):
            dependent_ids.update(normalize_id_list(sec.get("fields") or []))

    blocks = []
    subj = next((f for f in all_fields if f.get("type") == "default_subject"), None)
    desc = next((f for f in all_fields if f.get("type") == "default_description"), None)
    for core in (subj, desc):
        if core:
            blocks.extend(normalize_blocks(to_slack_block(core)))
    if sections_list and blocks:
        blocks.append({"type":"divider"})

    added: set[str] = set(b.get("block_id") for b in blocks if b.get("type") == "input")

    def _append_field_tree(field_obj: dict):
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
                if child:
                    _append_field_tree(child)

    section_headers_added: set[int] = set()

    for fid in id_order:
        f = by_id.get(fid)
        if not f or f.get("type") in {"default_subject", "default_description"}:
            continue
        if f["id"] in dependent_ids:
            continue
        mappings = f.get("section_mappings") or []
        if mappings:
            sid = mappings[0].get("section_id")
            if sid in sections and sid not in section_headers_added:
                sec = sections.get(sid)
                blocks.append({"type":"header","text":{"type":"plain_text","text":sec.get("name","Section")[:150]}})
                section_headers_added.add(sid)
        _append_field_tree(f)

    if not [b for b in blocks if b.get("type") == "input"]:
        blocks.append({"type":"section","text":{"type":"mrkdwn","text":"_No visible fields for customers on this form._"}})

    return blocks[:MAX_BLOCKS]

def build_form_fields_modal(form: dict, all_fields: list, state_values: dict | None = None):
    blocks = build_fields_for_form(form, all_fields, state_values)
    return {"type":"modal","callback_id":"submit_it_ticket",
            "title":{"type":"plain_text","text":(form.get("name") or "New IT Ticket")[:24]},
            "submit":{"type":"plain_text","text":"Create"},
            "close":{"type":"plain_text","text":"Back"},
            "blocks":blocks,
            "private_metadata": json_dumps({"ticket_form_id": form["id"]})}

# local lightweight JSON dumps to avoid circular imports
def json_dumps(obj):  # tiny helper
    import json
    return json.dumps(obj)
