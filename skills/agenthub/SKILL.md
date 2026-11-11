---
name: agenthub
description: Send and receive messages between coding agents with AgentHub. AgentHub uses a shared channel folder and no server. This skill gives the commands send and next, how to wait for the other agent without a blocked session, the participants and document kinds in hub.json (task{n}.md, response{n}.md), what "listening - NO" and exit code 3 mean, the live viewer, and what to do when a message does not arrive. Use it when the agent works with another agent through AgentHub, sends a message or a document, waits for a reply, sets up a channel, or cannot find a message. Triggers - agenthub, AgentHub, agenthub send, agenthub next, channel folder, hub.json, AGENTHUB_CHANNEL, listening NO, NO_MESSAGE, message the other agent, wait for the scientist, wait for the engineer.
---

# AgentHub

AgentHub sends messages between agents on one machine. A channel folder holds all the state. Each agent uses two
commands: `send` and `next`.

## The standing rule

The agent loads this skill only when a task names AgentHub. The rule below applies in every turn. Thus the user also
writes the rule in the instructions that the agent always loads (`CLAUDE.md` or `AGENTS.md`, see "Setup"):

> After each task, and after each message that you receive, run `agenthub next --me NAME`.
> In Claude Code, run it with `run_in_background: true`. Do not run it in the foreground.

The reason:
- Your harness wakes you only when a command that you started stops.
- A foreground `next` stops your interactive session until a message arrives.
- When a background `next` stops, your harness starts your next turn.
- If no `next` runs, you cannot receive. The `send` of the other agent then prints `listening: NO`.

## Your name

The user gives each agent its name. The name is a key of `participants` in `hub.json`. Your standing instructions
give it ("your name is NAME").
- Give your name with `--me` in each command.
- If you do not know your name, ask the user. Do not select a name yourself.
- Do not use the name of a different agent. Two sessions with one name use one read cursor. The session that runs
  `next` first gets each message.

## Receive messages: next

```
agenthub next --me NAME [--timeout SECONDS]
```

| Result | Meaning |
|---|---|
| Exit code 0, events printed | Unread events existed, or one arrived while `next` waited. |
| Exit code 3, `NO_MESSAGE for NAME ...` | No event arrived within `--timeout` seconds (default 3000). Run `next` again. |
| Exit code 2, `AGENTHUB_ERROR ...` | The name is wrong, or the channel is missing. Read the message. Correct the cause. |

The output for each event:
- One summary line.
- For a `message`: the text of the message, after the summary line.
- For a document: the summary line ends with `file=PATH`. Read that file with your file tool.

```
AGENTHUB #12 scientist->engineer message Run the tests
Please run the unit tests and report the count.
```

When `next` stops:
1. Read the output. Do the work that it asks for.
2. Start `next` again. In Claude Code, start it in the background. Do this also after exit code 3.
3. Run only one `next` for each participant at a time.
4. In Claude Code, the end of the background command is the signal. Do not read its output file again and again.

AgentHub delivers each event at least once. After a crash, an event can arrive two times. Its id (`#12`) identifies
it.

### The time limit of your client

Some clients stop a shell command after a fixed time. A command that the client stops gives no exit code 3.
- Give `--timeout` (seconds) a lower value than the limit of your client. The default is 3000 s.
- If you do not know the limit, run `agenthub next --me NAME --timeout 60` one time. Exit code 3 after 60 s shows
  that commands of at least that length are permitted.
- When the client stopped your `next`, start it again with a lower `--timeout`. You lose no event: your read
  position moves only after `next` has written the events.

### A client without background commands (Codex)

`next` blocks your turn until an event arrives. While it waits, you cannot talk to the user. Run the loop only in a
session whose work is the channel.

## Join a channel that already has events

Your read position starts at the first event of the channel. Thus your first `next` prints every earlier event that
is for you or for all. It does not wait. In a long channel this is hundreds of events and more than 1 MB of text.
Example: a participant added after about 2700 events received 774 events (1.2 MB) at its first `next`.

Do this one time, when you join:
1. Write the output of the first `next` to a file: `agenthub next --me NAME > first_next.txt`. In Claude Code, the
   background command writes its output to a file already.
2. Do not read the whole file. Search it for `->NAME` (events only for you) and read the last events.
3. To see the recent events without moving your read position, run `agenthub log --last 20`.
4. Then start the normal loop: `next` in the background (Claude Code) or as a usual command (Codex).

## Make sure that you listen

While your `next` waits, the others can see it:
- `agenthub view` shows a green dot on your lane.
- The `send` of another participant prints `recipient NAME listening: yes`.

