"""Doctor health checks — quick status checks for each subsystem."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mycelos.app import App


def check_storage(app: "App") -> dict[str, Any]:
    """Check SQLite database health."""
    try:
        row = app.storage.fetchone("SELECT COUNT(*) as cnt FROM audit_events")
        count = row["cnt"] if row else 0

        # Check WAL mode
        wal = app.storage.fetchone("PRAGMA journal_mode")
        mode = wal["journal_mode"] if wal else "unknown"

        return {
            "category": "storage",
            "status": "ok",
            "details": f"DB accessible, {count} audit events, journal={mode}",
        }
    except Exception as e:
        return {
            "category": "storage",
            "status": "error",
            "details": f"Database error: {e}",
        }


def check_credentials(app: "App") -> dict[str, Any]:
    """Check if LLM credentials are configured."""
    try:
        from mycelos.doctor.tools import doctor_check_credentials
        creds = doctor_check_credentials(app)
        if creds["count"] == 0:
            return {
                "category": "credentials",
                "status": "warning",
                "details": "No credentials stored. Run: mycelos credential store <provider> <key>",
            }
        services = ", ".join(c["service"] for c in creds["services"])
        return {
            "category": "credentials",
            "status": "ok",
            "details": f"{creds['count']} credential(s): {services}",
        }
    except Exception as e:
        return {
            "category": "credentials",
            "status": "error",
            "details": f"Credential check failed: {e}",
        }


def check_server(gateway_url: str | None) -> dict[str, Any]:
    """Check if the gateway server is running."""
    if not gateway_url:
        return {
            "category": "server",
            "status": "error",
            "details": "Server not running. Start with: mycelos serve",
        }
    try:
        import httpx
        resp = httpx.get(f"{gateway_url}/api/health", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            uptime = data.get("uptime_seconds", 0)
            hours = int(uptime // 3600)
            mins = int((uptime % 3600) // 60)
            return {
                "category": "server",
                "status": "ok",
                "details": f"Running ({hours}h {mins}m uptime), scheduler={'ON' if data.get('scheduler') else 'OFF'}",
            }
        return {
            "category": "server",
            "status": "error",
            "details": f"Health endpoint returned {resp.status_code}",
        }
    except Exception:
        return {
            "category": "server",
            "status": "error",
            "details": "Server not reachable. Start with: mycelos serve",
        }


def check_telegram(app: "App") -> dict[str, Any]:
    """Check Telegram bot configuration."""
    from mycelos.doctor.tools import doctor_check_telegram
    tg = doctor_check_telegram(app)

    if not tg["configured"]:
        return {
            "category": "telegram",
            "status": "not configured",
            "details": "No Telegram channel. Set up with: /connector add telegram <token>",
        }
    issues = []
    if not tg["has_token"]:
        issues.append("no bot token")
    if not tg["has_chat_id"]:
        issues.append("no chat_id (send a message to your bot first)")
    if tg.get("allowlist_empty"):
        issues.append("empty allowlist — bot will reject ALL messages (run: mycelos connector setup telegram)")
    if issues:
        status = "error" if tg.get("allowlist_empty") else "warning"
        return {
            "category": "telegram",
            "status": status,
            "details": f"Configured but: {', '.join(issues)}",
        }
    return {
        "category": "telegram",
        "status": "ok",
        "details": f"Bot configured, chat_id={tg['chat_id']}, {len(tg.get('allowed_users', []))} allowed user(s)",
    }


def check_reminder_scheduler(app: "App") -> dict[str, Any]:
    """Verify the periodic reminder scheduler is running.

    The Huey consumer should emit a ``reminder.tick`` audit event every
    minute. If there's been no tick in the last two hours, the consumer
    is almost certainly dead — which means reminders won't fire, even
    if they're correctly persisted with ``remind_at``.
    """
    row = app.storage.fetchone(
        """SELECT created_at FROM audit_events
           WHERE event_type = 'reminder.tick'
           ORDER BY created_at DESC LIMIT 1""",
    )
    if not row:
        return {
            "category": "reminder_scheduler",
            "status": "warn",
            "details": (
                "No reminder.tick events found yet. The Huey consumer may "
                "not be running — reminders won't fire until it is."
            ),
        }
    last = row["created_at"]
    try:
        from datetime import datetime, timezone, timedelta
        # audit_events.created_at is UTC ISO with fractional seconds
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last_dt
        if age > timedelta(hours=2):
            return {
                "category": "reminder_scheduler",
                "status": "warn",
                "details": (
                    f"Last reminder.tick was {int(age.total_seconds()/60)} min ago "
                    f"— expected every minute. Scheduler may be stuck."
                ),
            }
        return {
            "category": "reminder_scheduler",
            "status": "ok",
            "details": f"Last tick: {last} (healthy)",
        }
    except Exception as e:
        return {
            "category": "reminder_scheduler",
            "status": "warn",
            "details": f"Could not parse last tick timestamp: {e}",
        }


def check_reminders(app: "App") -> dict[str, Any]:
    """Check for overdue reminders."""
    from mycelos.doctor.tools import doctor_check_reminders
    r = doctor_check_reminders(app)

    if r["due_count"] == 0:
        return {
            "category": "reminders",
            "status": "ok",
            "details": "No overdue reminders",
        }
    task_list = ", ".join(t["title"] for t in r["tasks"][:3])
    last = r["last_sent"]
    last_info = f", last sent: {last['created_at']}" if last else ", never sent"
    return {
        "category": "reminders",
        "status": "warning",
        "details": f"{r['due_count']} overdue: {task_list}{last_info}",
    }


def check_schedules(app: "App") -> dict[str, Any]:
    """Check for missed scheduled workflows."""
    from mycelos.doctor.tools import doctor_check_schedules
    s = doctor_check_schedules(app)

    if s["total"] == 0:
        return {
            "category": "schedules",
            "status": "ok",
            "details": "No scheduled workflows",
        }
    if s["missed"] > 0:
        missed_names = ", ".join(m["workflow_id"] for m in s["missed_details"][:3])
        return {
            "category": "schedules",
            "status": "warning",
            "details": f"{s['missed']} missed: {missed_names}. Server may have been down.",
        }
    return {
        "category": "schedules",
        "status": "ok",
        "details": f"{s['active']} active schedule(s), none missed",
    }


def check_sqlite_vec(app: "App") -> dict[str, Any]:
    """Check whether sqlite-vec extension can be loaded for vector search.

    On macOS pyenv builds, Python is often compiled without
    --enable-loadable-sqlite-extensions, which means sqlite-vec falls back
    to FTS5-only search. This check surfaces the issue and points to the
    install script.
    """
    import sqlite3
    import sys

    if not hasattr(sqlite3.Connection, "enable_load_extension"):
        platform_hint = ""
        if sys.platform == "darwin":
            platform_hint = (
                " On macOS: rebuild Python with sqlite extensions support — "
                "see scripts/install-macos-sqlite.sh"
            )
        return {
            "category": "sqlite_vec",
            "status": "warning",
            "details": (
                "Vector search disabled — Python was built without "
                "loadable SQLite extensions. Knowledge base falls back to "
                "FTS5 search (slower for large bases)." + platform_hint
            ),
        }

    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        return {
            "category": "sqlite_vec",
            "status": "warning",
            "details": "sqlite-vec package not installed. Run: pip install sqlite-vec",
        }

    # Try actually loading the extension to confirm it works
    try:
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.close()
        return {
            "category": "sqlite_vec",
            "status": "ok",
            "details": "Vector search available (sqlite-vec loaded)",
        }
    except Exception as e:
        return {
            "category": "sqlite_vec",
            "status": "warning",
            "details": f"sqlite-vec load failed: {e}",
        }


def check_organizer(app: "App") -> dict[str, Any]:
    """Check knowledge organizer queue and last run."""
    pending_row = app.storage.fetchone(
        "SELECT COUNT(*) AS c FROM knowledge_notes WHERE organizer_state='pending'"
    )
    pending = pending_row["c"] if pending_row else 0

    last_row = app.storage.fetchone(
        "SELECT MAX(created_at) AS ts FROM audit_events WHERE event_type LIKE 'organizer.%'"
    )
    last_run = last_row["ts"] if last_row and last_row["ts"] else None

    if pending < 100:
        status = "ok"
        details = f"pending: {pending}, last_run: {last_run}"
    else:
        status = "warning"
        details = f"backlog: {pending} pending, last_run: {last_run}"

    return {
        "category": "organizer",
        "status": status,
        "details": details,
        "pending": pending,
        "last_run": last_run,
    }


def run_health_checks(
    app: "App", gateway_url: str | None = "http://localhost:9100"
) -> list[dict[str, Any]]:
    """Run all health checks and return results.

    When ``gateway_url`` is None the server-reachability check is omitted
    entirely — intended for callers running *inside* the gateway process
    (e.g. the `/api/admin/doctor` endpoint) where pinging ourselves is both
    redundant and misleading.
    """
    results: list[dict[str, Any]] = []
    if gateway_url is not None:
        results.append(check_server(gateway_url))
    results.extend([
        check_storage(app),
        check_sqlite_vec(app),
        check_credentials(app),
        check_telegram(app),
        check_reminders(app),
        check_reminder_scheduler(app),
        check_schedules(app),
    ])
    return results
