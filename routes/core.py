from __future__ import annotations
import json, logging, threading
from flask import Blueprint, request, jsonify
from config import ENABLE_WIZARD
from services.freshdesk import (
    fd_get,
    get_ticket_forms_cached,
    get_ticket_fields_cached,
)
from services.slack import slack_api, get_user_email
from logic.forms import filter_portal_forms
from logic.single_page import build_form_fields_modal
from logic.wizard import open_wizard_first_page, update_wizard, WIZARD_SESSIONS
from logic.ticket import modal_values_to_fd_ticket
from ui import loading_modal, build_form_picker_modal

log = logging.getLogger(__name__)
bp = Blueprint("core", __name__)


def _notify_user_ticket_created(user_id: str, ticket_id: int) -> bool:
    # Giving the requester a heads-up in Slack once their ticket is born.
    try:
        dm = slack_api("conversations.open", {"users": user_id})
        channel_id = (dm.get("channel") or {}).get("id") or user_id
        slack_api("chat.postMessage", {"channel": channel_id, "text": f"Ticket created: {ticket_id}"})
        return True
    except Exception as e:
        log.exception("Notify user failed: %s", e)
        return False

@bp.route("/it-ticket", methods=["POST"])
def it_ticket_command():
    # Handling the /it-ticket slash command kickoff.
    trigger_id = request.form.get("trigger_id")
    # Open a lightweight loading modal immediately to avoid trigger expiry
    initial = slack_api(
        "views.open",
        {"trigger_id": trigger_id, "view": loading_modal("Loading forms...")},
    )
    view_info = initial.get("view") or {}
    view_id = view_info.get("id")
    view_hash = view_info.get("hash")

    def _populate():
        try:
            forms = filter_portal_forms(get_ticket_forms_cached())
            modal = build_form_picker_modal(forms)
            payload = {"view_id": view_id, "view": modal}
            if view_hash:
                payload["hash"] = view_hash
            try:
                slack_api("views.update", payload)
            except RuntimeError:
                payload.pop("hash", None)
                slack_api("views.update", payload)
        except Exception as e:
            log.exception("Opening form picker failed: %s", e)
            err_view = {
                "type": "modal",
                "title": {"type": "plain_text", "text": "New IT Ticket"},
                "close": {"type": "plain_text", "text": "Close"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f":warning: Failed to load forms.\n`{e}`",
                        },
                    }
                ],
            }
            slack_api("views.update", {"view_id": view_id, "view": err_view})

    threading.Thread(target=_populate, daemon=True).start()
    return "", 200

