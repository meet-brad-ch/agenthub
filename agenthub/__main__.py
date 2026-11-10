"""AgentHub command line. Every agent uses only `send` and `next`.

python -m agenthub send --channel DIR --me NAME --kind KIND --body-file FILE
                        [--to T] [--number N] [--status S] [--title T]
    Post a message or a document (task, response, ...). Prints the event and whether each recipient is listening.
python -m agenthub next --channel DIR --me NAME [--timeout S]
    Print the unread messages for NAME and exit 0. When there are none, wait until one arrives.
    Exit 3 when nothing arrived within --timeout seconds (default 3000): run it again.
python -m agenthub init DIR        write DIR/hub.json (refuses to overwrite)
python -m agenthub log  --channel DIR [--last N]      one line per recent event (for people, not for agents)
python -m agenthub view --channel DIR [--port P] [--no-browser]
    Live, read-only view of the message flow in a web browser (http://127.0.0.1:P, default 8770). Ctrl+C stops it.

The channel can also come from the environment variable AGENTHUB_CHANNEL.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .core import BROADCAST, CONFIG_NAME, Hub, HubConfig, HubError, PostRequest
from .viewer import DEFAULT_PORT, ViewerServer

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_NO_MESSAGE = 3
DEFAULT_TIMEOUT_S = 3000.0
DEFAULT_LOG_LAST = 20
CHANNEL_ENV = "AGENTHUB_CHANNEL"


class Cli:
    """The commands. Each returns the process exit code."""

    @staticmethod
    def init(channel: Path) -> int:
        """Write the default hub.json into `channel`. Refuses to replace an existing one."""
        path = channel / CONFIG_NAME
        if path.exists():
            print(f"REFUSED: {path} exists", file=sys.stderr)
            return EXIT_ERROR
        channel.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(HubConfig.default_json(), indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {path}")
        return EXIT_OK

    @staticmethod
    def send(hub: Hub, request: PostRequest) -> int:
        """Post `request`; print the event summary and the listening state of each recipient."""
        result = hub.send(request)
        print(result.event.summary())
        for name, listening in result.listening.items():
            state = "yes" if listening else "NO (it will see the message when it next runs `next`)"
            print(f"recipient {name} listening: {state}")
        return EXIT_OK

    @staticmethod
    def next(hub: Hub, me: str, timeout_s: float) -> int:
        """Print the unread messages of `me`, waiting for one if needed. Exit 3 on timeout."""
        events = hub.next(me, timeout_s, sys.stdout)
        if events:
            return EXIT_OK
        print(f"NO_MESSAGE for {me} within {timeout_s:.0f} s; run `next` again")
        return EXIT_NO_MESSAGE

    @staticmethod
    def log(hub: Hub, last: int) -> int:
        """Print the summaries of the newest `last` events. @throws HubError when last < 1."""
        HubError.require(last >= 1, f"--last must be >= 1, got {last}")
        newest = hub.events()[-last:]
        for event in newest:
            print(event.summary())
        return EXIT_OK

    @staticmethod
    def view(hub: Hub, port: int, open_browser: bool) -> int:
        """Serve the live view until Ctrl+C. @throws OSError when the port is taken."""
        server = ViewerServer(hub, port)
        url = server.url()
        print(f"AgentHub view of {hub.channel} at {url} (read only; Ctrl+C stops it)", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:  # The normal way to stop the viewer.
            print("stopped")
        finally:
            server.server_close()
        return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    """The argument parser of the commands."""
    parser = argparse.ArgumentParser(prog="agenthub")
    version_text = f"agenthub {__version__}"
    parser.add_argument("--version", action="version", version=version_text)
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("channel", type=Path)
    for name in ("send", "next", "log", "view"):
        p = sub.add_parser(name)
        p.add_argument("--channel", type=Path, default=os.environ.get(CHANNEL_ENV))
        if name in ("send", "next"):
            p.add_argument("--me", required=True)
    p_send = sub.choices["send"]
    p_send.add_argument("--kind", required=True)
    p_send.add_argument("--body-file", required=True, type=Path)
    p_send.add_argument("--to", default=BROADCAST)
    p_send.add_argument("--number", type=int, default=0)
    p_send.add_argument("--status", default="")
    p_send.add_argument("--title", default="")
    sub.choices["next"].add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    sub.choices["log"].add_argument("--last", type=int, default=DEFAULT_LOG_LAST)
    sub.choices["view"].add_argument("--port", type=int, default=DEFAULT_PORT)
    sub.choices["view"].add_argument("--no-browser", action="store_true")
    return parser


def run(args: argparse.Namespace) -> int:
    """Run the parsed command. @throws HubError on a broken rule or a missing channel."""
    if args.command == "init":
        return Cli.init(args.channel)
    HubError.require(args.channel is not None, f"No channel: pass --channel or set {CHANNEL_ENV}")
    hub = Hub(args.channel)
    if args.command == "send":
        body = args.body_file.read_text(encoding="utf-8")
        request = PostRequest(sender=args.me, kind=args.kind, body=body, to=args.to, number=args.number,
                              status=args.status, title=args.title)
        return Cli.send(hub, request)
    if args.command == "next":
        return Cli.next(hub, args.me, args.timeout)
    if args.command == "view":
        return Cli.view(hub, args.port, not args.no_browser)
    return Cli.log(hub, args.last)


def main(argv: list[str] | None = None) -> int:
    """Entry point: run one command and return its exit code."""
    args = build_parser().parse_args(argv)
    # Message bodies may hold any Unicode; a Windows console code page must not make printing fail.
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        return run(args)
    except HubError as error:  # Program boundary: report the broken rule and exit nonzero.
        print(f"AGENTHUB_ERROR {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
