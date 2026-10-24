"""Headset disappears / comes back, with a fake sounddevice and a fake Windows device list."""
import sys
import types

import numpy as np
import pytest

from localflow import audio

HEADSET_MME = "Microphone (HyperX Cloud Stinge"          # what Windows' old API shows (31 chars)
HEADSET_FULL = "Microphone (HyperX Cloud Stinger Core Wireless)"
WEBCAM = "Microphone (C920 HD Webcam)"


class FakeStream:
    def __init__(self, sd, device, callback, **kw):
        self.sd, self.device, self.callback, self.active = sd, device, callback, False
        sd.opened.append(device)

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        pass


class FakeSD(types.ModuleType):
    """Like PortAudio: the device list is a snapshot taken at _initialize()."""

    def __init__(self):
        super().__init__("sounddevice")
        self.present = [WEBCAM]          # what's physically plugged in
        self.snapshot = list(self.present)
        self.opened, self.inits = [], 0
        self.default = None              # Windows' default input (None = first device)

    def _initialize(self):
        self.inits += 1
        self.snapshot = list(self.present)

    def _terminate(self):
        pass

    def query_hostapis(self):
        return [{"name": "MME"}, {"name": "Windows WASAPI"}]

    def query_devices(self, device=None, kind=None):
        devs = []
        for n in self.snapshot:
            devs.append({"name": n[:31], "max_input_channels": 1, "hostapi": 0, "default_samplerate": 16000})
            devs.append({"name": n, "max_input_channels": 1, "hostapi": 1, "default_samplerate": 48000})
        if device is None and kind == "input":
            name = self.default or (self.snapshot[0] if self.snapshot else None)
            return next(d for d in devs if d["name"] == name)
        return devs[device] if device is not None else devs

    def InputStream(self, device=None, callback=None, **kw):
        return FakeStream(self, device, callback, **kw)


@pytest.fixture()
def env(monkeypatch):
    sd = FakeSD()
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    calls = {"live": 0}

    def live():
        calls["live"] += 1
        return [n[:31] for n in sd.present]
    monkeypatch.setattr(audio, "live_input_names", live)
    monkeypatch.setattr(audio.Recorder, "_start_watchdog", lambda self: None)  # tests drive watch_once
    return sd, calls


def feed(rec, value, seconds):
    for _ in range(int(seconds * audio.RATE / audio.BLOCK) + 1):
        rec._cb(np.full((audio.BLOCK, 1), value, np.float32), audio.BLOCK, None, None)


def test_names_match_handles_truncation():
    assert audio.names_match(HEADSET_MME, HEADSET_FULL)
    assert audio.names_match(HEADSET_FULL, HEADSET_MME)
    assert not audio.names_match(HEADSET_FULL, WEBCAM)
    assert not audio.names_match(None, WEBCAM)


def test_list_uses_full_names(env):
    sd, _ = env
    sd.present = sd.snapshot = [HEADSET_FULL, WEBCAM]
    assert [n for _, n in audio.list_input_devices()] == [HEADSET_FULL, WEBCAM]
    assert audio.full_name(HEADSET_MME) == HEADSET_FULL


def test_headset_missing_then_returns_switches_back(env):
    sd, calls = env
    msgs = []
    rec = audio.Recorder(HEADSET_FULL, keep_open=True, on_status=lambda t, e: msgs.append(t))
    assert rec.fallback and rec.device is None and "isn't connected" in msgs[0]
    rec.open()
    assert sd.opened[-1] is None                       # recording from the default for now

    rec.watch_once(now=100.0)                          # headset still asleep: one cheap check, no rescan
    assert calls["live"] == 1 and sd.inits == 0 and rec.fallback

    rec.watch_once(now=101.5)                          # within 5 s: doesn't even check
    assert calls["live"] == 1

    sd.present = [HEADSET_FULL, WEBCAM]                # headset wakes up
    rec._last_cb = 106.0
    rec.watch_once(now=106.0)
    assert not rec.fallback and sd.inits == 1
    assert sd.query_devices()[rec.device]["name"].startswith("Microphone (HyperX")
    assert sd.opened[-1] == rec.device and msgs[-1] == "Mic reconnected"


