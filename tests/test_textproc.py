import pytest

from localflow import textproc as tp
from localflow.config import DEFAULT_INLINE


def test_corrections_longest_match():
    corr = {"CAPEX": ["cap x", "copics"], "Claude Code": ["cloud code", "clawed code"], "Claude": ["cloud"]}
    assert tp.apply_corrections("our cap x and copics in cloud code via cloud", corr) == \
        "our CAPEX and CAPEX in Claude Code via Claude"
    assert tp.apply_corrections("the cloudy day", corr) == "the cloudy day"


@pytest.mark.parametrize("raw, want", [
    ("Hello comma world period", "Hello, world."),
    ("Hello, comma, world. Period.", "Hello, world."),
    ("Is it done question mark", "Is it done?"),
    ("First line new line second line", "First line\nSecond line"),
    ("End of topic. New paragraph. Next topic", "End of topic.\n\nNext topic"),
    ("He said open quote hi close quote and left", "He said \"hi\" and left"),
    ("The trial period ends soon", "The trial period ends soon"),
    ("I'm done period", "I'm done."),
    ("Go to settings arrow privacy", "Go to settings → privacy"),
])
def test_inline(raw, want):
    assert tp.apply_inline(raw, DEFAULT_INLINE) == want


def test_snippets_roundtrip():
    snips = [{"trigger": "my signature", "text": "Best,\nMomin", "secret": False},
             {"trigger": "my password", "text": "b64:aHVudGVyMg==", "secret": True},
             {"trigger": "today's date", "text": "${day}", "secret": False}]
    text, found = tp.insert_snippet_placeholders("Thanks a lot. My signature.", snips)
    assert text == "Thanks a lot. [[S1]]"
    from localflow.vault import snippet_text
    assert tp.expand_snippets(text, found, snippet_text) == "Thanks a lot. Best,\nMomin"
    text, found = tp.insert_snippet_placeholders("my password", snips)
    assert tp.expand_snippets(text, found, snippet_text) == "hunter2"
    assert tp.expand_snippets(text, found, snippet_text, mask_secrets=True) == "[my password]"


def test_expand_vars():
    out = tp.expand_vars("on ${date} at ${time}: ${clipboard}", clipboard="X")
    assert out.endswith(": X") and "${" not in out


def test_commands():
    cmds = [{"trigger": "press enter", "action": "hotkey", "value": "enter"}]
    assert tp.match_command("Press enter.", cmds)["value"] == "enter"
    assert tp.match_command("please press enter now", cmds) is None


@pytest.mark.parametrize("raw, want", [
    ("Meet at 5. Scratch that. Meet at 6.", "Meet at 6."),
    ("Hi Sam. Meet at 5. Scratch that. Meet at 6.", "Hi Sam. Meet at 6."),
    ("I like it. Actually I hate it, scratch that, I love it.", "I like it. I love it."),
    ("So I think hello, the meeting is at 3 PM scratch that 4 PM.", "So I think hello, 4 PM."),
    ("We should ship the new onboarding flow this week because the old one confuses people "
     "and support tickets are up scratch that next week",
     "We should ship the new onboarding flow this week because the old one confuses people "
     "and support tickets are up next week"),
    ("scratch that", ""),
])
def test_scratch(raw, want):
    assert tp.apply_scratch_that(raw) == want


def test_spoken_lists():
    assert tp.apply_spoken_lists("For the trip I need first my passport, second the charger and third some snacks.") == \
        "For the trip I need:\n1. My passport\n2. The charger\n3. Some snacks"
    assert tp.apply_spoken_lists("Groceries bullet point milk bullet point eggs") == "Groceries:\n- Milk\n- Eggs"
    assert tp.apply_spoken_lists("I came first in the race.") == "I came first in the race."


@pytest.mark.parametrize("raw, want", [
    ("Call me at 3 p.m.", "Call me at 3 PM."),
    ("Meet at 10:30 am tomorrow", "Meet at 10:30 AM tomorrow"),
    ("Email john at example dot com please", "Email john@example.com please"),
    ("Look at google dot com", "Look at google.com"),
    ("It went up 50 percent", "It went up 50%"),
    ("Check www dot github dot com", "Check www.github.com"),
])
def test_smart(raw, want):
    assert tp.smart_format(raw) == want


def test_smart_idempotent():
    s = "Call 3 PM, mail a@b.com, 50%"
    assert tp.smart_format(tp.smart_format(s)) == s


def test_finish():
    assert tp.finish("hello world.", strip_trailing_period=True) == "Hello world"
    assert tp.finish("Hello world", ensure_punctuation=True) == "Hello world."
    assert tp.finish("Run npm install", capitalize_first=False) == "run npm install"
    assert tp.finish("List:\n1. A\n2. B", ensure_punctuation=True) == "List:\n1. A\n2. B"
