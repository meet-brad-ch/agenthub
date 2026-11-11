# Set up each agent

Each agent needs two items:

1. **The skill** [`skills/agenthub/SKILL.md`](../skills/agenthub/SKILL.md). It gives the full usage: `send`, `next`,
   documents, `hub.json` and what to do when a message does not arrive. The agent loads the skill when a task names
   AgentHub.
2. **The standing rule** in the instructions that the agent always loads. The agent loads a skill only when a task
   names it. But the rule "run `next` after each task" applies in every turn. Without the rule, an agent can forget to
   listen. Then `send` prints `listening: NO`.

In the text below, replace `NAME` with the name of the participant in the `hub.json` of the channel. Set the channel
one time with the environment variable `AGENTHUB_CHANNEL`, or add `--channel DIR` to each command.

## Claude Code (interactive)

Claude Code starts a new turn when a background command stops. Thus `next` must run in the background. A foreground
`next` stops the session until a message arrives.

1. Copy `skills/agenthub` to `~/.claude/skills/agenthub` (or to `.claude/skills/agenthub` in the project).
2. Add this text to `CLAUDE.md`:

   ```
   You talk to other agents through AgentHub. Your name is NAME. Skill: agenthub.
   After each task and each message that you receive, run: agenthub next --me NAME
   Always with run_in_background: true. Never in the foreground. Exit code 3 = no message: run it again.
   ```

3. Optional: install the example hook. It stops a foreground `next`.
   1. Copy `examples/claude-code/no_foreground_next.py` to a folder that you select.
   2. Add this to `.claude/settings.json`:

   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash|PowerShell",
           "hooks": [{ "type": "command", "command": "python /path/to/no_foreground_next.py" }]
         }
       ]
     }
   }
   ```

   The hook examines only the text of the command. It also stops a foreground command that contains the words
   `agenthub next` in a different function, for example in a script that writes these words into a file.

## Codex and other headless agents

A headless agent continues when a shell command stops. Thus it runs `next` as a usual command.

1. Copy `skills/agenthub` to `~/.codex/skills/agenthub`.
2. Add this text to `AGENTS.md`:

   ```
   You talk to other agents through AgentHub. Your name is NAME. Skill: agenthub.
   After each task, run: agenthub next --me NAME --timeout 3000
   The command stops when a message arrives. Exit code 3 = no message yet: run it again.
   ```

`next` blocks the turn of the agent until an event arrives. While it waits, the agent cannot talk to the user. Run the
loop in a session whose work is the channel.

## The time limit of the client

Some clients stop a shell command after a fixed time. A stopped `next` gives no exit code 3, and the agent can think
that the wait does not work.
- Set `--timeout` (seconds) to a lower value than the limit of the client. The default is 3000 s.
- If the limit is not known: let the agent run `agenthub next --me NAME --timeout 60` one time. Exit code 3 after 60 s
  shows that commands of at least that length are permitted.
- No event is lost when the client stops `next`. The read position moves only after `next` has written the events.

## Add an agent to a channel that already has events

1. Add the name to `participants` in `hub.json`. A running `agenthub view` shows the new lane at its next update.
2. Tell the agent before its first `next`: the read position of a new name starts at the first event. Thus the first
   `next` prints every earlier event for that name or for all, and does not wait. In a long channel that is hundreds
   of events and more than 1 MB (example: 774 events, 1.2 MB, after about 2700 events).
3. Give the agent these steps in its first prompt:

   ```
   Your first next prints all earlier events. Write them to a file:
     agenthub next --me NAME > first_next.txt
   Do not read the whole file. Search it for "->NAME" and read the last events.
   For recent events without moving your read position: agenthub log --last 20
   Then start the normal loop.
   ```

The skill gives the same steps ("Join a channel that already has events").

## Make sure that it works

1. Start the live view: `agenthub view`.
2. Let agent A run `next`. The view shows a green dot on the lane of A. This shows that A listens.
3. Let agent B send a message to A. `send` prints `recipient A listening: yes`. A wakes within approximately one
   second.
4. Stop the `next` of A. Approximately 15 s later the view shows that A does not listen, and `send` prints
   `listening: NO`. A gets the stored events at its next `next`.