@bp.route("/interactions", methods=["POST"])
def interactions():
    # All the Slack interactive callbacks funnel through here.
    payload = json.loads(request.form["payload"])
    ptype = payload.get("type")
    view  = payload.get("view", {})
    cb    = view.get("callback_id")

    # Step 1: choose the FD form
    if ptype == "view_submission" and cb == "pick_form":
        values = view.get("state", {}).get("values", {})
        sel = values.get("form_select", {}).get("ticket_form_select", {})
        chosen = (sel.get("selected_option") or {}).get("value")
        if not chosen or chosen == "__noop__":
            return jsonify({"response_action": "errors","errors": {"form_select": "Please choose a ticket type"}}), 200

        response = {"response_action": "update", "view": loading_modal("Loading form...")}
        if ENABLE_WIZARD:
            threading.Thread(
                target=open_wizard_first_page,
                args=(view["id"], int(chosen), view.get("hash")),
                daemon=True,
            ).start()
        else:
            # single-page async update
            def _run():
                try:
                    forms = get_ticket_forms_cached()
                    fd_fields = get_ticket_fields_cached()
                    form = next((f for f in forms if str(f["id"]) == str(chosen)), None)
                    updated = build_form_fields_modal(form, fd_fields, None)
                    from services.slack import slack_api
                    try:
                        slack_api(
                            "views.update",
                            {"view_id": view["id"], "hash": view.get("hash"), "view": updated},
                        )
                    except RuntimeError:
                        slack_api("views.update", {"view_id": view["id"], "view": updated})
                except Exception as e:
                    log.exception("Async update failed: %s", e)

            threading.Thread(target=_run, daemon=True).start()
        return jsonify(response), 200

    # Live updates while the user changes inputs or clicks wizard nav
    if ptype == "block_actions":
        try:
            meta = json.loads(view.get("private_metadata") or "{}")
        except json.JSONDecodeError:
            meta = {}

        # Wizard nav
        if ENABLE_WIZARD and meta.get("wizard_token"):
            token = meta["wizard_token"]
            state_values = view.get("state", {}).get("values", {}) or {}
            actions = payload.get("actions", []) or []
            nav = None
            for a in actions:
                if a.get("action_id") == "wizard_next":
                    nav = "next"
                elif a.get("action_id") == "wizard_prev":
                    nav = "prev"
            threading.Thread(
                target=update_wizard,
                args=(view["id"], token, view.get("hash"), state_values, nav),
                daemon=True,
            ).start()
            return "", 200

        # Single-page live update: rebuild fields based on current selections
        state_values = view.get("state", {}).get("values", {}) or {}
        ticket_form_id = meta.get("ticket_form_id")
        if ticket_form_id:
            def _run_update():
                try:
                    forms = get_ticket_forms_cached()
                    fd_fields = get_ticket_fields_cached()
                    form = next((f for f in forms if str(f["id"]) == str(ticket_form_id)), None)
                    updated = build_form_fields_modal(form, fd_fields, state_values)
                    try:
                        slack_api("views.update", {"view_id": view["id"], "hash": view.get("hash"), "view": updated})
                    except RuntimeError:
                        slack_api("views.update", {"view_id": view["id"], "view": updated})
                except Exception as e:
                    log.exception("Live update failed: %s", e)
            threading.Thread(target=_run_update, daemon=True).start()
        return "", 200

    # Single-page submit
    if ptype == "view_submission" and cb == "submit_it_ticket":
        values = view["state"]["values"]
        try:
            meta = json.loads(view.get("private_metadata") or "{}")
        except json.JSONDecodeError:
            meta = {}
        ticket_form_id = meta.get("ticket_form_id")
        user_id = (payload.get("user") or {}).get("id")
        user_email = get_user_email(user_id) if user_id else None
        fd_ticket = modal_values_to_fd_ticket(values, ticket_form_id, user_email)
        try:
            created = fd_get("/api/v2/admin/ticket_fields")  # dummy ping to keep token warm
            from services.freshdesk import fd_post
            created = fd_post("/api/v2/tickets", fd_ticket)
            ticket_id = created.get("id")
            log.info("✅ Ticket created: %s", ticket_id)
            notified = _notify_user_ticket_created(user_id, ticket_id) if user_id and ticket_id else False
            if notified:
                return jsonify({"response_action": "clear"}), 200
            success_view = {
                "type": "modal",
                "title": {"type": "plain_text", "text": "Ticket created"},
                "close": {"type": "plain_text", "text": "Close"},
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": f":white_check_mark: Ticket created: {ticket_id}"}}
                ]
            }
            return jsonify({"response_action": "update", "view": success_view}), 200
        except Exception as e:
            log.exception("Ticket create failed: %s", e)
            return jsonify({"response_action": "errors","errors": {"subject": "Ticket creation failed. Please try again."}}), 200

    # Wizard submit
    if ptype == "view_submission" and cb == "wizard_submit":
        try:
            meta = json.loads(view.get("private_metadata") or "{}")
        except json.JSONDecodeError:
            meta = {}
        token = meta.get("wizard_token"); session = WIZARD_SESSIONS.get(token) if token else None
        merged = dict((session.get("values") or {})) if session else {}
        merged.update(view.get("state", {}).get("values", {}) or {})
        ticket_form_id = (session or {}).get("ticket_form_id") or meta.get("ticket_form_id")

        user_id = (payload.get("user") or {}).get("id")
        user_email = get_user_email(user_id) if user_id else None
        fd_ticket = modal_values_to_fd_ticket(merged, ticket_form_id, user_email)
        try:
            from services.freshdesk import fd_post
            created = fd_post("/api/v2/tickets", fd_ticket)
            ticket_id = created.get("id")
            log.info("✅ Ticket created: %s", ticket_id)
            notified = _notify_user_ticket_created(user_id, ticket_id) if user_id and ticket_id else False
            if token and token in WIZARD_SESSIONS:
                del WIZARD_SESSIONS[token]
            if notified:
                return jsonify({"response_action": "clear"}), 200
            success_view = {
                "type": "modal",
                "title": {"type": "plain_text", "text": "Ticket created"},
                "close": {"type": "plain_text", "text": "Close"},
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": f":white_check_mark: Ticket created: {ticket_id}"}}
                ]
            }
            return jsonify({"response_action": "update", "view": success_view}), 200
        except Exception as e:
            log.exception("Ticket create failed: %s", e)
            return jsonify({"response_action": "errors","errors": {"subject": "Ticket creation failed. Please try again."}}), 200

    return "", 200
