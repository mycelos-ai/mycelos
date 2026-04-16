"""DoctorAgent — LLM-powered diagnostic agent with AGENT.md context."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mycelos.app import App

logger = logging.getLogger("mycelos.doctor")


class DoctorAgent:
    """Diagnoses system issues using LLM + AGENT.md + audit data."""

    def __init__(self, app: "App") -> None:
        self._app = app

    def build_context(self, question: str) -> str:
        """Build the diagnosis context: AGENT.md + relevant data."""
        parts = []

        # 1. AGENT.md — full system knowledge
        agent_md = self._load_agent_md()
        if agent_md:
            parts.append(f"## System Documentation (AGENT.md)\n\n{agent_md}")

        # 2. Recent audit events (last 30)
        from mycelos.doctor.tools import doctor_query_audit
        events = doctor_query_audit(self._app, limit=30)
        if events:
            event_lines = []
            for e in events:
                event_lines.append(f"  {e['created_at']} | {e['event_type']} | {e.get('details', '')}")
            parts.append("## Recent Audit Events\n\n" + "\n".join(event_lines))

        # 3. Config history
        from mycelos.doctor.tools import doctor_config_history
        configs = doctor_config_history(self._app, limit=5)
        if configs:
            config_lines = []
            for c in configs:
                active = " ← ACTIVE" if c.get("active") else ""
                config_lines.append(f"  Gen {c['id']}: {c.get('description', '?')} ({c.get('trigger', '?')}){active}")
            parts.append("## Config Generations\n\n" + "\n".join(config_lines))

        # 4. Reminder state (if question is about reminders)
        if any(w in question.lower() for w in ("remind", "erinner", "task", "notification", "benachricht")):
            from mycelos.doctor.tools import doctor_check_reminders
            r = doctor_check_reminders(self._app)
            parts.append(f"## Reminder State\n\nDue tasks: {r['due_count']}")
            if r["tasks"]:
                for t in r["tasks"]:
                    parts.append(f"  - {t['title']} (due: {t.get('due')}, remind_via: {t.get('remind_via')})")
            if r["last_sent"]:
                parts.append(f"  Last reminder.sent: {r['last_sent']['created_at']} — {r['last_sent']['details']}")

        # 5. Schedule state (if question is about schedules)
        if any(w in question.lower() for w in ("schedule", "daily", "workflow", "cron", "missed")):
            from mycelos.doctor.tools import doctor_check_schedules
            s = doctor_check_schedules(self._app)
            parts.append(f"## Scheduler State\n\nTotal: {s['total']}, Active: {s['active']}, Missed: {s['missed']}")
            for sched in s["schedules"]:
                parts.append(f"  - {sched['workflow_id']} ({sched['schedule']}) last_run={sched.get('last_run')}")

        # 6. Telegram state (if question is about telegram)
        if any(w in question.lower() for w in ("telegram", "bot", "push", "notification", "benachricht")):
            from mycelos.doctor.tools import doctor_check_telegram
            tg = doctor_check_telegram(self._app)
            parts.append(f"## Telegram State\n\n{json.dumps(tg, indent=2)}")

        # 7. Server health
        try:
            import httpx
            resp = httpx.get("http://localhost:9100/api/health", timeout=2)
            if resp.status_code == 200:
                parts.append(f"## Server Health\n\n{json.dumps(resp.json(), indent=2)}")
        except Exception:
            parts.append("## Server Health\n\nServer not reachable (not running or port changed)")

        return "\n\n".join(parts)

    def diagnose(self, question: str) -> str:
        """Run LLM diagnosis for a user question."""
        llm = getattr(self._app, "_llm", None) or getattr(self._app, "llm", None)
        if not llm:
            return "LLM not available. Check your API credentials."

        context = self.build_context(question)

        # Use strongest available model for diagnosis
        model = self._app.resolve_strongest_model()

        prompt = (
            "You are the Mycelos Doctor — a diagnostic agent. "
            "The user has a problem. Analyze the system state below and explain:\n"
            "1. What happened (root cause)\n"
            "2. Why it happened (based on audit events, config, and architecture)\n"
            "3. How to fix it (specific commands or actions)\n\n"
            "Be concise but thorough. Use the system documentation to reason about architecture.\n"
            "Respond in the user's language.\n\n"
            f"## User Question\n{question}\n\n"
            f"{context}"
        )

        try:
            response = llm.complete(
                [{"role": "user", "content": prompt}],
                model=model,
            )
            return response.content
        except Exception as e:
            logger.error("Doctor LLM diagnosis failed: %s", e)
            return f"Diagnosis failed: {e}\n\nTry: mycelos doctor (without --why) for basic checks."

    def _load_agent_md(self) -> str | None:
        """Load AGENT.md from the project root."""
        # Try multiple locations
        candidates = [
            Path(__file__).parent.parent.parent.parent / "AGENT.md",
            Path.cwd() / "AGENT.md",
        ]
        for p in candidates:
            if p.exists():
                try:
                    return p.read_text(encoding="utf-8")
                except Exception:
                    pass
        return None
