import json
from config import FORM_NAME_TO_DISPLAY

def loading_modal(msg="Loading…"):
    # Simple little spinner modal so folks know I'm fetching stuff.
    return {
        "type": "modal",
        "callback_id": "loading",
        "title": {"type": "plain_text", "text": "New IT Ticket"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{msg}*"}}]
    }

def build_form_picker_modal(forms):
    # Building the first step where I ask the user which form they want.
    options = []
    for f in forms:
        raw_name = f.get("name", f"Form {f.get('id')}")
        label = FORM_NAME_TO_DISPLAY.get(raw_name, raw_name)
        options.append({
            "text": {"type": "plain_text", "text": label[:75]},
            "value": str(f["id"])
        })

    return {
        "type": "modal",
        "callback_id": "pick_form",
        "title": {"type": "plain_text", "text": "New IT Ticket"},
        "submit": {"type": "plain_text", "text": "Next"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [{
            "type": "input",
            "block_id": "form_select",
            "label": {"type": "plain_text", "text": "Ticket Type"},
            "element": {
                "type": "static_select",
                "action_id": "ticket_form_select",
                "placeholder": {"type": "plain_text", "text": "Choose a ticket type"},
                "options": options
            },
            "dispatch_action": True
        }]
    }
