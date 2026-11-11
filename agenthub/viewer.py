"""Live, read-only view of one channel in a web browser (`python -m agenthub view`).

A small local HTTP server (standard library only, bound to 127.0.0.1) serves one page and one JSON endpoint:
  GET /                     the page (viewer.html next to this file)
  GET /api/state?after=N    the events with id > N, the participants (hub.json is read again when it changes),
                            who is listening now, and the read cursors
The page asks for new events once per second, so a new message appears within about one second. The viewer never
writes to the channel: it does not take the lock, does not move cursors and does not touch listening markers.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .core import Event, Hub, HubError

PAGE_PATH = Path(__file__).with_name("viewer.html")
HOST = "127.0.0.1"
DEFAULT_PORT = 8770
JSON_TYPE = "application/json; charset=utf-8"
HTML_TYPE = "text/html; charset=utf-8"
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR = 500


class EventTail:
    """The event log of one channel, read incrementally: only the bytes appended since the last read are parsed."""

    def __init__(self, hub: Hub) -> None:
        """@param hub the channel whose events.jsonl is read (never written)."""
        self.hub: Hub = hub
        self.offset: int = 0                      # bytes of the log already parsed
        self.events: list[Event] = []             # every event read so far, ids 1, 2, 3, ...
        self.guard: threading.Lock = threading.Lock()

    def refresh(self) -> None:
        """Parse the complete lines appended since the last call.

        @throws HubError when the ids are not consecutive.
        @throws OSError when the log cannot be read.
        @throws json.JSONDecodeError, TypeError when a complete line is not an event.
        """
        path = self.hub.events_path
        if not path.exists():
            return
        with self.guard:
            appended = self._read_appended(path)
            # A line that another process is still writing has no line end yet; it is read on the next call.
            last_line_end = appended.rfind(b"\n")
            if last_line_end < 0:
                return
            complete = appended[:last_line_end + 1]
            self.offset += len(complete)
            lines = complete.decode("utf-8").splitlines()
            for line in lines:
                self._add_line(path, line)

    def after(self, event_id: int) -> list[Event]:
        """The events with id > event_id. @param event_id the last id the caller has (0 = none)."""
        with self.guard:
            # Ids are 1, 2, 3, ... so the events after event_id start at list index event_id.
            start = max(event_id, 0)
            return list(self.events[start:])

    def _read_appended(self, path: Path) -> bytes:
        """The bytes of the log after `offset`. @throws OSError when the log cannot be read."""
        with path.open("rb") as handle:
            handle.seek(self.offset)
            return handle.read()

    def _add_line(self, path: Path, line: str) -> None:
        """Append the event of one complete line. @throws HubError when its id is not the next id."""
        if not line.strip():
            return
        record = json.loads(line)
        event = Event(**record)
        expected = len(self.events) + 1
        HubError.require(event.id == expected, f"{path}: event id {event.id}, expected {expected}")
        self.events.append(event)


class ViewerState:
    """Builds the JSON answer of /api/state from the channel."""

    def __init__(self, hub: Hub) -> None:
        """@param hub the channel to show."""
        self.hub: Hub = hub
        self.tail: EventTail = EventTail(hub)

    def snapshot(self, after: int) -> dict:
        """New events after `after` plus the live participant state.

        @param after the last event id the page has (0 = none).
        @throws HubError, OSError as EventTail.refresh and Hub.reload_config.
        """
        self.hub.reload_config()                  # a participant added to hub.json after the start is shown
        self.tail.refresh()
        names = sorted(self.hub.config.participants)
        listening = {name: self.hub.listening(name) for name in names}
        new_events = self.tail.after(after)
        events = [asdict(event) for event in new_events]
        last_id = len(self.tail.events)
        cursors = self.hub.cursors()
        return {"channel": str(self.hub.channel), "participants": names, "listening": listening,
                "cursors": cursors, "last_id": last_id, "events": events}


class ViewerHandler(BaseHTTPRequestHandler):
    """Answers the page and the state requests of one connection. The state comes from the server."""

    server: ViewerServer

    def do_GET(self) -> None:  # noqa: N802 (name required by BaseHTTPRequestHandler)
        """Serve the page, the state, or an error answer."""
        url = urlparse(self.path)
        if url.path == "/":
            page = PAGE_PATH.read_bytes()
            self._send(HTTP_OK, HTML_TYPE, page)
            return
        if url.path != "/api/state":
            self._send(HTTP_NOT_FOUND, JSON_TYPE, b'{"error": "not found"}')
            return
        self._send_state(url.query)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 (signature of the base class)
        """Silence the request log: an open page asks once per second."""
        return

    def _send_state(self, query_text: str) -> None:
        """Answer /api/state?after=N; a bad N is 400, a broken log is 500 with its message."""
        query = parse_qs(query_text)
        after_text = query.get("after", ["0"])[0]
        if not after_text.isdigit():
            self._send(HTTP_BAD_REQUEST, JSON_TYPE, b'{"error": "after must be a non-negative integer"}')
            return
        after = int(after_text)
        try:
            snapshot = self.server.state.snapshot(after)
        except HubError as error:  # A broken log is reported to the page, not hidden.
            message = json.dumps({"error": str(error)}).encode("utf-8")
            self._send(HTTP_SERVER_ERROR, JSON_TYPE, message)
            return
        payload = json.dumps(snapshot).encode("utf-8")
        self._send(HTTP_OK, JSON_TYPE, payload)

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        """Write one complete answer with no caching."""
        length = str(len(body))
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", length)
        self.end_headers()
        self.wfile.write(body)


class ViewerServer(ThreadingHTTPServer):
    """The viewer server on 127.0.0.1, one thread per connection."""

    def __init__(self, hub: Hub, port: int) -> None:
        """@param port the TCP port (0 = any free port). @throws OSError when the port is taken."""
        self.state: ViewerState = ViewerState(hub)
        super().__init__((HOST, port), ViewerHandler)

    def url(self) -> str:
        """The address of the page."""
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"
