# Mycelos — Use Cases, User Flows, and Where the Moat Is

Week 33 (2026). Companion to `2026-W33-brain-ui-concept.md`: that document says *how the screens work*; this one says *what the product is for*, how a user moves through it, and where it differs from the tools it will be compared against.

Written as input for the visual design. Everything marked **[live]** works today; **[built, unsurfaced]** exists in code but has no good home in the UI; **[planned]** is designed but not built.

---

## 1. The one-sentence positioning

> **Obsidian is a knowledge base you maintain. Mycelos is a knowledge base that maintains itself — and that other tools can pour into.**

The comparison set splits cleanly:

| | Obsidian | Notion | gbrain | Tencent Agent-Memory | **Mycelos** |
|---|---|---|---|---|---|
| Primary user | human writer | team | AI agent | AI agent | **human + their agents** |
| Who organizes | human | human | agent (nightly) | LLM (scene merge) | **agent, human confirms** |
| Local-first | yes | no | yes | yes | **yes** |
| Runs without cloud LLM | yes (no AI) | no | no | partly | **yes (local embeddings)** |
| EU-clean option | yes (no AI) | no | no | no | **yes, enforced** |
| Ingests from your tools | plugins | integrations | recipes | no | **connectors + MCP + OKF** |
| Audit trail | no | limited | no | no | **every mutation** |
| Talks to you | no | AI add-on | via host agent | via host agent | **native, multi-channel** |

The two AI-native competitors (gbrain, Tencent) are *libraries for agents*. Neither is a product a human opens in the morning. Obsidian and Notion are products humans open, but neither organizes itself or accepts machine feeds without manual plumbing.

**Mycelos sits in the gap: a self-organizing brain with a human front door.**

---

## 2. What Mycelos can do today

### Getting knowledge in
- **Talk to it** — chat in the app or over Telegram; anything worth keeping becomes a note **[live]**
- **Speak to it** — voice input, transcribed **[live]**
- **Drop documents** — PDFs, Word, images; text extracted and indexed **[live]**
- **Connect a mailbox** — Gmail ingest with idempotent re-runs **[live, unsurfaced]**
- **Import a folder** — bulk markdown import **[live]**
- **Feed it from your own tools** — OKF bundles, MCP sync **[planned: yt-summary first]**

### Organizing itself
- **Auto-classification** — an LLM places new notes into topics with a confidence score; confident ones silently, unsure ones as suggestions **[live]**
- **Duplicate detection** — semantic, vector-only, never keyword **[live]**
- **Lifecycle** — done tasks, fired reminders and archived notes age out on schedule **[live]**
- **Typed link graph** — wikilinks and relations extracted, `merged_from` provenance on merges **[live]**
- **Suggestion inbox** — everything unsure waits for one click **[live, weakly surfaced]**

### Getting knowledge out
- **Hybrid search** — BM25 + vector fused via RRF, diacritics-insensitive (German-friendly) **[live, new]**
- **Ask across everything** — chat answers grounded in the knowledge base **[live]**
- **Morning briefing** — overdue tasks, today's reminders, yesterday's new notes, synthesized into a short text, delivered to Telegram **[live, opt-in]**
- **Reminders** — time-based, multi-channel **[live]**
- **Interactive graph** — force-directed, clickable **[live, needs the redesign]**
- **OKF export** — the whole tree as a portable bundle **[live]**

### Running it your way
- **Local embeddings** — multilingual E5, no cloud call for semantic search **[live, new]**
- **EU mode** — enforced at the broker; refuses non-EU providers rather than silently falling back **[live]**
- **Own infrastructure** — Docker on your own box; no account, no SaaS **[live]**
- **Full audit trail** — every state change logged **[live]**

---

## 3. The moat — five things, honestly rated

### 3.1 Self-organizing with a confirmation loop — **strong**
Obsidian and Notion put the filing burden on the human; that is why knowledge bases die. gbrain organizes autonomously but has no confirmation surface — you trust it or you don't. Mycelos classifies with a confidence score and routes the unsure cases to an inbox: automatic where it is safe, human where it matters, and every correction can teach the rule that caused it.

**Why it's hard to copy:** it is not the classifier, it is the plumbing around it — confidence floors, fail-closed acceptance, undo, provenance. Roughly the work of the last two weeks.

### 3.2 Security posture as a product feature — **strongest, and unique**
Nobody else in this comparison has it. gbrain's default stack requires US SaaS keys for embeddings, reranking and enrichment; Tencent's gateway is open by default and its cloud backend *fails open* (returns empty on error). Mycelos is fail-closed by constitution, audits every mutation, keeps credentials away from agents entirely, and can run fully EU-resident with local embeddings.

**Why it's hard to copy:** retrofitting fail-closed behavior into a system designed fail-open means touching every path. We know, because we just spent two weeks finding the places where our own posture had leaked.

**This is the moat to lead with for anyone with client data** — accountants, lawyers, consultants, anyone under GDPR.

### 3.3 Ingest from your own tools — **strong, mostly unbuilt**
The pattern — a source attaches to a folder subtree, carries one free-text rule, feeds through OKF or MCP — is genuinely differentiated. Obsidian needs a plugin per source and manual filing. Notion needs an integration and manual filing. gbrain has recipes but no rule-per-source model. Nobody has "attach your own tool's MCP server and tell it in one sentence where things belong."

