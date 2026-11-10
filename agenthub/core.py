"""Rules and storage of one AgentHub channel. No server: the channel folder is the only state.

A channel folder holds hub.json (the rules), the documents that the agents read (for example task1.md,
response1.md) and a hidden `.agenthub/` subfolder with:
  events.jsonl   append-only event log, ids 1, 2, 3, ... in order
  cursors.json   per participant: the id of the last event delivered by `next`
  lock           an OS file lock taken around every write, so any number of processes can share the channel
  listen-NAME    present while NAME waits in `next`; refreshed every few seconds (the "listening" signal)

Every agent uses the channel the same way: `send` to post, `next` to receive (it waits until a message is there).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

CONFIG_NAME = "hub.json"
STATE_DIR = ".agenthub"
EVENTS_NAME = "events.jsonl"
CURSORS_NAME = "cursors.json"
LOCK_NAME = "lock"
LISTEN_PREFIX = "listen-"
BROADCAST = "*"
MESSAGE_KIND = "message"
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
MIN_PARTICIPANTS = 2
MAX_TITLE_CHARS = 200
MAX_STATUS_CHARS = 64
LOCK_TIMEOUT_S = 30.0
LOCK_RETRY_S = 0.01
POLL_S = 0.2
HEARTBEAT_S = 5.0
LISTEN_STALE_S = 15.0
EVENT_SEPARATOR = "-" * 72


class HubError(ValueError):
    """A request that breaks a hub rule. The message says which rule."""

    @staticmethod
    def require(ok: bool, message: str) -> None:
        """Raise HubError(message) when `ok` is false.

        @param ok the checked condition.
        @param message the rule that `ok` false breaks, with the bad value.
        @throws HubError when `ok` is false.
        """
        if not ok:
            raise HubError(message)


class Files:
    """File writes that a concurrent reader must never see half done."""

    @staticmethod
    def write_atomic(path: Path, text: str) -> None:
        """Write `text` through a temporary file in the same folder, then rename it over `path`.

        @param path the target file.
        @param text the full new content (UTF-8, LF line ends).
        @throws OSError when the write, the flush or the rename fails.
        """
        temporary = path.with_name(path.name + ".agenthub-tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    @staticmethod
    def append_line(path: Path, line: str) -> None:
        """Append one line to `path` and force it to the disk.

        @param path the log file; created when absent.
        @param line the text without its line end.
        @throws OSError when the write or the flush fails.
        """
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class FileLock:
    """An exclusive OS lock on one file, shared by every process that opens the same channel."""

    def __init__(self, path: Path) -> None:
        """@param path the lock file; created when absent."""
        self.path = path

    @contextmanager
    def held(self) -> Iterator[None]:
        """Hold the lock for the body of a `with` statement.

        @throws HubError when the lock is not free within LOCK_TIMEOUT_S.
        @throws OSError when the lock file cannot be opened.
        """
        with self.path.open("a+b") as handle:
            self._acquire(handle)
            try:
                yield
            finally:
                self._release(handle)

    @staticmethod
    def _acquire(handle) -> None:
        """Take the lock on byte 0 of `handle`, retrying until LOCK_TIMEOUT_S."""
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        # Ends when the lock is taken or the deadline passes.
        while True:
            if FileLock._try_lock(handle):
                return
            HubError.require(time.monotonic() < deadline, f"Channel lock busy for more than {LOCK_TIMEOUT_S:.0f} s")
            time.sleep(LOCK_RETRY_S)

    @staticmethod
    def _try_lock(handle) -> bool:
        """One non-blocking lock attempt. @returns true when the lock is held now."""
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:  # Held by another process: the caller retries.
                return False
            return True
        import fcntl
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # Held by another process: the caller retries.
            return False
        return True

    @staticmethod
    def _release(handle) -> None:
        """Release the lock taken by _try_lock."""
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class DocumentKind:
    """A kind of post that is also written to a file in the channel folder.

    pattern: file name with `{n}` for the number, for example `task{n}.md`.
    immutable: when true, an existing file of this kind is never replaced.
    sequential: when true, number n needs the file for n-1 (n > 1) and n starts at 1.
    requires: another kind whose file with the same number must exist first, or "".
    """

    name: str
    pattern: str
    immutable: bool
    sequential: bool
    requires: str

    @staticmethod
    def from_json(name: str, spec: dict) -> DocumentKind:
        """Parse one entry of hub.json "documents".

        @param name the kind name (the key of the entry).
        @param spec the entry: pattern, immutable, sequential, requires.
        @throws HubError when the name or the pattern is not valid.
        """
        HubError.require(NAME_PATTERN.match(name) is not None, f"Bad document kind name {name!r}")
        HubError.require(name != MESSAGE_KIND, f"'{MESSAGE_KIND}' is reserved, not a document kind")
        pattern = str(spec.get("pattern", ""))
        HubError.require(pattern.count("{n}") == 1, f"Kind {name}: pattern needs exactly one {{n}}")
        has_folder = "/" in pattern or "\\" in pattern
        HubError.require(not has_folder, f"Kind {name}: pattern must be a bare file name")
        return DocumentKind(
            name=name,
            pattern=pattern,
            immutable=bool(spec.get("immutable", False)),
            sequential=bool(spec.get("sequential", False)),
            requires=str(spec.get("requires", "")),
        )


@dataclass(frozen=True)
class Participant:
    """An agent name and the post kinds it may send."""

    name: str
    may_post: tuple[str, ...]

    @staticmethod
    def from_json(name: str, spec: dict, known_kinds: set[str]) -> Participant:
        """Parse one entry of hub.json "participants".

        @param name the participant name (the key of the entry).
        @param spec the entry: may_post, a list of kind names.
        @param known_kinds the document kinds of the config plus "message".
        @throws HubError when the name is not valid or a kind is unknown.
        """
        HubError.require(NAME_PATTERN.match(name) is not None, f"Bad participant name {name!r}")
        may_post = tuple(str(k) for k in spec.get("may_post", []))
        unknown = [k for k in may_post if k not in known_kinds]
        HubError.require(not unknown, f"Participant {name}: unknown kinds {unknown}")
        return Participant(name=name, may_post=may_post)


@dataclass(frozen=True)
class HubConfig:
    """The channel rules, read from hub.json."""

    participants: dict[str, Participant]
    documents: dict[str, DocumentKind]

    @staticmethod
    def from_json(data: dict) -> HubConfig:
        """Parse and check a hub.json object.

        @param data the parsed JSON object with "participants" and "documents".
        @throws HubError on any defect: a bad name or pattern, an unknown kind, fewer than two participants.
        """
        documents: dict[str, DocumentKind] = {}
        for name, spec in dict(data.get("documents", {})).items():
            documents[name] = DocumentKind.from_json(name, spec)
        for kind in documents.values():
            known_requirement = kind.requires == "" or kind.requires in documents
            HubError.require(known_requirement, f"Kind {kind.name}: unknown requires {kind.requires!r}")
        known_kinds = set(documents) | {MESSAGE_KIND}
        participants: dict[str, Participant] = {}
        for name, spec in dict(data.get("participants", {})).items():
            participants[name] = Participant.from_json(name, spec, known_kinds)
        HubError.require(len(participants) >= MIN_PARTICIPANTS, "hub.json needs at least two participants")
        return HubConfig(participants=participants, documents=documents)

    @staticmethod
    def default_json() -> dict:
        """The hub.json that `agenthub init` writes: a scientist issues tasks, an engineer answers.
        Names and kinds can be edited freely; no code depends on them."""
        return {
            "participants": {
                "scientist": {"may_post": ["task", MESSAGE_KIND]},
                "engineer": {"may_post": ["response", MESSAGE_KIND]},
            },
            "documents": {
                "task": {"pattern": "task{n}.md", "immutable": True, "sequential": True, "requires": ""},
                "response": {"pattern": "response{n}.md", "immutable": False, "sequential": False,
                             "requires": "task"},
            },
        }


@dataclass(frozen=True)
class PostRequest:
    """What a participant asks to send.

    kind: a document kind or "message". to: a participant name or "*" (all others).
    number: the document number; 0 on a sequential kind means the next free number; for a message
    an optional reference. status, title: one line each, may be empty.
    """

    sender: str
    kind: str
    body: str
    to: str = BROADCAST
    number: int = 0
    status: str = ""
    title: str = ""


@dataclass(frozen=True)
class Event:
    """One posted message. `file` is the document written for it, relative to the channel, or ""."""

    id: int
    time_utc: str
    sender: str
    to: str
    kind: str
    number: int
    status: str
    title: str
    body: str
    file: str

    def summary(self) -> str:
        """One line for notifications and the log listing. Never contains the body."""
        number = f" {self.number}" if self.number else ""
        status = f" [{self.status}]" if self.status else ""
        file = f" file={self.file}" if self.file else ""
        title = f" {self.title}" if self.title else ""
        return f"AGENTHUB #{self.id} {self.sender}->{self.to} {self.kind}{number}{status}{title}{file}"


@dataclass(frozen=True)
class SendResult:
    """The stored event and whether each recipient was waiting in `next` at the time of sending."""

    event: Event
    listening: dict[str, bool]


class Hub:
    """One channel folder. Safe for many processes at once: every write holds the channel lock."""

    def __init__(self, channel: Path) -> None:
        """Load hub.json of `channel` and make the state folder.

        @param channel the channel folder; must exist and hold hub.json.
        @throws HubError when the folder or hub.json is missing or invalid.
        @throws OSError when a file cannot be read or the state folder cannot be made.
        """
        self.channel = Path(channel).resolve()
        HubError.require(self.channel.is_dir(), f"Channel folder does not exist: {self.channel}")
        config_path = self.channel / CONFIG_NAME
        HubError.require(config_path.is_file(), f"Missing {config_path}; run 'agenthub init' first")
        self.config = HubConfig.from_json(json.loads(config_path.read_text(encoding="utf-8")))
        self.state = self.channel / STATE_DIR
        self.state.mkdir(exist_ok=True)
        self.events_path = self.state / EVENTS_NAME
        self.cursors_path = self.state / CURSORS_NAME
        self.lock = FileLock(self.state / LOCK_NAME)

    # ----- reading ---------------------------------------------------------------------------

    def events(self) -> list[Event]:
        """The whole event log. @throws HubError when the ids are not 1, 2, 3, ... in order."""
        events: list[Event] = []
        if not self.events_path.exists():
            return events
        raw = self.events_path.read_bytes()
        # Readers do not take the lock: a line that another process is still appending has no line end yet.
        # It is cut off here and read on the next call.
        last_line_end = raw.rfind(b"\n")
        complete_length = last_line_end + 1
        complete = raw[:complete_length]
        lines = complete.decode("utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            event = Event(**json.loads(line))
            where = f"{self.events_path}:{line_number}"
            HubError.require(event.id == len(events) + 1, f"{where}: event ids are not consecutive")
            events.append(event)
        return events

    def participant(self, name: str) -> Participant:
        """The participant with this name. @throws HubError for an unknown name."""
        known = sorted(self.config.participants)
        HubError.require(name in self.config.participants, f"Unknown participant {name!r}; known: {known}")
        return self.config.participants[name]

    def document_path(self, kind: str, number: int) -> Path:
        """The file of document kind `kind` with number `number`. @throws KeyError for an unknown kind."""
        return self.channel / self.config.documents[kind].pattern.format(n=number)

    def cursors(self) -> dict[str, int]:
        """Participant name -> id of the last event delivered to it."""
        if not self.cursors_path.exists():
            return {}
        data = json.loads(self.cursors_path.read_text(encoding="utf-8"))
        return {str(name): int(event_id) for name, event_id in data.items()}

    @staticmethod
    def addressed(event: Event, me: str) -> bool:
        """True when `event` comes from another participant and is sent to `me` or to all."""
        return event.sender != me and event.to in (me, BROADCAST)

    def unread(self, me: str) -> list[Event]:
        """Events addressed to `me` after its cursor. Does not move the cursor.
        @throws HubError for an unknown participant."""
        self.participant(me)
        start = self.cursors().get(me, 0)
        newer = self.events()[start:]
        unread: list[Event] = []
        for event in newer:
            if self.addressed(event, me):
                unread.append(event)
        return unread

    # ----- sending ---------------------------------------------------------------------------

    def send(self, request: PostRequest) -> SendResult:
        """Check the rules, write the document if the kind is a document kind, and append the event.
        Nothing is written when a rule is broken.

        @param request the post.
        @returns the stored event and the listening state of every recipient.
        @throws HubError on a broken rule: permission, recipient, title/status form, number, file state.
        @throws OSError when a file write fails.
        """
        self._check_header(request)
        with self.lock.held():
            event_id = len(self.events()) + 1
            number = request.number
            file = ""
            if request.kind == MESSAGE_KIND:
                HubError.require(number >= 0, "number must be >= 0")
                HubError.require(request.body.strip() != "", "A message needs a body")
            else:
                number = self._resolve_number(request.kind, number)
                file = self._write_document(request.kind, number, request.body)
            time_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            event = Event(id=event_id, time_utc=time_utc, sender=request.sender, to=request.to, kind=request.kind,
                          number=number, status=request.status, title=request.title, body=request.body, file=file)
            event_line = json.dumps(asdict(event), ensure_ascii=False)
            Files.append_line(self.events_path, event_line)
        listening: dict[str, bool] = {}
        for name in self.config.participants:
            is_recipient = name != request.sender and request.to in (name, BROADCAST)
            if is_recipient:
                listening[name] = self.listening(name)
        return SendResult(event=event, listening=listening)

    def _check_header(self, request: PostRequest) -> None:
        """Check sender permission, recipient, title and status. @throws HubError on a defect."""
        participant = self.participant(request.sender)
        allowed = list(participant.may_post)
        HubError.require(request.kind in participant.may_post,
                         f"{request.sender} may not post {request.kind!r}; allowed: {allowed}")
        known_recipient = request.to == BROADCAST or request.to in self.config.participants
        HubError.require(known_recipient, f"Unknown recipient {request.to!r}")
        HubError.require(request.to != request.sender, "A participant cannot post to itself")
        title_ok = "\n" not in request.title and len(request.title) <= MAX_TITLE_CHARS
        HubError.require(title_ok, f"title must be one line of at most {MAX_TITLE_CHARS} characters")
        status_ok = "\n" not in request.status and len(request.status) <= MAX_STATUS_CHARS
        HubError.require(status_ok, f"status must be one line of at most {MAX_STATUS_CHARS} characters")

    def _resolve_number(self, kind: str, number: int) -> int:
        """Return `number`, except that 0 on a sequential kind means the next free number."""
        if number != 0 or not self.config.documents[kind].sequential:
            return number
        # The next free number is at most one more than the count of files in the folder.
        file_count = sum(1 for _ in self.channel.iterdir())
        for candidate in range(1, file_count + 2):
            if not self.document_path(kind, candidate).exists():
                return candidate
        raise HubError(f"No free {kind} number found among {file_count + 1} candidates")

    def _write_document(self, kind: str, number: int, body: str) -> str:
        """Check the file rules of `kind` and write the document atomically.

        @returns the file name relative to the channel.
        @throws HubError on a bad number, an empty body or a broken immutable/sequential/requires rule.
        """
        spec = self.config.documents[kind]
        HubError.require(number >= 1, f"Kind {kind} needs a number >= 1")
        HubError.require(body.strip() != "", f"A {kind} needs a body")
        path = self.document_path(kind, number)
        if spec.immutable:
            immutable_message = f"{path.name} exists and {kind} files are immutable; use the next number"
            HubError.require(not path.exists(), immutable_message)
        if spec.sequential and number > 1:
            previous = self.document_path(kind, number - 1)
            HubError.require(previous.exists(), f"{kind} {number - 1} does not exist; {kind} numbers are sequential")
        if spec.requires:
            needed = self.document_path(spec.requires, number)
            requires_message = f"{needed.name} does not exist; {kind} {number} answers {spec.requires} {number}"
            HubError.require(needed.exists(), requires_message)
        text = body
        if not text.endswith("\n"):
            text = text + "\n"
        Files.write_atomic(path, text)
        return path.name

    # ----- receiving -------------------------------------------------------------------------

    def listening(self, name: str) -> bool:
        """True while `name` waits in `next` (its marker was refreshed within LISTEN_STALE_S)."""
        marker = self.state / f"{LISTEN_PREFIX}{name}"
        try:
            age = time.time() - marker.stat().st_mtime
        except FileNotFoundError:  # No marker: nobody waits.
            return False
        return age <= LISTEN_STALE_S

    def next(self, me: str, timeout_s: float, out: TextIO) -> list[Event]:
        """Deliver the unread events of `me`: write them to `out`, then move the cursor past them.
        Waits until at least one is unread or `timeout_s` passes. Delivery is at-least-once: the cursor
        moves only after `out` is flushed.

        @param me the receiving participant.
        @param timeout_s the longest wait in seconds, > 0.
        @param out where the events are written.
        @returns the delivered events, or [] on timeout.
        @throws HubError for an unknown participant or a timeout <= 0.
        """
        self.participant(me)
        HubError.require(timeout_s > 0, "timeout must be > 0")
        marker = self.state / f"{LISTEN_PREFIX}{me}"
        deadline = time.monotonic() + timeout_s
        last_beat = 0.0
        seen_size = -1
        try:
            # Ends when events are delivered or the deadline passes.
            while True:
                if time.monotonic() - last_beat >= HEARTBEAT_S:
                    marker_text = f"pid {os.getpid()}\n"
                    Files.write_atomic(marker, marker_text)
                    last_beat = time.monotonic()
                size = self._log_size()
                # The log is parsed only when its size changed since the last look.
                if size != seen_size:
                    seen_size = size
                    events = self._deliver(me, out)
                    if events:
                        return events
                if time.monotonic() >= deadline:
                    return []
                time.sleep(POLL_S)
        finally:
            marker.unlink(missing_ok=True)

    def _log_size(self) -> int:
        """Size of the event log in bytes, 0 when it does not exist yet."""
        if not self.events_path.exists():
            return 0
        return self.events_path.stat().st_size

    def _deliver(self, me: str, out: TextIO) -> list[Event]:
        """Write the unread events of `me` to `out`, then move its cursor past them. @returns them, or []."""
        events = self.unread(me)
        if not events:
            return events
        self._write_events(events, out)
        last_id = events[-1].id
        self._advance_cursor(me, last_id)
        return events

    def _write_events(self, events: list[Event], out: TextIO) -> None:
        """Write each event: summary line, then the document path or the message body. Flushes `out`."""
        for event in events:
            summary = event.summary()
            out.write(summary + "\n")
            if event.file:
                document = self.channel / event.file
                out.write(f"document: {document}\n")
            else:
                body = event.body.rstrip("\n")
                out.write(body + "\n")
            out.write(EVENT_SEPARATOR + "\n")
        out.flush()

    def _advance_cursor(self, me: str, event_id: int) -> None:
        """Set the cursor of `me` to `event_id` (never backwards) under the channel lock."""
        with self.lock.held():
            cursors = self.cursors()
            cursors[me] = max(cursors.get(me, 0), event_id)
            Files.write_atomic(self.cursors_path, json.dumps(cursors, indent=2) + "\n")
