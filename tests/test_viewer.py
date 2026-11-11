"""The live viewer: the page, the incremental state endpoint, listening markers, a half-written line, bad input."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from agenthub.core import CONFIG_NAME, LISTEN_PREFIX, PostRequest
from agenthub.viewer import ViewerServer

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR = 500
SECOND_EVENT = 2


@pytest.fixture
def served(hub):
    server = ViewerServer(hub, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = server.url().rstrip("/")
    yield hub, base
    server.shutdown()
    server.server_close()


def get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def state(base: str, after: int) -> dict:
    status, body = get(f"{base}/api/state?after={after}")
    assert status == HTTP_OK
    return json.loads(body)


def test_page_is_served(served):
    _, base = served
    status, body = get(base + "/")
    assert status == HTTP_OK and b"AgentHub Live" in body


def test_events_arrive_incrementally(served):
    hub, base = served
    first = state(base, 0)
    assert first["events"] == [] and first["last_id"] == 0 and first["participants"] == sorted(hub.config.participants)
    hub.send(PostRequest("scientist", "task", "one", title="first"))
    hub.send(PostRequest("engineer", "message", "hello", to="scientist", title="second"))
    both = state(base, 0)
    assert [e["id"] for e in both["events"]] == [1, 2] and both["events"][1]["body"] == "hello"
    newer = state(base, 1)
    assert [e["id"] for e in newer["events"]] == [2] and newer["last_id"] == SECOND_EVENT
    assert state(base, 2)["events"] == []


def test_half_written_line_waits(served):
    hub, base = served
    hub.send(PostRequest("scientist", "message", "complete", title="ok"))
    with hub.events_path.open("ab") as handle:
        handle.write(b'{"id": 2, "time_utc": "x"')            # no line end yet: another process is still writing
    assert [e["id"] for e in state(base, 0)["events"]] == [1]


def test_listening_marker_is_shown(served):
    hub, base = served
    assert state(base, 0)["listening"]["engineer"] is False
    (hub.state / f"{LISTEN_PREFIX}engineer").write_text("pid", encoding="utf-8")
    assert state(base, 0)["listening"]["engineer"] is True


def test_bad_requests(served):
    _, base = served
    assert get(base + "/api/state?after=-1")[0] == HTTP_BAD_REQUEST
    assert get(base + "/nothing")[0] == HTTP_NOT_FOUND


def test_participant_added_to_hub_json_is_shown(served):
    hub, base = served
    assert "g" not in state(base, 0)["participants"]
    config_path = hub.channel / CONFIG_NAME
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data["participants"]["g"] = {"may_post": ["message"]}
    config_path.write_text(json.dumps(data), encoding="utf-8")
    shown = state(base, 0)
    assert "g" in shown["participants"] and shown["listening"]["g"] is False


def test_broken_hub_json_is_reported(served):
    hub, base = served
    (hub.channel / CONFIG_NAME).write_text("{not json", encoding="utf-8")
    status, body = get(f"{base}/api/state?after=0")
    assert status == HTTP_SERVER_ERROR and b"hub.json" in body
