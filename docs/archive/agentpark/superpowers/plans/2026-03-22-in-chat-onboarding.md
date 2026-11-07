# In-Chat Onboarding — Erste Conversation = Setup

**Status:** PLAN (nicht implementieren, nur Konzept)
**Goal:** Erster `mycelos chat` Start fuehrt den User in 5 Minuten zum "das ist MEIN Assistent" Gefuehl.

---

## Design-Philosophie

Kein separates CLI-Command. Kein Config-Wizard. Das Onboarding IST die erste Conversation. Mycelos fragt, der User antwortet, und nach jedem Schritt passiert etwas Sichtbares.

---

## Die 5 Schritte

### Step 0: Begruesssung + Name

```
Mycelos> Hallo! Ich bin Mycelos, dein persoenlicher Assistent.
        Wie heisst du?

User> Stefan

Mycelos> Freut mich, Stefan! Ab jetzt merke ich mir alles.
        Was beschaeftigt dich gerade? Wofuer haettest du gerne Hilfe?
```

**Erfolgsmoment:** Mycelos spricht die Sprache des Users. Persoenlich ab Sekunde 1.
**System:** `memory.set("user.name", "Stefan")`, Audit: `onboarding.step_1`

### Step 1: Live Demo — Sofort was Echtes tun

Basierend auf der Antwort des Users tut Mycelos SOFORT etwas:

| User sagt... | Mycelos macht... |
|---|---|
| News, AI, Tech | Web-Suche + Zusammenfassung |
| Notizen, Ideen | Markdown-Notiz erstellen (wenn Mount da) |
| Recherche, Thema | Themen-Recherche mit Ergebnissen |
| "Weiss nicht" | Default: AI-News von heute |

```
Mycelos> Das hat 4 Sekunden gedauert und $0.003 gekostet.
        Soll ich das jeden Morgen fuer dich machen?
```

**Erfolgsmoment:** Echte Information. Nicht "ich KOENNTE das", sondern "hier SIND die Ergebnisse".
**Stickiness:** Kosten-Transparenz ("0.3 Cent fuer 3 Minuten Lesearbeit").

### Step 2: Memory — "Ich merke mir das"

```
Mycelos> Ich merke mir: du interessierst dich fuer AI-News
        und willst Zeit bei der Recherche sparen.

        Du kannst jederzeit sehen was ich weiss: /memory list
```

**Erfolgsmoment:** "Mycelos hat ein Gedaechtnis" — anders als ChatGPT.
**System:** `memory.set("user.preference.topic", "AI-News")`

### Step 3: Ordner-Zugriff (optional)

```
Mycelos> Wenn du moechtest, kann ich Ergebnisse auch als Dateien
        speichern. Dafuer braeuchte ich Zugriff auf einen Ordner.

  Execute `/mount add ~/Documents --rw`? [Y/n]: y
  ✓ Documents freigegeben!
```

**Erfolgsmoment:** User kontrolliert Zugriff explizit. Sicher + einfach.
**Skip:** "Lass das erstmal" → Mycelos geht weiter, kein Dead-End.

### Step 4: Personalisiertes Menu

```
Mycelos> Stefan, basierend auf dem was du mir erzaehlt hast:

        1. 📰 Morgen-Briefing — Jeden Tag AI-News in ~/Documents
        2. 🔍 Recherche on demand — Thema nennen, ich fasse zusammen
        3. 📝 Notizen — Gedanken als Markdown speichern
        4. 🧾 Rechnungen — PDFs scannen und Daten extrahieren

        Was davon klingt gut?
```

**Erfolgsmoment:** Die Vorschlaege reflektieren was der User GESAGT hat. Nicht generisch.
**Stickiness:** Jeder Vorschlag ist ein natuerlicher Gespraechsstarter.

---

## Stickiness-Mechanismen

1. **Progressive Value:** Jeder Schritt macht Mycelos wertvoller (Name → Demo → Memory → Files → Vorschlaege). Aufhoeren = Wissen verlieren.

2. **Memory-Hook:** "Ich merke mir das" pflanzt die Idee dass Mycelos mit der Zeit immer besser wird.

3. **Kosten-Transparenz:** "$0.003" entmystifiziert die Kosten und macht den Wert greifbar.

4. **Morning-Briefing-Hook:** Wenn der User ein taegliches Briefing will, hat er einen Grund jeden Morgen Mycelos zu oeffnen.

5. **Aha-Moment:** Step 1 — echte Ergebnisse in 4 Sekunden. Ab da denkt der User nicht mehr "Setup" sondern "nuetzlich".

---

## Telegram-Vorschlag (nicht im Onboarding, danach)

Nicht in den ersten 5 Minuten. Nach der ersten oder zweiten Session:

```
Mycelos> Uebrigens: Wenn du mich auch vom Handy nutzen willst,
        kann ich als Telegram-Bot laufen. Interesse?
```

Telegram-Setup ist komplex genug um NICHT Teil des ersten Onboardings zu sein.

---

## Skip-Mechanismen

| Situation | Verhalten |
|---|---|
| User tippt /help | Volle Command-Liste, Onboarding-Modus endet |
| User stellt sofort eine Fachfrage | Mycelos antwortet normal, fragt spaeter nach Name |
| User sagt "Skip" | Onboarding endet, normaler Chat |
| Returning User (Name schon da) | Kein Onboarding, normaler Welcome-Back |

---

## Metriken

| Metrik | Ziel | Messung |
|--------|------|---------|
| Completion Rate | >80% erreichen Step 4 | Memory-Eintraege als Proxy |
| Time to First Value | <3 Min bis Step 1 | Timestamp-Delta |
| 7-Day Retention | >50% kommen zurueck | Sessions mit gleichem user.name |
| Drop-Off Step | Tracken pro Step | Audit-Events `onboarding.step_N` |
| Cost per Onboarding | <$0.05 | LLM Usage mit purpose="onboarding" |

---

## Implementation (wenn wir es bauen)

Kein neues System noetig. Nur:
1. **System-Prompt erweitern** — Onboarding-Anweisungen fuer den Chat-Agent
2. **Audit-Events** — `onboarding.step_N` bei jedem Schritt
3. **Memory-basierte Detection** — kein user.name = Onboarding-Modus

Alles andere (Memory, Mounts, Slash Commands, Tool Use) existiert bereits.
