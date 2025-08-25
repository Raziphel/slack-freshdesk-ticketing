"""Core Slack endpoints.

I ripped out all the dynamic Freshdesk form stuff and replaced it with a super
simple manual question flow. Future-me: this is where Slack hits my server.
"""

import json
from flask import Blueprint, request

from logic.manual_flow import start_flow, handle_submission

# I'm registering a tiny blueprint because Flask likes things modular
bp = Blueprint("core", __name__)


@bp.route("/it-ticket", methods=["POST"])
def it_ticket_command():
    """Slash command entry point."""
    # Slack hands me a trigger id which I need to open the first modal
    trigger_id = request.form.get("trigger_id")
    # Kick off the manual question flow using that trigger
    start_flow(trigger_id)
    # Slack doesn't expect a body here; an empty 200 keeps it happy
    return "", 200


@bp.route("/interactions", methods=["POST"])
def interactions():
    """All interactive events from Slack land here."""
    payload = json.loads(request.form["payload"])
    # I'm only interested in view submissions since each question is its own modal
    if payload.get("type") == "view_submission":
        # Let my manual flow module figure out what to do next
        handle_submission(payload)
    # Even if I don't care about something I should still return 200 so Slack stops retrying
    return "", 200
