"""Deterministic text stages (fast, offline, no LLM).

Order used by the pipeline:
  vocabulary corrections -> snippet placeholders -> inline voice formatting ->
  filler removal -> "scratch that" -> spoken lists -> smart formatting ->
  [LLM] -> finishing touches -> snippet expansion
"""
from __future__ import annotations

import re
from datetime import datetime

# ---------------------------------------------------------------------------
# Vocabulary corrections: {"CAPEX": ["cap x", "copics"]}, longest match wins
# ---------------------------------------------------------------------------

def _phrase_re(phrase: str) -> str:
    words = [re.escape(w) for w in phrase.split()]
    return r"[\s-]+".join(words)


def apply_corrections(text: str, corrections: dict[str, list[str]]) -> str:
    pairs = [(wrong, right) for right, wrongs in (corrections or {}).items() for wrong in wrongs if wrong.strip()]
    if not pairs or not text:
        return text
    pairs.sort(key=lambda p: len(p[0]), reverse=True)

    def norm(s: str) -> str:
        return re.sub(r"[\s-]+", " ", s.lower().strip())

    lookup = {norm(w): r for w, r in pairs}
    pattern = re.compile(r"(?i)(?<![\w])(" + "|".join(_phrase_re(w) for w, _ in pairs) + r")(?![\w])")
    return pattern.sub(lambda m: lookup.get(norm(m.group(1)), m.group(1)), text)


# ---------------------------------------------------------------------------
# Snippets: trigger phrase -> placeholder [[S1]] -> expansion at the very end
# ---------------------------------------------------------------------------

def insert_snippet_placeholders(text: str, snippets: list[dict]) -> tuple[str, dict[str, dict]]:
    found: dict[str, dict] = {}
    items = sorted([s for s in snippets or [] if s.get("trigger", "").strip()],
                   key=lambda s: len(s["trigger"]), reverse=True)
    for s in items:
        pat = re.compile(r"(?i)(?<![\w])" + _phrase_re(s["trigger"].strip()) + r"(?![\w])[.!?,]?")

        def repl(_m, s=s):
            token = f"[[S{len(found) + 1}]]"
            found[token] = s
            return token

        text = pat.sub(repl, text)
    return text, found


def expand_vars(text: str, clipboard: str = "") -> str:
    now = datetime.now()
    return (text.replace("${date}", now.strftime("%d %B %Y"))
                .replace("${time}", now.strftime("%H:%M"))
                .replace("${day}", now.strftime("%A"))
                .replace("${clipboard}", clipboard or ""))


def expand_snippets(text: str, found: dict[str, dict], resolve, clipboard: str = "", mask_secrets=False) -> str:
    for token, snip in found.items():
        if mask_secrets and snip.get("secret"):
            value = f"[{snip['trigger']}]"
        else:
            value = expand_vars(resolve(snip), clipboard)
        text = text.replace(token, value)
    return text


# ---------------------------------------------------------------------------
# Voice commands: the whole utterance must equal the trigger
# ---------------------------------------------------------------------------