def test_no_checks_at_all_when_mic_is_fine(env):
    sd, calls = env
    sd.present = sd.snapshot = [HEADSET_FULL]
    rec = audio.Recorder(HEADSET_FULL)
    rec.open()
    for t in range(0, 600, 2):                         # ten minutes of watchdog ticks
        rec._last_cb = float(t)
        rec.watch_once(now=float(t))
    assert calls["live"] == 0 and sd.inits == 0 and not rec.fallback


def test_hotkey_press_picks_up_returned_headset(env):
    sd, _ = env
    rec = audio.Recorder(HEADSET_FULL)
    rec.open()
    assert rec.fallback
    sd.present = [HEADSET_FULL]                        # came back between watchdog checks
    rec.start()
    assert not rec.fallback and sd.opened[-1] == rec.device and rec.recording


def test_dead_stream_reopens_once_then_says_muted(env):
    sd, _ = env
    sd.present = sd.snapshot = [HEADSET_FULL]
    msgs = []
    rec = audio.Recorder(HEADSET_FULL, on_status=lambda t, e: msgs.append(t))
    rec.start()
    opened = len(sd.opened)
    feed(rec, 0.0, 1.2)                                # exact digital silence
    rec._last_cb = 50.0
    rec.watch_once(now=50.0)
    assert len(sd.opened) == opened + 1                # reopened once
    feed(rec, 0.0, 1.2)
    rec._last_cb = 52.0
    rec.watch_once(now=52.0)
    assert len(sd.opened) == opened + 1                # ...not again
    rec.stop()
    assert "muted" in msgs[-1]


def test_quiet_room_is_not_mistaken_for_dead_mic(env):
    sd, _ = env
    sd.present = sd.snapshot = [HEADSET_FULL]
    rec = audio.Recorder(HEADSET_FULL)
    rec.start()
    opened = len(sd.opened)
    feed(rec, 1e-5, 3.0)                               # very quiet, but a real signal
    rec._last_cb = 9.0
    rec.watch_once(now=9.0)
    assert len(sd.opened) == opened


def test_system_default_never_polls(env):
    sd, calls = env
    rec = audio.Recorder(None)
    rec.open()
    rec._last_cb = 30.0
    rec.watch_once(now=30.0)
    assert not rec.fallback and calls["live"] == 0


CABLE = "CABLE Output (VB-Audio Virtual Cable)"


def test_is_virtual():
    assert audio.is_virtual(CABLE) and audio.is_virtual("Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)")
    assert audio.is_virtual("Stereo Mix (Realtek(R) Audio)")
    assert not audio.is_virtual(HEADSET_FULL) and not audio.is_virtual(WEBCAM)


def test_fallback_skips_vb_cable_when_windows_makes_it_default(env):
    sd, _ = env
    sd.present = sd.snapshot = [CABLE, WEBCAM]
    sd.default = CABLE                                  # headset asleep, Windows promoted the cable
    rec = audio.Recorder(HEADSET_FULL)
    rec.open()
    assert rec.fallback and sd.query_devices()[sd.opened[-1]]["name"] == WEBCAM


def test_fallback_keeps_real_default(env):
    sd, _ = env
    sd.present = sd.snapshot = [CABLE, WEBCAM]
    sd.default = WEBCAM
    rec = audio.Recorder(HEADSET_FULL)
    rec.open()
    assert sd.opened[-1] is None                        # plain system default


def test_only_a_cable_left_still_records(env):
    sd, _ = env
    sd.present = sd.snapshot = [CABLE]
    sd.default = CABLE
    rec = audio.Recorder(HEADSET_FULL)
    rec.open()
    assert sd.opened[-1] is None


def test_cable_can_still_be_chosen_on_purpose(env):
    sd, _ = env
    sd.present = sd.snapshot = [CABLE, WEBCAM]
    sd.default = WEBCAM
    rec = audio.Recorder(CABLE)
    rec.open()
    assert not rec.fallback and sd.query_devices()[sd.opened[-1]]["name"].startswith("CABLE Output")


def test_cable_returning_headset_switches_back(env):
    sd, _ = env
    sd.present = sd.snapshot = [CABLE]
    sd.default = CABLE
    rec = audio.Recorder(HEADSET_FULL)
    rec.open()
    sd.present = [CABLE, HEADSET_FULL]
    rec._last_cb = 10.0
    rec.watch_once(now=10.0)
    assert not rec.fallback and sd.query_devices()[sd.opened[-1]]["name"].startswith("Microphone (HyperX")
