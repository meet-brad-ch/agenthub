# AgentHub

AgentHub sends messages between coding agents on one machine. Example: a Codex agent and a Claude Code agent that work
on the same project in turns. A message wakes its recipient at once. The messages need no server. A shared channel
folder holds all the state.

- Agents use two commands: `send` and `next`.
- It needs only the Python standard library (Python 3.10 or later). It runs on Windows, Linux and macOS.
- A live view in the browser shows the messages between the agents as they occur (see "Live view").

## Install

```
pip install git+https://github.com/meet-brad-ch/agenthub
```

This installs the `agenthub` command. `python -m agenthub` does the same.

## Quickstart

```
agenthub init ./channel                 # writes ./channel/hub.json with two example participants
export AGENTHUB_CHANNEL=./channel       # or give --channel ./channel to each command
```

The agent "engineer" waits for work:

```
agenthub next --me engineer
```

The agent "scientist" sends a message to the engineer:

```
echo "Please run the tests." > body.md
agenthub send --me scientist --kind message --to engineer --title "Run tests" --body-file body.md
```

`send` prints `AGENTHUB #1 scientist->engineer message Run tests` and `recipient engineer listening: yes`. The
engineer's `next` prints the message and stops with exit code 0.

## The rule for every agent

**After each task, run `agenthub next`.**

- If unread messages exist, `next` prints them and stops with exit code 0.
- If no message exists, `next` waits. It stops approximately 0.2 s after a message arrives.
- If no message arrives within `--timeout` seconds (default 3000), `next` stops with exit code 3. Run it again.
- For a document, `next` prints the path of the file. Read the file with your usual file tools.
- An interactive client (Claude Code) runs `next` as a background command. When the command stops, the client starts
  the next turn of the agent.
- A headless client (Codex) runs `next` as a usual command.

Each agent needs three items. [docs/clients.md](docs/clients.md) gives all three:
1. The skill [skills/agenthub](skills/agenthub/SKILL.md). It teaches the agent how to use AgentHub.
2. A three-line rule in `CLAUDE.md` or `AGENTS.md`.
3. Optional: a Claude Code hook that stops a foreground `next`.

## Live view

You can watch the messages between the agents while they work. Start the view in a separate terminal:

```
agenthub view                           # or: agenthub view --channel ./channel --port 8770
```

The command starts a small web server on your computer and opens the page `http://127.0.0.1:8770` in your browser.
The server accepts connections only from your own computer. It only reads the channel folder. It does not change the
messages, the read cursors or the listening markers, so the agents work the same with or without the view. Stop it
with Ctrl+C. Use `--no-browser` to start the server without a browser, and `--port P` if port 8770 is in use.

What the page shows:
- **One lane for each participant**, from left to right. The top of each lane shows:
  - a green, pulsing dot when the participant waits in `next` (it listens), and a grey dot when it does not;
  - `read #N`: the last message that the participant has read;
  - `N unread`: the messages for the participant that it has not read yet.
- **One row for each message**, the newest at the bottom. An arrow goes from the lane of the sender (a dot) to the lane
  of the recipient (an arrowhead).
  - A solid arrow is a message to one recipient.
  - A dashed arrow is a message to all other participants.
  - Each participant has its own colour. An arrow has the colour of its sender.
  - The row shows the id, the kind, the number and status of a document, the title and the time.
  - A new message is highlighted for a short time.
- **Click a row** to show the full text of the message. Click again to hide it.
- **Filters** at the top: text (title, body, kind), one participant, one kind.
- **Follow newest** (on at start) scrolls to each new message. When you scroll up, it switches off, so new messages do
  not move the page while you read. Switch it on again to follow.
- The page shows the newest 300 messages. **Show older messages** at the bottom adds older ones.
- A legend under the controls explains the colours, the arrows, the dots and the counters.

The page gets new messages once each second. The line at the top shows `live` and the number of messages, or
`disconnected` if the server stopped.

Use the view to find problems quickly:
- A grey dot on a lane means that this agent does not listen. Its messages wait until it runs `next` again.
- An unread count that increases means that the agent does not read its messages.
- A message on the wrong lane means a wrong `--to` or a wrong `--me`.

## Commands

| Command | What it does |
|---|---|
| `agenthub send --me NAME --kind KIND --body-file FILE [--to T] [--number N] [--status S] [--title T]` | Sends a message or a document. Prints the event and tells if each recipient listens. |
| `agenthub next --me NAME [--timeout S]` | Prints your unread events. If none exist, waits for one. Exit code 3 on timeout. |
| `agenthub init DIR` | Writes `DIR/hub.json`. Does not write over an existing file. |
| `agenthub log [--last N]` | Prints one line for each recent event. For people, not for agents. |
| `agenthub view [--port P] [--no-browser]` | Starts the live view at `http://127.0.0.1:P` (default port 8770). See "Live view". |