def norm_phrase(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def match_command(text: str, commands: list[dict]) -> dict | None:
    n = norm_phrase(text)
    if not n:
        return None
    for c in commands or []:
        if c.get("trigger") and norm_phrase(c["trigger"]) == n:
            return c
    return None


# ---------------------------------------------------------------------------
# Inline voice formatting: "comma", "new paragraph", "open quote" ...
# ---------------------------------------------------------------------------

_CLOSE = set(",.;:?!…)]}")
_OPEN = set("([{")
_SENT_END = {".", "?", "!"}


def apply_inline(text: str, replacements: dict[str, str]) -> str:
    if not text or not replacements:
        return text
    items = sorted(replacements.items(), key=lambda kv: len(kv[0]), reverse=True)
    outs = []

    def make_repl(out):
        def repl(m):
            outs.append(out)
            return f" \x01{len(outs) - 1}\x01 "
        return repl

    for phrase, out in items:
        if not phrase.strip():
            continue
        body = _phrase_re(phrase.strip())
        tail = r"(?![\w])"
        if " " not in phrase.strip() and out in _SENT_END:
            # single-word sentence enders ("period") only count at a sentence boundary,
            # so "the trial period ends" is left alone
            tail = r"(?![\w])(?=[.,!?]?\s*(?:$|\n|(?-i:[A-Z])))"
        # Whisper often punctuates around the spoken command ("Hello, comma, world").
        # Swallow that punctuation only when the command itself is punctuation.
        lead = r"[,.]?[ \t]*" if out and all(c in _CLOSE for c in out) else r"[ \t]*"
        pat = re.compile(r"(?i)" + lead + r"(?<![\w])" + body + tail + r"[,.!?]?")
        text = pat.sub(make_repl(out), text)

    if not outs:
        return text
    parts = re.split(r"\x01(\d+)\x01", text)
    res = parts[0]
    quote_open = False
    for i in range(1, len(parts), 2):
        out, after = outs[int(parts[i])], parts[i + 1]
        if out == "\"":
            out_kind = "close" if quote_open else "open"
            quote_open = not quote_open
        elif out and all(c in _CLOSE for c in out):
            out_kind = "close"
        elif out in _OPEN:
            out_kind = "open"
        elif out.strip() == "":
            out_kind = "newline"
        else:
            out_kind = "word"
        if out_kind == "close":
            res = res.rstrip() + out
            after = " " + after.lstrip() if after.strip() else ""
        elif out_kind == "open":
            res = res.rstrip() + (" " if res.strip() else "") + out
            after = after.lstrip()
        elif out_kind == "newline":
            res = res.rstrip(" \t") + out
            after = after.lstrip(" \t")
        else:
            res = res.rstrip() + " " + out + " "
            after = after.lstrip()
        res += after
    res = re.sub(r"[ \t]{2,}", " ", res).strip(" ")
    return capitalize_sentences(res)


def capitalize_sentences(t: str) -> str:
    t = re.sub(r"([.!?]\s+|\n\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
    return t[:1].upper() + t[1:] if t[:1].islower() else t


# ---------------------------------------------------------------------------
# "scratch that" -> delete the previous sentence/clause
# ---------------------------------------------------------------------------

_SCRATCH = re.compile(r"(?i)\b(?:scratch that|delete that|strike that)\b[,.!]?\s*")


def _last_boundary(s: str, chars: str) -> int:
    """Index just after the last boundary char (followed by whitespace) in s, or -1."""
    best = -1
    for m in re.finditer(rf"[{re.escape(chars)}](?=\s)", s):
        best = m.end()
    return best


def apply_scratch_that(text: str) -> str:
    """'Meet at 5. Scratch that. Meet at 6.' -> 'Meet at 6.'

    Deletes only the sentence (or clause) right before the phrase. If there's no
    clear boundary in a long passage, only the phrase itself is removed - never
    a whole dictation.
    """
    while True:
        m = _SCRATCH.search(text)
        if not m:
            return text
        before, after = text[: m.start()].rstrip(), text[m.end():]
        if before[-1:] in (".", "!", "?"):
            body = before[:-1]                         # previous complete sentence
            cut = _last_boundary(body, ".!?\n")
        else:
            body = before.rstrip(",;")                 # mid-sentence: previous clause
            cut = _last_boundary(body, ".!?\n,;")
        if cut >= 0:
            kept = body[:cut].rstrip() + " "
        elif len(body.split()) <= 15:
            kept = ""
        else:
            kept = before + " "                       # unclear: just drop the phrase
        text = (kept + after.lstrip()).strip()
        if text and text[0].islower() and (not kept or kept.rstrip()[-1:] in ".!?"):
            text = text[0].upper() + text[1:]


# ---------------------------------------------------------------------------
# Spoken lists: "first, milk. second, eggs" -> numbered list
# ---------------------------------------------------------------------------

_ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
_ORD_RE = re.compile(r"(?i)(?<![\w])(" + "|".join(_ORDINALS) + r")(?:ly)?(?![\w])[,:]?\s*")
_BULLET_RE = re.compile(r"(?i)(?<![\w])(?:bullet point|bullet|next point)(?![\w])[,:]?\s*")


def _clean_item(s: str) -> str:
    s = re.sub(r"(?i)[,;.\s]+(?:and|then)?[,;.\s]*$", "", s.strip())
    s = s.strip(" ,;.")
    return s[:1].upper() + s[1:]


def apply_spoken_lists(text: str) -> str:
    matches = list(_ORD_RE.finditer(text))
    seq = []
    for m in matches:
        if _ORDINALS.index(m.group(1).lower()) == len(seq):
            seq.append(m)
    if len(seq) >= 2:
        intro = text[: seq[0].start()].rstrip(" ,")
        items = [_clean_item(text[m.end(): (seq[i + 1].start() if i + 1 < len(seq) else len(text))])
                 for i, m in enumerate(seq)]
        if all(items):
            if intro:
                intro = intro.rstrip(".") + (":" if not intro.endswith(":") else "") + "\n"
            return intro + "\n".join(f"{i + 1}. {it}" for i, it in enumerate(items))
    bullets = list(_BULLET_RE.finditer(text))
    if len(bullets) >= 2:
        intro = text[: bullets[0].start()].rstrip(" ,")
        items = [_clean_item(text[m.end(): (bullets[i + 1].start() if i + 1 < len(bullets) else len(text))])
                 for i, m in enumerate(bullets)]
        if all(items):
            if intro:
                intro = intro.rstrip(".") + (":" if not intro.endswith(":") else "") + "\n"
            return intro + "\n".join(f"- {it}" for it in items)
    return text


# ---------------------------------------------------------------------------
# Smart formatting (each toggle independent, all idempotent)
# ---------------------------------------------------------------------------

_TIME = re.compile(r"(?i)\b(\d{1,2})(?:[:.](\d{2}))?\s*([ap])\.?\s?m\b\.?")
_TLDS = "com|org|net|io|dev|ai|co|edu|gov|app|me|sa|pk|uk|us|info|xyz|tech"
_EMAIL = re.compile(r"(?i)\b([a-z0-9][\w.+-]*)\s+at\s+([a-z0-9-]+(?:\s+dot\s+[a-z0-9-]+)+)\b")
_URL = re.compile(rf"(?i)\b((?:[a-z0-9-]+\s+dot\s+)+)({_TLDS})\b")
_DOT = re.compile(r"(?i)\s+dot\s+")
_PCT = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*(?:percent|per cent)\b")
_NOT_EMAIL_LOCAL = {"look", "me", "us", "it", "them", "him", "her", "that", "this", "here", "there", "home",
                    "work", "least", "all", "once", "first", "last", "him", "you", "one", "arrive", "be", "is",
                    "are", "was", "were", "meet", "stay", "stayed", "staying", "go", "went", "check", "find"}


def smart_format(text: str, times=True, emails=True, urls=True, percent=True) -> str:
    if times:
        def tfix(m):
            mm = f":{m.group(2)}" if m.group(2) and m.group(2) != "00" else ""
            end = "." if m.group(0).endswith(".") and m.end() == len(text) else ""
            return f"{m.group(1)}{mm} {m.group(3).upper()}M{end}"
        text = _TIME.sub(tfix, text)
    if emails:
        def efix(m):
            if m.group(1).lower() in _NOT_EMAIL_LOCAL:
                return m.group(0)
            domain = _DOT.sub(".", m.group(2))
            return f"{m.group(1)}@{domain}".lower()
        text = _EMAIL.sub(efix, text)
    if urls:
        text = _URL.sub(lambda m: _DOT.sub(".", m.group(1)).lower() + m.group(2).lower(), text)
    if percent:
        text = _PCT.sub(r"\1%", text)
    return text


# ---------------------------------------------------------------------------
# Finishing touches
# ---------------------------------------------------------------------------

def finish(text: str, capitalize_first=True, ensure_punctuation=False, strip_trailing_period=False) -> str:
    t = text.strip()
    if not t:
        return t
    if capitalize_first and t[0].islower():
        t = t[0].upper() + t[1:]
    if not capitalize_first and t[0].isupper() and (len(t) == 1 or not t[1].isupper()) and not t.startswith("I "):
        t = t[0].lower() + t[1:]
    last_line = t.splitlines()[-1]
    is_list = bool(re.match(r"^\s*(\d+\.|-)\s", last_line))
    if ensure_punctuation and not is_list and t[-1].isalnum():
        t += "."
    if strip_trailing_period and t.endswith(".") and not t.endswith("..") and not t.endswith("]]."):
        t = t[:-1]
    return t
