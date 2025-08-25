"""Quick CLI to step through my manual question flow."""

# I'm doing this so I can poke the question flow without touching Slack.
# It's basically the same logic as the real flow but in the terminal.

import json
from pathlib import Path

from config import QUESTION_FLOW_FILE


def _load_flow() -> dict:
    """Read the flow config and map ids to question objects."""
    path = Path(QUESTION_FLOW_FILE)
    with path.open() as fh:
        data = json.load(fh)
    # I like dicts keyed by id because they're easy to traverse
    return {q["id"]: q for q in data}


def run_cli() -> None:
    """Run an interactive session on the command line."""
    flow = _load_flow()
    # Start with the very first question in the config
    current = next(iter(flow))
    answers: dict[str, str] = {}
    while current:
        q = flow[current]
        print(q["question"])  # Show the actual question text
        answer = ""
        if q.get("type") == "select":
            # When it's a select I show the options with numbers
            options = q.get("options", [])
            for idx, opt in enumerate(options, 1):
                print(f"{idx}. {opt}")
            choice = input("> ").strip()
            # Let me pick by number or type the option outright
            if choice.isdigit() and 1 <= int(choice) <= len(options):
                answer = options[int(choice) - 1]
            else:
                answer = choice
        else:
            # For plain text questions I just capture whatever I type
            answer = input("> ").strip()
        answers[current] = answer
        nxt = q.get("next")
        if isinstance(nxt, dict):
            current = nxt.get(answer)
        else:
            current = nxt
    # When there's no next question I dump out everything I captured
    print("\nDone. Here's what I collected:")
    for qid, ans in answers.items():
        print(f"- {qid}: {ans}")


if __name__ == "__main__":
    run_cli()
