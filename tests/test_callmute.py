"""Mute other apps' mic sessions while dictating - with a fake Windows audio stack."""
import json
import os
import time

import pytest

from localflow import callmute


class FakeSessions:
    def __init__(self):
        self.muted = {}              # vol id -> bool
        self.pids = {}               # vol id -> pid
        self.released = []

    def add(self, vol, pid, muted=False):
        self.pids[vol], self.muted[vol] = pid, muted

    def sessions(self):
        for vol, pid in list(self.pids.items()):
            yield pid, vol


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    f = FakeSessions()
    monkeypatch.setattr(callmute, "WIN", True)
    monkeypatch.setattr(callmute, "_co_init", lambda: None)
    monkeypatch.setattr(callmute, "_capture_sessions", f.sessions)
    monkeypatch.setattr(callmute, "_get_mute", lambda v: f.muted[v])
    monkeypatch.setattr(callmute, "_set_mute", lambda v, on: f.muted.__setitem__(v, on) or True)
    monkeypatch.setattr(callmute, "_release", f.released.append)
    monkeypatch.setattr(callmute, "_exe", str)
    return f, tmp_path / "muted_apps.json"


def settle(m):
    end = time.monotonic() + 2
    while not m._q.empty() and time.monotonic() < end:
        time.sleep(0.01)
    time.sleep(0.05)


def test_mutes_calls_and_restores_exactly_them(fake):
    f, state = fake
    f.add(101, 4242)                      # Discord, in a call
    f.add(102, os.getpid())               # LocalFlow itself: never muted
    f.add(103, 5555, muted=True)          # you had this one muted already
    m = callmute.CallMuter(state)
    m.mute()
    settle(m)
    assert f.muted == {101: True, 102: False, 103: True}
    assert json.loads(state.read_text()) == [4242]       # remembered in case of a crash
    m.unmute()
    settle(m)
    assert f.muted == {101: False, 102: False, 103: True}   # yours stays muted
    assert not state.exists()
    m.shutdown()


def test_double_mute_is_harmless(fake):
    f, state = fake
    f.add(101, 4242)
    m = callmute.CallMuter(state)
    m.mute(); m.mute()
    settle(m)
    m.unmute()
    settle(m)
    assert f.muted[101] is False
    m.shutdown()


def test_crash_mid_dictation_is_undone_on_next_start(fake):
    f, state = fake
    f.add(101, 4242, muted=True)          # left muted by a run that died
    state.write_text(json.dumps([4242]))
    m = callmute.CallMuter(state)
    settle(m)
    assert f.muted[101] is False and not state.exists()
    m.shutdown()


def test_shutdown_unmutes(fake):
    f, state = fake
    f.add(101, 4242)
    m = callmute.CallMuter(state)
    m.mute()
    settle(m)
    m.shutdown()
    assert f.muted[101] is False
