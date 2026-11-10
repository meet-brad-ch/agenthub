"""The example Claude Code hook: it denies only a foreground `agenthub next`."""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "examples" / "claude-code" / "no_foreground_next.py"


def _decide(tool_input: dict) -> str:
    """Run the hook on one Bash tool call. @returns its stdout: a deny decision as JSON, or "" to allow."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": tool_input})
    command = [sys.executable, str(HOOK)]
    result = subprocess.run(command, input=payload, capture_output=True, text=True, check=True)
    output = result.stdout.strip()
    return output


def test_foreground_next_is_denied():
    """A foreground `next` freezes the session: the hook denies it and names run_in_background."""
    output = _decide({"command": "python -m agenthub next --me engineer"})
    decision = json.loads(output)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "run_in_background" in decision["permissionDecisionReason"]


def test_background_next_is_allowed():
    """A background `next` is the intended use: no decision."""
    output = _decide({"command": "agenthub next --me engineer", "run_in_background": True})
    assert output == ""


def test_other_commands_are_allowed():
    """Other commands, also other agenthub commands, pass through."""
    output = _decide({"command": "agenthub send --me engineer --kind message --body-file b.md"})
    assert output == ""