`next` writes a marker every 5 s. Approximately 15 s after your `next` stops, the others see `listening: NO`.

## Send messages: send

```
agenthub send --me NAME --kind KIND --body-file FILE [--to OTHER] [--title "short title"] [--number N] [--status S]
```

1. Write the text of the message to a file first. Use your file tool. Do not put a long text on the command line.
   Quotes can break, and long commands can fail.
2. Send the message. `--to` gives one recipient. Without `--to`, all participants get the event.
3. Read the output:
   - `AGENTHUB #N ...`: AgentHub stored the event.
   - `recipient OTHER listening: yes`: the recipient wakes now.
   - `recipient OTHER listening: NO (...)`: the recipient does not wait. It gets the message when it runs `next`
     again. If the recipient must act soon, tell the user that OTHER does not listen.
4. Run `next` again to receive the reply.

The kinds:
- `message`: always permitted. The event log keeps the text.
- A document kind from `hub.json` (for example `task`, `response`): the text becomes a file in the channel folder
  (for example `task3.md`). Give `--number`. AgentHub applies the rules in `hub.json`:
  - `may_post`: who can send this kind.
  - `immutable`: nobody can write the file again.
  - `sequential`: the numbers are 1, 2, 3, and so on.
  - `requires`: the document of this kind with the same number must exist first.
- If a rule is not satisfied, `send` prints `AGENTHUB_ERROR` and stores nothing.

## The channel

Give `--channel DIR` to each command, or set `AGENTHUB_CHANNEL` one time. `DIR/hub.json` gives the participants:

```json
{
  "participants": {"scientist": {"may_post": ["task", "message"]}, "engineer": {"may_post": ["response", "message"]}},
  "documents": {"task": {"pattern": "task{n}.md", "immutable": true, "sequential": true, "requires": ""},
                "response": {"pattern": "response{n}.md", "immutable": false, "sequential": false, "requires": "task"}}
}
```

`agenthub init DIR` writes this example. The user selects all names and kinds.

Commands for people:
- `agenthub log [--last N]` prints the recent events.
- `agenthub view` opens a live view in the browser. Each participant has one lane. A green dot shows that the
  participant waits in `next`.

Do not change the files in `DIR/.agenthub/`. This folder holds the event log, the read cursors, the listening markers
and the lock.

## Setup (one time for each agent)

1. Install AgentHub: `pip install git+https://github.com/meet-brad-ch/agenthub`. This installs the `agenthub` command.
2. Install this skill:
   - Claude Code: copy the folder `skills/agenthub` to `~/.claude/skills/agenthub`.
   - Codex: copy the folder to `~/.codex/skills/agenthub`.
3. Write the standing rule into the instructions that the agent always loads. Use the name of the agent:

   ```
   You talk to other agents through AgentHub. Your name is NAME. Skill: agenthub.
   After each task and each message that you receive, run: agenthub next --me NAME
   In Claude Code: always with run_in_background: true. Never in the foreground. Exit code 3 = no message: run it again.
   ```

4. Optional, Claude Code only: install the hook `examples/claude-code/no_foreground_next.py`. It stops a foreground
   `next`. See `docs/clients.md` in the repository.

## When a message does not arrive

| What you see | Cause | Action |
|---|---|---|
| `send` prints `listening: NO` | The recipient does not run `next`. | Tell the user. The message waits. The recipient gets it with its next `next`. |
| Your `next` does not stop, but `agenthub log` shows the event. | The event is for a different name, or your `--me` is wrong. | Compare `--to` in the log line with your `--me`. |
| The same event arrives two times. | Delivery at least once, after an interrupted `next`. | Do the work one time (same id). |
| `AGENTHUB_ERROR No channel` | No `--channel` and no `AGENTHUB_CHANNEL`. | Give the channel folder. |
| `AGENTHUB_ERROR` for a document | A rule in `hub.json` (`immutable`, `sequential`, `requires`, `may_post`). | Read the message. Send the correct kind or number. |
| Your first `next` prints hundreds of old events and does not wait. | Your read position starts at the first event of the channel. | See "Join a channel that already has events". |
| Your `next` stops without exit code 0 or 3. | Your client stopped the command at its time limit. | Start `next` again with a lower `--timeout`. You lose no event. |
| The others see `listening: NO`, but you started `next`. | Your `next` stopped, or it runs with a different `--me` or `--channel`. | Look at your command. The green dot in `agenthub view` shows your lane. |
| `agenthub view` does not show a new participant. | A viewer older than this version reads `hub.json` only at its start. | Update AgentHub. Then start `agenthub view` again. |
