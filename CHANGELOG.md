# Changelog

## Unreleased

- `view` reads `hub.json` again when the file changes. A participant that is added while the viewer runs now appears
  without a restart. A broken `hub.json` gives an error answer (HTTP 500 with the message) and does not show stale data.
  New method `Hub.reload_config()`.
- Docs (skill and `docs/clients.md`): how an agent joins a channel that already has events (the first `next` prints
  all earlier events; write it to a file and search it), the time limit of a client (a stopped `next` gives no exit
  code 3, no event is lost), a client without background commands, and how to check that an agent listens. New rows in
  the troubleshooting table.

## 0.1.0 — 2026-11-10

First public release.

- `send` and `next` send messages between agents through a shared channel folder. There is no server.
- If unread messages exist, `next` prints them at once. If not, `next` waits. It stops approximately 0.2 s after a
  message arrives. It stops with exit code 3 at `--timeout`.
- `send` applies the rules in `hub.json`: the participants, the permitted kinds and the numbers of the documents. It
  tells if each recipient listens at that time.
- Documents (for example `task{n}.md`, `response{n}.md`) are usual files in the channel folder.
- AgentHub delivers each message at least once. The read cursor moves only after `next` has written its output.
- The commands `init`, `log` and `view`. `view` shows a live view of the messages in the browser. It only reads.
- The option `--version`.
- The skill `skills/agenthub` for the agents (Claude Code and Codex).
- `docs/clients.md`: how to set up each client.
- An example Claude Code hook. It stops a foreground `next`.
