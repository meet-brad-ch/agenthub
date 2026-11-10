"""The command line across separate processes: the way agents use the hub."""
import subprocess
import sys
import time
from pathlib import Path

from agenthub.core import Hub

REPO = Path(__file__).resolve().parents[1]
MAX_WAKE_LATENCY_S = 2.0
WRITERS = 2
SENDS_PER_WRITER = 150
EXIT_NO_MESSAGE = 3
EXIT_ERROR = 2


def _cli(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "agenthub", *args], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", check=False, **kwargs)


def _body(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "body.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_next_in_one_process_wakes_on_send_from_another(channel, tmp_path):
    command = [sys.executable, "-m", "agenthub", "next", "--channel", str(channel), "--me", "engineer",
               "--timeout", "30"]
    receiver = subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE, text=True, encoding="utf-8")
    deadline = time.monotonic() + 10
    while not Hub(channel).listening("engineer") and time.monotonic() < deadline:
        time.sleep(0.05)
    sent = _cli("send", "--channel", str(channel), "--me", "scientist", "--kind", "message",
                "--body-file", str(_body(tmp_path, "wake up")), "--title", "ping")
    start = time.monotonic()
    out, _ = receiver.communicate(timeout=30)
    assert "recipient engineer listening: yes" in sent.stdout
    assert receiver.returncode == 0 and time.monotonic() - start < MAX_WAKE_LATENCY_S
    assert "message ping" in out and "wake up" in out


def test_next_timeout_exit_code(channel):
    result = _cli("next", "--channel", str(channel), "--me", "engineer", "--timeout", "0.3")
    assert result.returncode == EXIT_NO_MESSAGE and "NO_MESSAGE" in result.stdout


def test_send_reports_deaf_recipient_and_rule_errors(channel, tmp_path):
    sent = _cli("send", "--channel", str(channel), "--me", "scientist", "--kind", "message",
                "--body-file", str(_body(tmp_path, "hello")))
    assert sent.returncode == 0 and "listening: NO" in sent.stdout
    bad = _cli("send", "--channel", str(channel), "--me", "engineer", "--kind", "task",
               "--body-file", str(_body(tmp_path, "x")))
    assert bad.returncode == EXIT_ERROR and "may not post" in bad.stderr


def test_concurrent_writers_keep_ids_consecutive(channel):
    script = ("import sys; from pathlib import Path; from agenthub.core import Hub, PostRequest\n"
              "hub = Hub(Path(sys.argv[1]))\n"
              f"for i in range({SENDS_PER_WRITER}):\n"
              "    hub.send(PostRequest(sys.argv[2], 'message', f'{sys.argv[2]} {i}'))\n")
    writers = [subprocess.Popen([sys.executable, "-c", script, str(channel), name], cwd=REPO)
               for name in ("scientist", "engineer")[:WRITERS]]
    for writer in writers:
        assert writer.wait(timeout=120) == 0
    events = Hub(channel).events()
    assert [e.id for e in events] == list(range(1, WRITERS * SENDS_PER_WRITER + 1))


def test_version_prints_the_package_version():
    """`--version` prints the version of the package, from one place (agenthub.__version__)."""
    from agenthub import __version__

    expected = f"agenthub {__version__}"
    result = _cli("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == expected
