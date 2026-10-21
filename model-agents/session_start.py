"""Codex SessionStart hook: inject the AGENTS rules written for the session's model.

Codex passes the event as JSON on stdin; `model` is the active model slug.
Output is the documented SessionStart shape, so the text arrives as developer context.
Unknown models get nothing and the session continues normally.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RULES = {
    "gpt-6-sol": "AGENTS.gpt-6-sol.md",
    "gpt-6.1-sol": "AGENTS.gpt-6.1-sol.md",
    "gpt-6-astra": "AGENTS.gpt-6-astra.md",
}


def rules_file(model):
    model = model.strip().lower()
    if model in RULES:
        return RULES[model]
    # dated snapshots such as gpt-6.1-sol-2026-10-01
    for slug in sorted(RULES, key=len, reverse=True):
        if model.startswith(slug + "-"):
            return RULES[slug]
    return None


def main():
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return 0
    name = rules_file(str(event.get("model") or ""))
    if name is None:
        return 0
    path = HERE / name
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8").strip()
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    sys.stdout.write(json.dumps(out, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
