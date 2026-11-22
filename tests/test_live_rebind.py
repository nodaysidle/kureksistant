"""Live voice also starts on a Tailscale IP that appears after the daemon started."""
from types import SimpleNamespace

import pytest

import kurek_daemon as daemon

TS_IP = "100.101.102.103"


class _Ticks:  # stop_event stand-in: wait() returns False n times, then True
    def __init__(self, n):
        self.left = n

    def wait(self, _t):
        self.left -= 1
        return self.left < 0


@pytest.fixture
def tailscale(monkeypatch):
    monkeypatch.setattr(daemon, "require_strong_remote_token_for_bind", lambda: None)
    monkeypatch.setattr(daemon, "detect_tailscale_ipv4", lambda: TS_IP)


def test_rebind_starts_live_voice_once(monkeypatch, tailscale):
    http = []
    monkeypatch.setattr(daemon, "_start_http_server", lambda h, p: http.append((h, p)))
    live = []
    bound = {"127.0.0.1"}
    daemon._tailscale_rebind_loop(18793, bound, _Ticks(3), live.append)
    assert http == [(TS_IP, 18793)]
    assert live == [TS_IP]
    assert bound == {"127.0.0.1", TS_IP}


def test_rebind_retries_a_busy_address_before_live_voice(monkeypatch, tailscale):
    attempts = []

    def start(h, p):
        attempts.append((h, p))
        if len(attempts) == 1:
            raise OSError("busy")

    monkeypatch.setattr(daemon, "_start_http_server", start)
    live = []
    daemon._tailscale_rebind_loop(18793, {"127.0.0.1"}, _Ticks(3), live.append)
    assert len(attempts) == 2
    assert live == [TS_IP]


def test_rebind_without_live_voice_binds_http_only(monkeypatch, tailscale):
    http = []
    monkeypatch.setattr(daemon, "_start_http_server", lambda h, p: http.append((h, p)))
    daemon._tailscale_rebind_loop(18793, {"127.0.0.1"}, _Ticks(3))
    assert http == [(TS_IP, 18793)]


def test_live_starter(monkeypatch):
    assert daemon._live_starter(SimpleNamespace()) is None
    monkeypatch.setenv("KUREK_LIVE_VOICE", "1")
    recorded = []
    monkeypatch.setattr(daemon, "start_live_servers",
                        lambda hosts, auth, tools, run: recorded.append((hosts, auth, tools, run)) or hosts)
    engine = SimpleNamespace(openai_tools=[{"t": 1}], _run_tool=lambda n, a, remote: (n, a, remote))
    assert daemon._live_starter(engine)("100.64.0.9") is True
    [(hosts, auth, tools, run)] = recorded
    assert hosts == ["100.64.0.9"]
    assert auth is daemon._live_authorized
    assert tools is engine.openai_tools
    assert run("web_search", {"q": 1}) == ("web_search", {"q": 1}, True)

    monkeypatch.setattr(daemon, "start_live_servers", lambda hosts, *_a: [])  # the bind failed
    assert daemon._live_starter(engine)("100.64.0.9") is False


def test_an_on_bound_error_does_not_stop_the_loop(monkeypatch, tailscale, capsys):
    http, calls = [], []
    monkeypatch.setattr(daemon, "_start_http_server", lambda h, p: http.append((h, p)))

    def on_bound(ip):
        calls.append(ip)
        raise RuntimeError("websockets broke")

    daemon._tailscale_rebind_loop(18793, {"127.0.0.1"}, _Ticks(3), on_bound)
    assert http == [(TS_IP, 18793)]
    assert calls == [TS_IP] * 3  # the loop kept running and kept retrying live voice
    assert "websockets broke" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["raises", "returns False"])
def test_a_failed_live_start_is_retried_then_never_repeated(monkeypatch, tailscale, failure):
    monkeypatch.setattr(daemon, "_start_http_server", lambda h, p: None)
    calls = []

    def on_bound(ip):
        calls.append(ip)
        if len(calls) == 1:
            if failure == "raises":
                raise OSError("port 8791 busy")
            return False
        return True

    daemon._tailscale_rebind_loop(18793, {"127.0.0.1"}, _Ticks(4), on_bound)
    assert calls == [TS_IP, TS_IP]
