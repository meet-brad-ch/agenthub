"""Rules, storage and delivery of the Hub in one process."""
import io
import json
import threading
import time

import pytest

from agenthub.core import CONFIG_NAME, Hub, HubConfig, HubError, PostRequest

SHORT_TIMEOUT_S = 0.3
MAX_WAKE_LATENCY_S = 1.0


def test_task_is_written_and_logged(hub, channel):
    event = hub.send(PostRequest("scientist", "task", "# Task 1\nDo it.", title="first")).event
    assert event.id == 1 and event.file == "task1.md"
    assert (channel / "task1.md").read_text(encoding="utf-8") == "# Task 1\nDo it.\n"
    assert "AGENTHUB #1 scientist->* task 1" in event.summary()


def test_tasks_are_immutable_and_sequential(hub):
    hub.send(PostRequest("scientist", "task", "one", number=1))
    with pytest.raises(HubError, match="immutable"):
        hub.send(PostRequest("scientist", "task", "again", number=1))
    with pytest.raises(HubError, match="sequential"):
        hub.send(PostRequest("scientist", "task", "three", number=3))
    assert len(hub.events()) == 1


def test_response_needs_its_task_and_may_be_updated(hub, channel):
    with pytest.raises(HubError, match="task1.md does not exist"):
        hub.send(PostRequest("engineer", "response", "Status: ACCEPTED", number=1))
    hub.send(PostRequest("scientist", "task", "one", number=1))
    hub.send(PostRequest("engineer", "response", "Status: ACCEPTED", number=1, status="ACCEPTED"))
    hub.send(PostRequest("engineer", "response", "Status: DONE", number=1, status="DONE"))
    assert (channel / "response1.md").read_text(encoding="utf-8") == "Status: DONE\n"


def test_permissions_and_recipients(hub):
    with pytest.raises(HubError, match="may not post"):
        hub.send(PostRequest("engineer", "task", "x", number=1))
    with pytest.raises(HubError, match="Unknown participant"):
        hub.send(PostRequest("intruder", "message", "x"))
    with pytest.raises(HubError, match="Unknown recipient"):
        hub.send(PostRequest("engineer", "message", "x", to="nobody"))
    with pytest.raises(HubError, match="itself"):
        hub.send(PostRequest("engineer", "message", "x", to="engineer"))
    assert hub.events() == []


def test_failed_send_writes_nothing(hub, channel):
    with pytest.raises(HubError):
        hub.send(PostRequest("scientist", "task", "   ", number=1))
    assert not (channel / "task1.md").exists() and hub.events() == []


def test_next_delivers_unread_at_once_and_moves_cursor(hub, channel):
    hub.send(PostRequest("scientist", "message", "to engineer"))
    hub.send(PostRequest("engineer", "message", "to scientist"))
    out = io.StringIO()
    events = hub.next("engineer", 5, out)
    assert [e.body for e in events] == ["to engineer"] and "to engineer" in out.getvalue()
    assert Hub(channel).unread("engineer") == []
    assert [e.body for e in Hub(channel).unread("scientist")] == ["to scientist"]


def test_next_prints_document_path_not_body(hub, channel):
    hub.send(PostRequest("scientist", "task", "long task text", number=1))
    out = io.StringIO()
    hub.next("engineer", 5, out)
    assert f"document: {channel / 'task1.md'}" in out.getvalue() and "long task text" not in out.getvalue()


def test_next_times_out(hub):
    start = time.monotonic()
    assert hub.next("engineer", SHORT_TIMEOUT_S, io.StringIO()) == []
    assert time.monotonic() - start >= SHORT_TIMEOUT_S


def test_next_wakes_on_send_and_listening_is_visible(hub):
    result = {}

    def receive():
        result["events"] = hub.next("engineer", 10, io.StringIO())
        result["time"] = time.monotonic()
    thread = threading.Thread(target=receive)
    thread.start()
    time.sleep(0.5)
    sent = hub.send(PostRequest("scientist", "message", "now"))
    start = time.monotonic()
    thread.join(10)
    assert sent.listening == {"engineer": True}
    assert [e.body for e in result["events"]] == ["now"] and result["time"] - start < MAX_WAKE_LATENCY_S
    assert not hub.listening("engineer")


def test_sender_sees_deaf_recipient(hub):
    assert hub.send(PostRequest("scientist", "message", "anyone?")).listening == {"engineer": False}


def test_message_to_other_participant_is_not_delivered(hub):
    config = HubConfig.default_json()
    config["participants"]["reviewer"] = {"may_post": ["message"]}
    (hub.channel / CONFIG_NAME).write_text(json.dumps(config), encoding="utf-8")
    hub3 = Hub(hub.channel)
    hub3.send(PostRequest("scientist", "message", "for reviewer", to="reviewer"))
    assert hub3.next("engineer", SHORT_TIMEOUT_S, io.StringIO()) == []
    assert [e.body for e in hub3.next("reviewer", 1, io.StringIO())] == ["for reviewer"]


def test_unicode_body_round_trip(hub):
    hub.send(PostRequest("scientist", "message", "Köhne ≤ 5 % → ok"))
    assert hub.next("engineer", 1, io.StringIO())[0].body == "Köhne ≤ 5 % → ok"


def test_partial_last_line_is_ignored(hub):
    hub.send(PostRequest("scientist", "message", "one"))
    with hub.events_path.open("ab") as handle:
        handle.write(b'{"id": 2, "time_utc"')
    assert [e.id for e in hub.events()] == [1]


def test_config_rejects_defects():
    with pytest.raises(HubError, match="two participants"):
        HubConfig.from_json({"participants": {"a": {"may_post": []}}})
    with pytest.raises(HubError, match="unknown kinds"):
        HubConfig.from_json({"participants": {"a": {"may_post": ["x"]}, "b": {"may_post": []}}})
    with pytest.raises(HubError, match="exactly one"):
        HubConfig.from_json({"participants": {"a": {}, "b": {}}, "documents": {"t": {"pattern": "t.md"}}})
    with pytest.raises(HubError, match="bare file name"):
        HubConfig.from_json({"participants": {"a": {}, "b": {}}, "documents": {"t": {"pattern": "../t{n}.md"}}})


def test_corrupt_log_is_rejected(hub):
    hub.send(PostRequest("scientist", "message", "one"))
    log = hub.events_path
    log.write_text(log.read_text(encoding="utf-8").replace('"id": 1', '"id": 5'), encoding="utf-8")
    with pytest.raises(HubError, match="consecutive"):
        hub.events()
