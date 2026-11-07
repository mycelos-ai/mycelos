# Workflow Agent — LLM-Powered Workflow Execution

> **Week:** 13 (2026)
> **Status:** Draft
> **Depends on:** Knowledge System v2

## 1. Problem

The current WorkflowRunner is a dumb dispatcher — it calls tools sequentially
but can't reason, summarize, make decisions, or ask questions. Real workflows
need intelligence: "Is this breaking news?" requires judgment, not a tool call.

## 2. Solution

Replace the WorkflowRunner with a **Workflow Agent** — an LLM that executes
a plan using tools, makes decisions based on the plan's instructions, and can
pause for user clarification when needed.

## 3. How It Works

### Workflow Definition (stored in DB)
```yaml
id: daily-news-summary
name: Daily News Summary
model: haiku                          # cheapest model that can handle this
tools: [http_get, note_write, telegram_send, search_web]
schedule: "0 8 * * *"                 # every day at 8am

plan: |
  You are the Daily News Summary agent.

  Your task:
  1. Fetch these websites using http_get (format: markdown):
     - https://www.heise.de
     - https://www.spiegel.de
     - https://news.orf.at
  2. Read the content and identify the top 5 news stories.
  3. Write a concise summary (German, 3-5 sentences per story).
  4. Decide: Are there breaking news that the user MUST know immediately?
     - YES → Send a short Telegram message with the headline + 1 sentence
     - NO → Skip Telegram
  5. Save the full summary as a knowledge note:
     - Title: "News Summary [today's date]"
     - Type: note
     - Topic: "daily-news"
     - Tags: [news, daily, summary]
  6. Done.
```

### Execution (Runtime)
```
Trigger: Scheduler or User ("Fasse mir die News zusammen")
    ↓
WorkflowAgent receives:
  - system_prompt = workflow.plan
  - tools = workflow.tools (filtered by ToolRegistry permissions)
  - model = workflow.model
    ↓
LLM Loop (same pattern as ChatService.handle_message):
  - LLM reads plan + previous tool results
  - LLM decides: call a tool or generate text
  - Tool call → ToolRegistry.execute() → result back to LLM
  - LLM decides: next tool or done
  - Repeat until LLM says done or max_rounds reached
    ↓
Result:
  - Audit log: workflow executed, steps taken, cost
  - Knowledge note created (if plan says so)
  - Telegram sent (if plan decided to)
  - Status: completed / failed / needs_clarification
```

## 4. Workflow Agent vs ChatService

| | ChatService (Mycelos) | WorkflowAgent |
|---|---|---|
| System prompt | Mycelos personality + handoff rules | Workflow plan |
| Tools | All Mycelos tools | Only workflow-allowed tools |
| Model | User's default | Workflow-specified |
| Trigger | User message | Schedule or run_workflow |
| User interaction | Always (chat) | Only on clarification |
| Session | User session | Own ephemeral session |
| Cost tracking | Per session | Per workflow run |

## 5. User Clarification (Pause/Resume)

### When the agent needs input
The LLM can't always decide autonomously. Example:
```
Plan: "If you find conflicting information, ask the user which source to trust."
LLM: "Heise says X, Spiegel says Y. I need clarification."
```

### Flow
```
WorkflowAgent runs → hits a point where it needs input
    ↓
Agent sets status: "needs_clarification"
Saves: question text, workflow_id, partial results, conversation so far
    ↓
Notification:
  - If Telegram is configured → send message: "Workflow 'daily-news' has a question: ..."
  - If user is in a chat session → show inline: "⚠ Workflow needs your input"
  - Web UI: badge on Workflows page
    ↓
User responds (via Chat, Telegram, or Web):
  "Trust Heise for tech news"
    ↓
WorkflowAgent resumes:
  - Loads saved conversation + partial results
  - Appends user's answer
  - Continues LLM loop from where it paused
    ↓
Result: completed
```

### DB Schema
```sql
-- workflow_runs table (already exists, extend)
ALTER TABLE workflow_runs ADD COLUMN conversation TEXT;  -- JSON: the LLM conversation so far
ALTER TABLE workflow_runs ADD COLUMN clarification TEXT;  -- The question for the user
```

### Status Lifecycle
```
pending → running → completed
                  → failed
                  → needs_clarification → running → completed
```

## 6. Tool Access (Security)

Each workflow defines which tools it can use. The WorkflowAgent gets ONLY those:

```yaml
tools: [http_get, note_write]  # This workflow can fetch web + write notes
                                 # It CANNOT access filesystem, connectors, etc.
```

The Builder sets the tools when creating the workflow — based on what the
workflow needs. The user confirms during creation.

Permissions flow:
```
Workflow definition → tools list
    ↓
WorkflowAgent startup → ToolRegistry.get_tools_for_workflow(tools_list)
    ↓
LLM only sees these tools in its function definitions
    ↓
ToolRegistry.execute() double-checks permission before running
```

