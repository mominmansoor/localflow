"""Dictation history in SQLite: search, retention, corrections mining."""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS dictations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  app TEXT, profile TEXT, kind TEXT,
  raw TEXT, final TEXT,
  audio_s REAL, stt_ms INTEGER, llm_ms INTEGER, llm TEXT
);
CREATE INDEX IF NOT EXISTS idx_ts ON dictations(ts);
"""

_COMMON = set("""a about after again all also am an and any are as at be because been before being but by can
could did do does doing don't down during each few for from further get got had has have having he her here hers
him his how i i'm if in into is it it's its just know let like make me more most my no nor not now of off on once
only or other our out over own please really right same see she should so some such than thank thanks that the
their them then there these they this those through to too under until up very want was we well were what when
where which while who whom why will with would yeah yes you your okay ok hey hi hello monday tuesday wednesday
thursday friday saturday sunday january february march april may june july august september october november
december english first second third one two three""".split())


class History:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.executescript(SCHEMA)

    def add(self, *, raw, final, app=None, profile=None, kind="dictation", audio_s=0.0, stt_ms=0, llm_ms=0, llm="") -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO dictations (ts, app, profile, kind, raw, final, audio_s, stt_ms, llm_ms, llm) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (time.time(), app, profile, kind, raw, final, audio_s, stt_ms, llm_ms, llm))
            self._db.commit()

    def search(self, query: str = "", limit: int = 500) -> list[dict]:
        with self._lock:
            cur = self._db.execute(
                "SELECT id, ts, app, profile, kind, raw, final, audio_s, stt_ms, llm_ms, llm FROM dictations "
                "WHERE (? = '' OR final LIKE ? OR raw LIKE ?) ORDER BY ts DESC LIMIT ?",
                (query, f"%{query}%", f"%{query}%", limit))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def delete(self, row_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM dictations WHERE id = ?", (row_id,))
            self._db.commit()

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM dictations")
            self._db.commit()

    def prune(self, retention_days: int) -> int:
        """retention_days <= 0 keeps nothing."""
        cutoff = time.time() - max(0, retention_days) * 86400 if retention_days > 0 else float("inf")
        with self._lock:
            n = self._db.execute("DELETE FROM dictations WHERE ts < ?", (cutoff,)).rowcount
            self._db.commit()
        return n

    def stats(self) -> dict:
        with self._lock:
            n, words, secs = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(final) - LENGTH(REPLACE(final, ' ', '')) + 1), 0), "
                "COALESCE(SUM(audio_s), 0) FROM dictations").fetchone()
        return {"count": n, "words": words, "audio_minutes": round(secs / 60, 1)}

    def suggest_hotwords(self, existing: list[str] | None = None, min_count: int = 2, limit: int = 30) -> list[tuple[str, int]]:
        """Capitalised words / CamelCase / acronyms you say often that aren't common English."""
        have = {w.lower() for w in existing or []}
        counts: Counter = Counter()
        for (text,) in self._db.execute("SELECT final FROM dictations"):
            for sent in re.split(r"(?<=[.!?\n])\s+", text or ""):
                words = re.findall(r"[A-Za-z][\w.+#-]*[\w+#]|[A-Za-z]", sent)
                for i, w in enumerate(words):
                    if i == 0 and not (w.isupper() and len(w) > 1) and not re.search(r"[a-z][A-Z]", w):
                        continue  # sentence-initial capital means nothing
                    interesting = (w[0].isupper() or re.search(r"[a-z][A-Z]", w)) and len(w) > 1
                    if interesting and w.lower() not in _COMMON and w.lower() not in have:
                        counts[w] += 1
        return [(w, c) for w, c in counts.most_common(limit) if c >= min_count]

    def close(self) -> None:
        self._db.close()
