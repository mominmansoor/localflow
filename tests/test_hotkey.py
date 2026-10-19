from localflow.hotkey import ARMED, CANCEL, FIRE, LOCK, START, STOP, HotkeyMachine, parse_hotkey

B = {"dictate": "ctrl+win", "rephrase": "ctrl+shift+win", "paste_last": "alt+shift+z", "pause": "ctrl+alt+win"}


def m(lock_mode="double_tap", **kw):
    return HotkeyMachine(B, lock_mode=lock_mode, tap_ms=300, double_tap_ms=400, chord_guard_ms=400, **kw)


def test_parse():
    assert parse_hotkey("Ctrl + Cmd") == {"ctrl", "win"}


def test_hold_to_talk():
    h = m()
    assert h.press("ctrl", 0.0) == []
    assert h.press("win", 0.05) == [(START, "dictate")]
    assert h.press("win", 0.5) == []              # auto-repeat
    assert h.release("win", 2.0) == [(STOP, "dictate")]
    assert h.release("ctrl", 2.01) == []


def test_double_tap_locks_and_tap_stops():
    h = m()
    h.press("ctrl", 0); assert h.press("win", 0.01) == [(START, "dictate")]
    assert h.release("win", 0.1) == [(ARMED, "dictate")]
    h.release("ctrl", 0.11)
    h.press("ctrl", 0.25)
    assert h.press("win", 0.26) == [(LOCK, "dictate")]
    assert h.release("win", 0.3) == [] and h.release("ctrl", 0.3) == []
    assert h.poll(5) == []                        # locked: poll does nothing
    h.press("ctrl", 0.5); h.release("ctrl", 0.6)  # Ctrl alone while locked: ignored
    h.press("ctrl", 6.0)
    assert h.press("win", 6.01) == [(STOP, "dictate")]
    assert h.state == h.IDLE


def test_single_tap_alone_is_discarded():
    h = m()
    h.press("ctrl", 0); h.press("win", 0.01)
    assert h.release("win", 0.1) == [(ARMED, "dictate")]
    assert h.poll(0.3) == []                      # still inside window
    assert h.poll(0.6) == [(CANCEL, "dictate")]


def test_single_tap_mode_and_off_mode():
    h = m("single_tap")
    h.press("ctrl", 0); h.press("win", 0.01)
    assert h.release("win", 0.1) == [(LOCK, "dictate")]
    h2 = m("off")
    h2.press("ctrl", 0); h2.press("win", 0.01)
    assert h2.release("win", 0.1) == [(STOP, "dictate")]


def test_esc_cancels_everywhere():
    h = m()
    h.press("ctrl", 0); h.press("win", 0.01)
    assert h.press("esc", 1.0) == [(CANCEL, "dictate")]
    assert h.release("win", 1.2) == []


def test_system_shortcut_cancels():
    h = m()
    h.press("ctrl", 0); h.press("win", 0.01)
    assert h.press("d", 0.1) == [(CANCEL, "dictate")]


def test_rephrase_beats_dictate():
    h = m()
    h.press("ctrl", 0); h.press("shift", 0.01)
    assert h.press("win", 0.02) == [(START, "rephrase")]
    assert h.release("win", 2) == [(STOP, "rephrase")]


def test_upgrade_dictate_to_rephrase():
    h = m()
    h.press("ctrl", 0); h.press("win", 0.01)
    assert h.press("shift", 0.1) == [(CANCEL, "dictate"), (START, "rephrase")]


def test_rephrase_quick_tap_just_stops():
    h = m()
    h.press("ctrl", 0); h.press("shift", 0); h.press("win", 0.01)
    assert h.release("win", 0.1) == [(STOP, "rephrase")]


def test_tap_bindings_and_pause():
    h = m()
    h.press("alt", 0); h.press("shift", 0)
    assert h.press("z", 0.1) == [(FIRE, "paste_last")]
    for k in ("z", "shift", "alt"):
        h.release(k, 0.2)
    h.press("ctrl", 1); h.press("alt", 1)
    assert h.press("win", 1.01) == [(FIRE, "pause")] and h.paused
    for k in ("win", "alt", "ctrl"):
        h.release(k, 1.1)
    h.press("ctrl", 2)
    assert h.press("win", 2.01) == []             # dictate disabled while paused
    h.release("win", 2.1); h.release("ctrl", 2.1)
    h.press("ctrl", 3); h.press("alt", 3)
    assert h.press("win", 3.01) == [(FIRE, "pause")] and not h.paused


def test_resync_drops_stale_keys():
    os_down = {"ctrl"}
    h = m(is_down=lambda k: k in os_down)
    h.pressed.add("win")
    assert h.press("ctrl", 0) == []
    os_down.add("win")
    assert h.press("win", 0.1) == [(START, "dictate")]


def test_force_stop():
    h = m()
    h.press("ctrl", 0); h.press("win", 0.01)
    assert h.force_stop() == [(STOP, "dictate")]
    assert h.force_stop() == []
