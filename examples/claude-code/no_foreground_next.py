"""Claude Code PreToolUse hook (Bash, PowerShell): stop `agenthub next` in the foreground.

A foreground `next` stops the interactive session until a message arrives. A background `next` that stops is the
signal that starts the next turn. The hook reads the tool call as JSON from stdin. If the command contains
`agenthub next` and does not run in the background, the hook prints a deny decision.
The hook examines only the text of the command. Installation: see docs/clients.md.
"""
import json
import sys

REASON = ("Run `agenthub next` with run_in_background: true. Do not run it in the foreground. "
          "When the background command stops, you get the signal for your next turn.")


def main() -> int:
    """Entry point: print a deny decision for a foreground `agenthub next`. Print nothing for other calls.

    @returns 0.
    """
    data = json.load(sys.stdin)
    tool_input = data.get("tool_input") or {}
    in_background = tool_input.get("run_in_background") is True
    command = str(tool_input.get("command", ""))
    runs_next = "agenthub next" in command
    if in_background or not runs_next:
        return 0
    decision = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": REASON}}
    decision_text = json.dumps(decision)
    print(decision_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
