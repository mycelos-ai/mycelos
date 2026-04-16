"""Deterministic DE+EN note parser.

Runs both in the `POST /api/knowledge/notes` handler and (as a sibling
JS port) in the browser Quick-Capture modal. No LLM, no I/O. Given the
same input and reference time, it must return the same result in both
languages and in both runtimes. Shared test vectors in
``tests/fixtures/parse-note-vectors.json`` enforce that.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TypedDict


class ParsedNote(TypedDict):
    type: str          # "note" | "task"
    due: str | None    # ISO8601 UTC
    tags: list[str]
    wikilinks: list[str]
    reminder: bool


_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_TAG_RE = re.compile(r"(?:^|\s)#([A-Za-zÄÖÜäöüß0-9_-]+)")
_TODO_RE = re.compile(r"^\s*(TODO|FIXME|AUFGABE)\s*[:\s]", re.IGNORECASE)

_REMIND_RE = re.compile(
    r"\b(remind\s+me|erinnere\s+mich|erinner\s+mich)\b",
    re.IGNORECASE,
)
_IN_DURATION_RE = re.compile(
    r"\bin\s+(\d+)\s+(minute|minuten|minutes|min|stunde|stunden|hour|hours|std|h)\b",
    re.IGNORECASE,
)

# "tomorrow 2pm" / "morgen 14 uhr" / "morgen 14:00"
_TOMORROW_RE = re.compile(
    r"\b(tomorrow|morgen)\b(?:\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|uhr)?)?",
    re.IGNORECASE,
)

# "14.04. 15:00" / "14.04.2026 15:00"
_GERMAN_DATE_RE = re.compile(
    r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})?\s*(\d{1,2})(?::(\d{2}))?",
)


def _to_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _duration_seconds(amount: int, unit: str) -> int:
    u = unit.lower()
    if u.startswith("min"):
        return amount * 60
    if u.startswith(("h", "std", "stunde", "hour")):
        return amount * 3600
    return amount * 60


def _parse_12h_to_24h(hour: int, ampm: str | None) -> int:
    if not ampm:
        return hour
    ampm = ampm.lower()
    if ampm == "pm" and hour < 12:
        return hour + 12
    if ampm == "am" and hour == 12:
        return 0
    return hour


def parse_note_text(text: str, *, now: datetime | None = None) -> ParsedNote:
    """Parse free-form note text into structured fields. Pure function."""
    if now is None:
        now = datetime.now(tz=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    wikilinks = _WIKILINK_RE.findall(text)
    tags = _TAG_RE.findall(text)

    reminder = bool(_REMIND_RE.search(text))
    type_: str = "note"
    due: str | None = None

    if _TODO_RE.match(text):
        type_ = "task"

    in_dur = _IN_DURATION_RE.search(text)
    if in_dur:
        amount = int(in_dur.group(1))
        seconds = _duration_seconds(amount, in_dur.group(2))
        due = _to_iso(now + timedelta(seconds=seconds))
        type_ = "task"

    if due is None:
        tomorrow = _TOMORROW_RE.search(text)
        if tomorrow:
            hour = int(tomorrow.group(2)) if tomorrow.group(2) else 9
            minute = int(tomorrow.group(3)) if tomorrow.group(3) else 0
            hour = _parse_12h_to_24h(hour, tomorrow.group(4))
            target = (now + timedelta(days=1)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            due = _to_iso(target)
            type_ = "task"

    if due is None:
        gdate = _GERMAN_DATE_RE.search(text)
        if gdate:
            day = int(gdate.group(1))
            month = int(gdate.group(2))
            year = int(gdate.group(3)) if gdate.group(3) else now.year
            hour = int(gdate.group(4))
            minute = int(gdate.group(5)) if gdate.group(5) else 0
            target = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            due = _to_iso(target)
            type_ = "task"

    if reminder and due is None:
        # "remind me" without a time: default to +1h so the caller can still
        # persist the reminder intent. The server may overwrite this.
        due = _to_iso(now + timedelta(hours=1))
        type_ = "task"

    return ParsedNote(
        type=type_,
        due=due,
        tags=tags,
        wikilinks=wikilinks,
        reminder=reminder,
    )
