"""Codex SessionStart hook: inject the AGENTS rules written for the session's model.

Codex passes the event as JSON on stdin; `model` is the active model slug and `source` says why the hook ran
(startup, clear, compact). Output is the documented SessionStart shape, so the text arrives as developer context.
Unknown models get nothing and the session continues normally.

After a context compaction Codex rebuilds the history and this hook's text becomes the last item the model sees,
often in the middle of a task. Bare rules in that position read like a new instruction, and some models answer
with "已收到，后续按这些约定执行" and end the turn. So on compaction the rules are wrapped in a note that they were
reloaded and that the interrupted task continues.
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
COMPACT_HEAD = "【上下文刚被自动压缩，下面是重新加载的模型补充规则。它不是用户的新消息，不需要回复或确认。】"
COMPACT_TAIL = ("【规则到此结束。不要回复“已收到”或复述规则。以压缩摘要和最近一条用户消息为准，"
                "从压缩前停下的地方接着做那个任务；任务已经做完、只等用户回复时，照常结束。】")


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
    if str(event.get("source") or "").lower() == "compact":
        text = f"{COMPACT_HEAD}\n\n{text}\n\n{COMPACT_TAIL}"
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    sys.stdout.write(json.dumps(out, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