**Status:** designed, first implementation (yt-summary) planned. **This is the highest-leverage unbuilt thing.**

### 3.4 Multi-channel presence — **moderate**
Telegram in, Telegram out, voice, briefings. The brain reaches you where you are instead of waiting in a tab. Obsidian is where you go; Mycelos also comes to you.

**Why only moderate:** copyable. It is a nice-to-have that raises daily usage, not a defensible position.

### 3.5 Local-first *and* AI-native — **structural**
Obsidian is local but has no native AI; Notion has AI but no local option; gbrain and Tencent are AI-native but need cloud services for real quality. Mycelos is the only one where semantic search, classification and conversation can all run on your own hardware.

**Why it's structural:** the competitors' business models depend on the cloud path. They *could* build local-first; it would cannibalize them.

### Where we are honestly behind
- **Editing experience** — Obsidian's editor is a decade ahead. We should not compete there; we should export cleanly to it.
- **Mobile** — Obsidian and Notion have real apps; we have a web UI.
- **Ecosystem** — Obsidian has thousands of plugins. Our answer is MCP, but that is a bet, not a fact yet.
- **Polish** — they have design teams. That is what this redesign is addressing.

---

## 4. User flows

### Flow A: The daily loop (the core one)

1. **Land on the graph.** Not a chat prompt, not a blank page — your knowledge, as it is now. The status line says what is new and what waits.
2. **Search from the top.** Typing highlights matching nodes *in place* — the graph stays, results light up inside it. This is what makes it a workspace instead of a picture.
3. **Open a node.** Content, where it came from, what is filed here, what links here, what arrived recently.
4. **Ask about it.** The conversation is anchored to what you are looking at; answers cite the nodes they used, and clicking a citation moves the graph there.
5. **Keep the answer.** One gesture turns the exchange into a note. It is placed like anything else — the loop closes.

**The point:** search → read → ask → keep, without leaving one surface. In Obsidian steps 4 and 5 need a plugin and a decision about where to file. Here the brain files it.

### Flow B: Connecting a source (the differentiating one)

1. **Pick the folder** it belongs to — or root when it is mixed.
2. **Attach the source** right there on the node, not in a settings page.
3. **Write the rule in one sentence:** *"Mails from @vorfina.de belong under Vorfina, newsletters go to Archive, anything about taxes to Tax law."*
4. **It runs.** Content flows into the attached subtrees — the attached folder or anything beneath it, never above, never sideways.
5. **It is wrong sometimes.** The node shows recent arrivals; one gesture moves a misfiled note, and Mycelos offers to teach the rule.

**The point:** you configure a source once, in your own words, at the place it feeds. Nobody else does this.

### Flow C: Capture on the move

1. **Send it to Telegram** — a thought, a voice message, a link.
2. **It lands and gets classified.** Confident → filed. Unsure → inbox.
3. **The morning briefing** tells you what arrived, what is due, what is waiting.

**The point:** capture has no friction and no app-switch. The brain is a contact, not a destination.

### Flow D: Bringing existing knowledge in

1. **Import** a markdown folder, an OKF bundle, or (planned) a ChatGPT/Claude export.
2. **The organizer proposes structure** for material that arrived without any.
3. **Confirm in bulk** from the inbox, correct what is wrong.

**The point:** the migration path in. Obsidian imports files; it does not organize them.

### Flow E: Tending the brain

1. **The inbox** collects everything unsure — placements, merges, unclassifiable notes, failed source runs.
2. **Each entry states what and why**, with confidence.
3. **Accept, place elsewhere, or dismiss** — and placing elsewhere offers to teach.
4. **Brain health** is a status line, not a chore: how much knowledge, how connected, what is stale.

**The point:** maintenance is bounded and visible instead of infinite and invisible. This is the mechanism that decides whether the brain is alive in six months.

---

## 5. What to borrow from the competitors

From the W32 research (`2026-08-09-knowledge-brain-competitive-research.md` in local-reviews), still worth taking:

1. **Two-layer notes** (gbrain): compiled truth above the line, append-only evidence timeline below, each claim typed as observed/inferred with a date. Would make entity notes (people, projects, clients) far stronger than flat markdown.
2. **The dream cycle** (gbrain): nightly maintenance as a budgeted job — contradiction detection, salience scoring, consolidation. We have the organizer; this is the ambitious version of it.
3. **Consolidation under a capacity budget** (Tencent): when a level is full, force a merge instead of growing forever. More elegant than any decay heuristic.
4. **Persona as a living document** (Tencent): a narrative profile of the user, rewritten as it learns. Fits our memory service and the onboarding redesign.
5. **Evidence contract on search results** (gbrain): every result says *why* it matched and how safe it is to treat as new. Feeds the duplicate detection directly.

---

## 6. The pitch, three lengths

**One line:** The knowledge brain that organizes itself, runs on your hardware, and lets your tools pour into it.

**Three lines:** Everything you learn — conversations, videos, mail, documents — flows into one brain that files it for you, links it, and can be asked about it in plain language. It runs on your own machine, works without any cloud service, and logs every change. Your other tools connect through MCP and feed it directly.

**For the GDPR-bound professional:** A second brain you can put client data into. Local embeddings, enforced EU providers, fail-closed security, and an audit trail for every change — with the self-organizing and conversational qualities you would otherwise only get from a US cloud service.
