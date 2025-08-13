from __future__ import annotations
from flask import Blueprint, request, jsonify
from services.freshdesk import fetch_field_detail, fd_get
from logic.mapping import get_field_choices, iter_choice_items, ensure_choices

bp = Blueprint("debug", __name__)

@bp.get("/debug/field/<int:fid>")
def debug_field(fid: int):
    data = fetch_field_detail(fid) or {}
    seen = get_field_choices(data)
    try:
        preview = list(iter_choice_items(seen))[:10] if seen else []
    except Exception:
        preview = []
    return jsonify({
        "id": fid,
        "name": data.get("name"),
        "type": data.get("type"),
        "has_choices": bool(seen),
        "choices_preview": preview,
        "raw_keys": list(data.keys())
    }), 200

@bp.get("/debug/find")
def debug_find():
    q = (request.args.get("name") or "").strip().lower()
    if not q:
        return jsonify({"error": "pass ?name=..."}), 400
    try:
        fields = fd_get("/api/v2/admin/ticket_fields")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    hits = []
    for f in fields:
        nm = (f.get("name") or "").lower()
        lbl = (f.get("label_for_customers") or f.get("label") or "").lower()
        if q in nm or q in lbl:
            choices = get_field_choices(ensure_choices(f))
            preview = []
            if choices:
                try:
                    preview = list(iter_choice_items(choices))[:10]
                except Exception:
                    preview = []
            hits.append({
                "id": f.get("id"),
                "name": f.get("name"),
                "label": f.get("label_for_customers") or f.get("label"),
                "type": f.get("type"),
                "has_choices": bool(choices),
                "choices_preview": preview
            })
    return jsonify({"query": q, "results": hits}), 200
