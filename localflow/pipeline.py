"""Turn a raw Whisper transcript into the text that gets delivered."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import cleanup, textproc
from .vault import snippet_text


@dataclass
class Result:
    text: str = ""                 # what gets typed (snippets expanded)
    display: str = ""              # same, but secret snippets masked (history / tray)
    command: dict | None = None    # voice command to run instead of typing
    info: dict = field(default_factory=dict)


def process(raw: str, eff, clipboard: str = "", use_llm: bool = True) -> Result:
    """`eff` is an Effective settings view (anything with .get(dotted_key))."""
    g = eff.get
    info = {"llm": "skipped", "llm_ms": 0}
    text = raw.strip()
    if not text:
        return Result(info=info)

    # 1. voice commands: whole utterance == trigger
    if g("commands.enabled"):
        cmd = textproc.match_command(text, g("commands.items"))
        if cmd:
            return Result(command=cmd, display=f"[command: {cmd['trigger']}]", info=info)

    level, style = g("cleanup.level"), g("cleanup.style")

    # 2. deterministic pre-processing
    text = textproc.apply_corrections(text, g("vocabulary.corrections"))
    text, found = textproc.insert_snippet_placeholders(text, g("snippets"))
    if g("formatting.inline_commands"):
        text = textproc.apply_inline(text, g("formatting.inline_replacements"))
    if level != "none":
        text = cleanup.rule_clean(text)
    smart = dict(times=g("formatting.smart_times"), emails=g("formatting.smart_emails"),
                 urls=g("formatting.smart_urls"), percent=g("formatting.smart_percent"))
    text = textproc.smart_format(text, **smart)      # before scratch-that: "3 p.m." isn't a sentence end
    if level in ("medium", "high"):
        text = textproc.apply_scratch_that(text)
    if g("formatting.spoken_lists"):
        text = textproc.apply_spoken_lists(text)

    # 3. LLM polish
    if (use_llm and level in ("medium", "high") and style != "verbatim"
            and cleanup.word_count(text) >= g("cleanup.llm_min_words")):
        out, ms, status = cleanup.llm_clean(text, g, level, style)
        info.update(llm=status, llm_ms=round(ms))
        if out:
            text = textproc.smart_format(out, **smart)

    # 4. finishing + snippet expansion
    text = textproc.finish(text, g("formatting.capitalize_first"), g("formatting.ensure_punctuation"),
                           g("formatting.strip_trailing_period"))
    final = textproc.expand_snippets(text, found, snippet_text, clipboard)
    display = textproc.expand_snippets(text, found, snippet_text, clipboard, mask_secrets=True)
    return Result(text=final, display=display, info=info)
