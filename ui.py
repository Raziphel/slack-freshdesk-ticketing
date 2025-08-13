import json

def loading_modal(msg="Loading…"):
    return {
        "type": "modal",
        "callback_id": "loading",
        "title": {"type": "plain_text", "text": "New IT Ticket"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{msg}*"}}]
    }

def build_form_picker_modal(forms):
    options = [{
        "text": {"type": "plain_text", "text": f.get("name", f"Form {f.get('id')}")[:75]},
        "value": str(f["id"])
    } for f in forms]

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
