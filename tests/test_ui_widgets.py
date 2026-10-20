"""Click the real controls and check they behave (and don't move)."""
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

app = QApplication.instance() or QApplication([])

from localflow.ui.widgets import ChoiceGrid, Select, Sidebar, Stepper, Toggle  # noqa: E402


def pump(ms=400):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture()
def host():
    w = QWidget()
    QVBoxLayout(w)
    w.resize(400, 300)
    w.show()
    yield w
    w.close()


def test_toggle_click_turns_on_and_stays_put(host):
    t = Toggle(False)
    host.layout().addWidget(t)
    pump(100)
    before = t.geometry()
    seen = []
    t.toggled.connect(seen.append)
    QTest.mouseClick(t, Qt.LeftButton, pos=QPoint(10, 10))
    pump(400)
    assert t.isChecked() and seen == [True]
    assert t.geometry() == before              # the old bug slid the whole switch away
    assert t.get_knob() == pytest.approx(1.0)
    QTest.mouseClick(t, Qt.LeftButton, pos=QPoint(30, 10))
    pump(400)
    assert not t.isChecked() and t.geometry() == before and t.get_knob() == pytest.approx(0.0)


def test_animated_property_names_dont_shadow_qt_builtins():
    builtin = {"pos", "size", "geometry", "x", "y", "width", "height", "rect", "font", "enabled", "visible",
               "windowOpacity", "minimumSize", "maximumSize"}
    for cls in (Toggle, Select, Stepper):
        mo = cls.staticMetaObject
        own = {mo.property(i).name() for i in range(mo.propertyOffset(), mo.propertyCount())}
        assert not (own & builtin), f"{cls.__name__} defines {own & builtin}"


def test_choice_grid_and_sidebar(host):
    g = ChoiceGrid({"a": ("A", "first"), "b": ("B", "second")}, "a", 2)
    host.layout().addWidget(g)
    picked = []
    g.picked.connect(picked.append)
    pump(100)
    card_b = g.cards["b"]
    before = card_b.geometry()
    QTest.mouseClick(card_b, Qt.LeftButton, pos=QPoint(20, 20))
    pump(300)
    assert picked == ["b"] and card_b.isChecked() and not g.cards["a"].isChecked()
    assert card_b.geometry() == before

    sb = Sidebar([("X", [("One", "general"), ("Two", "mic")])])
    host.layout().addWidget(sb)
    pump(100)
    got = []
    sb.currentChanged.connect(got.append)
    sb.setCurrent(0, animated=False)
    QTest.mouseClick(sb.items[1], Qt.LeftButton, pos=QPoint(20, 10))
    pump(400)
    assert got[-1] == 1 and sb.current() == 1
    assert sb.indicator.geometry() == sb.items[1].geometry()


def test_stepper(host):
    s = Stepper(0, 10, 2, " px")
    host.layout().addWidget(s)
    committed = []
    s.committed.connect(committed.append)
    s.setValue(4)
    QTest.mouseClick(s.plus, Qt.LeftButton)
    pump(700)
    assert s.value() == 6 and committed == [6] and s.edit.text() == "6 px"


def test_run_bg_always_delivers():
    import gc
    from localflow.ui.widgets import run_bg
    got = []
    for i in range(40):
        run_bg(lambda i=i: (time.sleep(0.002), i)[1], lambda res, err: got.append(res))
    end = time.monotonic() + 5
    while len(got) < 40 and time.monotonic() < end:
        gc.collect()
        app.processEvents()
        time.sleep(0.01)
    assert sorted(got) == list(range(40))
