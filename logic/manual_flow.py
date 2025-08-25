"""Manual question flow controller.

This file uses a JSON config to define the order of questions. I'm adding way
more comments than usual so future-me can follow along when I inevitably forget
how it works.
"""

import json
import uuid
from pathlib import Path
from typing import Dict, Any

from config import QUESTION_FLOW_FILE
from services.slack import slack_api

# I'm keeping a simple session cache in memory so I know where each user is in the flow
SESSIONS: Dict[str, Dict[str, Any]] = {}


def _load_flow() -> Dict[str, Dict[str, Any]]:
    """Load the question flow from disk."""
    # I want the flexibility to edit my questions without touching Python
    path = Path(QUESTION_FLOW_FILE)
    # If the file doesn't exist I'll let the exception bubble up so I notice it
    with path.open() as fh:
        data = json.load(fh)
    # The config is a list, but it's easier for me to look questions up by id
    return {q["id"]: q for q in data}


# Grab the flow definition once on import so I don't keep re-reading the file
FLOW = _load_flow()


def start_flow(trigger_id: str) -> None:
    """Kick off a brand new question flow."""
    # Every run gets a random token so I can track answers across steps
    token = str(uuid.uuid4())
    # The first question is just the first item in the config
    first_id = next(iter(FLOW))
    # Stash where I am and any answers I collect
    SESSIONS[token] = {"current": first_id, "answers": {}}
    # Build the modal for the first question and fire it off to Slack
    view = _build_modal(first_id, token)
    slack_api("views.open", {"trigger_id": trigger_id, "view": view})


def handle_submission(payload: Dict[str, Any]) -> None:
    """Handle a view_submission from Slack."""
    # Slack gives me back the view so I can figure out which question this was
    view = payload["view"]
    meta = json.loads(view.get("private_metadata") or "{}")
    token = meta["token"]
    qid = meta["question_id"]
    # Slack nests the answer in a pretty deep structure; unwrap it
    state = view["state"]["values"][qid]["answer"]
    answer = state.get("value")
    if answer is None:
        # If there's no text value it's probably a select, grab the selected option
        answer = (state.get("selected_option") or {}).get("value")
    # Save the answer so I can use it later if I want
    session = SESSIONS[token]
    session["answers"][qid] = answer
    # Figure out what question should come next
    next_id = _next_question(qid, answer)
    if not next_id:
        # No next question means I'm done; swap the view for a simple completion message
        slack_api("views.update", {"view_id": view["id"], "view": _completion_view()})
        # Cleanup so I don't leak memory
        del SESSIONS[token]
        return
    # Update where I am in the session
    session["current"] = next_id
    # Build and show the next question
    next_view = _build_modal(next_id, token)
    slack_api("views.update", {"view_id": view["id"], "view": next_view})


def _next_question(qid: str, answer: str | None) -> str | None:
    """Given an answer, look up where I should go next."""
    node = FLOW.get(qid) or {}
    nxt = node.get("next")
    # If "next" is a dict I'm doing branching based on the exact answer
    if isinstance(nxt, dict):
        return nxt.get(str(answer))
    # Otherwise it's just a straight line to another question id
    return nxt


def _build_modal(qid: str, token: str) -> Dict[str, Any]:
    """Create the Slack modal for a single question."""
    q = FLOW[qid]
    element = _build_element(q)
    # If there's another question after this, the button should say "Next"
    submit_text = "Next" if _next_question(qid, None) else "Submit"
    return {
        "type": "modal",
        # I prefix the callback so I can spot these in the interactions endpoint
        "callback_id": f"manual_{qid}",
        "title": {"type": "plain_text", "text": "New IT Ticket"},
        "submit": {"type": "plain_text", "text": submit_text},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": qid,
                "label": {"type": "plain_text", "text": q["question"][:75]},
                "element": element,
            }
        ],
        # I tuck the session token and current question into private metadata for round-tripping
        "private_metadata": json.dumps({"token": token, "question_id": qid}),
    }


def _build_element(q: Dict[str, Any]) -> Dict[str, Any]:
    """Build the actual input element for a question."""
    if q.get("type") == "select":
        # For a select I'm mapping each option into Slack's format
        options = [
            {"text": {"type": "plain_text", "text": opt}, "value": opt}
            for opt in q.get("options", [])
        ]
        return {
            "type": "static_select",
            "action_id": "answer",
            "options": options,
            "placeholder": {"type": "plain_text", "text": "Choose an option"},
        }
    # Default to a plain text input
    return {"type": "plain_text_input", "action_id": "answer"}


def _completion_view() -> Dict[str, Any]:
    """Return a simple 'you're done' modal."""
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Done"},
        "close": {"type": "plain_text", "text": "Close"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*All done!*"},
            }
        ],
    }