Each command except `init` takes `--channel DIR`. Without it, the command reads the environment variable
`AGENTHUB_CHANNEL`.

## The channel file hub.json

`hub.json` gives the participants and the kinds that each participant can send. You select all names and kinds. The
code does not use fixed names.

```json
{
  "participants": {
    "scientist": {"may_post": ["task", "message"]},
    "engineer": {"may_post": ["response", "message"]}
  },
  "documents": {
    "task": {"pattern": "task{n}.md", "immutable": true, "sequential": true, "requires": ""},
    "response": {"pattern": "response{n}.md", "immutable": false, "sequential": false, "requires": "task"}
  }
}
```

- The kind `message` is always available. The event log keeps its text. No file is written.
- A document kind writes its text to a file in the channel folder. `pattern` gives the file name. `{n}` is the value of
  `--number`.
  - `immutable`: nobody can write the file again.
  - `sequential`: the numbers must be 1, 2, 3, and so on.
  - `requires`: the document of this kind with the same number must exist first.
- You can have as many participants as necessary. `--to` gives one recipient. Without `--to`, all participants get
  the message.

AgentHub keeps its state in `CHANNEL/.agenthub/`:
- `events.jsonl`: the event log. Each line is one event in JSON. The ids are 1, 2, 3, and so on.
- One read cursor for each participant.
- A listening marker for each participant that waits in `next`.
- The lock file.

## Names of agents

The user gives the names. The names are the keys of `participants` in `hub.json`. The code has no fixed names.
- A name starts with a letter. It contains only letters, digits, `_` and `-`. It has a maximum of 32 characters.
- `send` and `next` stop with an error if a name is not in `hub.json`.
- An agent gets its name from its standing instructions ("your name is NAME", see [docs/clients.md](docs/clients.md)).
  It gives the name with `--me`.
- Give each running agent its own name. Two sessions with the same name use one read cursor. The session that runs
  `next` first gets the message. The other session never sees it.
- For a second agent with the same role, add a second name. Example: `engineer` and `engineer2`.
- Use short role names (`scientist`, `engineer`, `reviewer`). Do not use model names. A role name stays correct when
  you change the model.

## Design

- **No server for the messages.** (The optional live view is a small local web server that only reads.) Only the
  harness of an agent can wake the agent. The harness wakes the agent when a command that the
  agent started stops. A server push (MCP notification, SSE) gets to the client program, but it does not start a
  model turn. Thus the only way to receive is a command that stops when a message arrives (`next`). A folder replaces
  the server. You do not start or monitor a process.
- **One way to receive.** A blocking tool call stops an interactive session. A pipe such as `watch | head -1` can wake
  an agent one message late. A full inbox can be too large to read. `next` prevents all three problems. It runs in the
  background, and it prints only the unread events. It prints documents as paths.
- **Delivery at least once.** The read cursor moves only after `next` has written its output. After a crash, an agent
  can get a message two times. It cannot lose a message.
- **Deaf recipients are visible.** `send` tells if each recipient waits in `next` at that time. While `next` waits, it
  writes its listening marker again every few seconds. If the process stops, the marker becomes old, and the recipient
  shows as "not listening".
- **Concurrency.** Each write to the log and to a cursor holds an operating-system file lock. Windows uses `msvcrt`.
  Other systems use `fcntl`.
- **Limits.** `next` reads the size of the log every 0.2 s. This is local file access, not polling by the agent. Each
  new event makes `next` read the full log again. A log of a few MB (thousands of events) causes no problem. For much
  longer conversations, start a new channel folder.
- **The viewer** requests `/api/state?after=N` once each second. It reads only the new part of the log. It accepts
  connections only from 127.0.0.1. It writes nothing.

## Development

```
pip install -e ".[dev]"
python -m coverage run -m pytest -q tests && python -m coverage combine && python -m coverage report
python -m ruff check agenthub tests examples
python -m lizard -C 10 -L 60 -a 6 -w agenthub tests examples
```

Files:
- `agenthub/core.py`: the class Hub. It contains the hub.json rules, the event log, the document files, the read
  cursors, the file lock, the listening marker, `send` and `next`.
- `agenthub/__main__.py`: the commands.
- `agenthub/viewer.py`, `agenthub/viewer.html`: the live viewer.
- `skills/agenthub/SKILL.md`: the skill for the agents (Claude Code and Codex).
- `examples/claude-code/`: the example hook.
- `tests/`: the tests.
  - The rules and the delivery, in one process.
  - The commands in separate processes: wake time, timeout, a deaf recipient, two writers at the same time.
  - The viewer and the example hook.

## License

MIT. See [LICENSE](LICENSE).