## 7. Model Selection

The Builder decides the model when creating the workflow:

```yaml
model: haiku     # Simple tasks: fetch + summarize → cheap
model: sonnet    # Complex reasoning: analyze + decide → mid-tier
model: opus      # Critical tasks: code review, security audit → best
```

Cost-awareness: The Builder should prefer the cheapest model that can handle
the task. The user can override in the workflow definition.

Failover: If the primary model is unavailable (rate limit), the LLM broker's
existing failover kicks in (e.g., Claude → GPT-5.4).

## 8. Integration with ChatService

### Manual Trigger
```
User: "Fasse mir die News zusammen"
Mycelos: recognizes → existing workflow "daily-news-summary"
Mycelos: calls run_workflow("daily-news-summary", {})
    ↓
WorkflowAgent starts in background
    ↓
When done: result appears in chat as system message
```

### Scheduled Trigger
```
Scheduler: "daily-news-summary is due"
    ↓
WorkflowAgent starts (no user session)
    ↓
When done:
  - Note created in Knowledge
  - If Telegram configured: summary sent
  - Audit logged
  - If needs_clarification: notification sent
```

### Clarification in Chat
```
User opens chat → Mycelos checks for pending clarifications
Mycelos: "⚠ Der Workflow 'daily-news' hat eine Frage:
  'Heise und Spiegel berichten widersprüchlich über X. Welche Quelle soll ich bevorzugen?'"
User: "Heise für Tech, Spiegel für Politik"
Mycelos: forwards answer to WorkflowAgent → resumes
```

## 9. Implementation

### WorkflowAgent class
```python
class WorkflowAgent:
    """Executes a workflow plan using LLM + tools."""

    def __init__(self, app, workflow_def, run_id):
        self.app = app
        self.plan = workflow_def["plan"]        # System prompt
        self.tools = workflow_def["tools"]       # Allowed tool names
        self.model = workflow_def.get("model", "haiku")
        self.run_id = run_id
        self.conversation = []                   # LLM conversation history
        self.max_rounds = 20                     # Safety limit

    def execute(self, inputs=None):
        """Run the workflow plan. Returns WorkflowRunResult."""
        # Build system prompt
        self.conversation = [
            {"role": "system", "content": self.plan},
        ]
        if inputs:
            self.conversation.append({
                "role": "user",
                "content": f"Inputs: {json.dumps(inputs)}",
            })

        # Get tool schemas for allowed tools only
        tool_schemas = ToolRegistry.get_schemas_for(self.tools)

        # LLM loop
        for round in range(self.max_rounds):
            response = self.app.llm.complete(
                self.conversation,
                tools=tool_schemas,
                model=self.model,
            )

            # Text response (no tool call) → done or clarification
            if not response.tool_calls:
                self.conversation.append({"role": "assistant", "content": response.content})
                if "clarification" in response.content.lower() or "frage" in response.content.lower():
                    return WorkflowRunResult(
                        status="needs_clarification",
                        clarification=response.content,
                        conversation=self.conversation,
                    )
                return WorkflowRunResult(status="completed", result=response.content)

            # Tool calls → execute and feed back
            self.conversation.append({"role": "assistant", "tool_calls": response.tool_calls})
            for tc in response.tool_calls:
                tool_name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"])
                result = ToolRegistry.execute(tool_name, args, context={...})
                self.conversation.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result),
                })

        return WorkflowRunResult(status="failed", error="Max rounds exceeded")

    def resume(self, user_answer):
        """Resume after user clarification."""
        self.conversation.append({"role": "user", "content": user_answer})
        return self.execute()  # Continue the loop
```

### Workflow Definition (DB schema addition)
```sql
ALTER TABLE workflows ADD COLUMN plan TEXT;        -- LLM system prompt
ALTER TABLE workflows ADD COLUMN model TEXT DEFAULT 'haiku';
ALTER TABLE workflows ADD COLUMN allowed_tools TEXT DEFAULT '[]';  -- JSON array
```

## 10. Builder Creates Workflow Agents

When the Builder creates a workflow, it now writes:
- `plan` — the full system prompt with step-by-step instructions
- `model` — cheapest model that can handle the complexity
- `allowed_tools` — only the tools this workflow needs
- `steps` — kept for backwards compat + Web UI display

The `plan` field is the "intelligence" — the steps field is the "summary".

## 11. Priority

1. **WorkflowAgent class** — LLM loop with tools
2. **Schema changes** — plan, model, allowed_tools columns
3. **Update Builder** — generates plan + model when creating workflows
4. **Clarification flow** — pause/resume with notification
5. **Update run_workflow tool** — delegates to WorkflowAgent
6. **Scheduler integration** — use WorkflowAgent instead of old runner
7. **Chat integration** — show pending clarifications
