// Deterministic DE+EN note parser — client-side twin of
// mycelos/knowledge/parse_note.py. The Python version is the source of
// truth; this module is kept in lockstep via shared test vectors in
// tests/fixtures/parse-note-vectors.json.

const WIKILINK_RE = /\[\[([^\]|]+)(?:\|[^\]]+)?\]\]/g;
const TAG_RE = /(?:^|\s)#([A-Za-zÄÖÜäöüß0-9_-]+)/g;
const TODO_RE = /^\s*(TODO|FIXME|AUFGABE)\s*[:\s]/i;
const REMIND_RE = /\b(remind\s+me|erinnere\s+mich|erinner\s+mich)\b/i;
const IN_DURATION_RE = /\bin\s+(\d+)\s+(minuten|minutes|minute|min|stunden|stunde|hours|hour|std|h)\b/i;
const TOMORROW_RE = /\b(tomorrow|morgen)\b(?:\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|uhr)?)?/i;
const GERMAN_DATE_RE = /\b(\d{1,2})\.(\d{1,2})\.(\d{4})?\s*(\d{1,2})(?::(\d{2}))?/;

function toIso(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return (
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    `T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}Z`
  );
}

function durationSeconds(amount, unit) {
  const u = unit.toLowerCase();
  if (u.startsWith('min')) return amount * 60;
  if (u.startsWith('h') || u.startsWith('std') || u.startsWith('stunde') || u.startsWith('hour')) {
    return amount * 3600;
  }
  return amount * 60;
}

function twelveToTwentyFour(hour, ampm) {
  if (!ampm) return hour;
  const a = ampm.toLowerCase();
  if (a === 'pm' && hour < 12) return hour + 12;
  if (a === 'am' && hour === 12) return 0;
  return hour;
}

export function parseNoteText(text, now = new Date()) {
  const wikilinks = [];
  let m;
  const wRe = new RegExp(WIKILINK_RE.source, 'g');
  while ((m = wRe.exec(text)) !== null) wikilinks.push(m[1]);

  const tags = [];
  const tRe = new RegExp(TAG_RE.source, 'g');
  while ((m = tRe.exec(text)) !== null) tags.push(m[1]);

  const reminder = REMIND_RE.test(text);
  let type = 'note';
  let due = null;

  if (TODO_RE.test(text)) type = 'task';

  const dur = text.match(IN_DURATION_RE);
  if (dur) {
    const amount = parseInt(dur[1], 10);
    const seconds = durationSeconds(amount, dur[2]);
    due = toIso(new Date(now.getTime() + seconds * 1000));
    type = 'task';
  }

  if (due === null) {
    const tm = text.match(TOMORROW_RE);
    if (tm) {
      let hour = tm[2] ? parseInt(tm[2], 10) : 9;
      const minute = tm[3] ? parseInt(tm[3], 10) : 0;
      hour = twelveToTwentyFour(hour, tm[4]);
      const target = new Date(now.getTime());
      target.setUTCDate(target.getUTCDate() + 1);
      target.setUTCHours(hour, minute, 0, 0);
      due = toIso(target);
      type = 'task';
    }
  }

  if (due === null) {
    const gd = text.match(GERMAN_DATE_RE);
    if (gd) {
      const day = parseInt(gd[1], 10);
      const month = parseInt(gd[2], 10) - 1;
      const year = gd[3] ? parseInt(gd[3], 10) : now.getUTCFullYear();
      const hour = parseInt(gd[4], 10);
      const minute = gd[5] ? parseInt(gd[5], 10) : 0;
      due = toIso(new Date(Date.UTC(year, month, day, hour, minute, 0)));
      type = 'task';
    }
  }

  if (reminder && due === null) {
    due = toIso(new Date(now.getTime() + 3600 * 1000));
    type = 'task';
  }

  return { type, due, tags, wikilinks, reminder };
}
