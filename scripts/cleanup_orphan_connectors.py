#!/usr/bin/env python3
"""Remove orphan connector_registry rows whose id is no longer in RECIPES.

One-shot maintenance tool. Default mode is --dry-run; pass --apply to
actually delete. --apply backs up mycelos.db before writing.

Usage:
    python scripts/cleanup_orphan_connectors.py --data-dir ~/.mycelos
    python scripts/cleanup_orphan_connectors.py --data-dir ~/.mycelos --apply
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def _resolve_data_dir(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _backup_db(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    shutil.copy2(db_path, backup)
    return backup


def _find_orphans(app) -> list[dict]:
    from mycelos.connectors.mcp_recipes import RECIPES
    rows = app.connector_registry.list_connectors()
    return [r for r in rows if r["id"] not in RECIPES]


def _remove_orphan(app, orphan: dict) -> None:
    """Delete one orphan: capabilities, credentials, the row itself.
    Single transaction per orphan so partial failure is per-orphan."""
    cid = orphan["id"]
    storage = app.storage
    with storage.transaction():
        storage.execute(
            "DELETE FROM connector_capabilities WHERE connector_id = ?", (cid,)
        )
        storage.execute(
            "DELETE FROM credentials WHERE service IN (?, ?)",
            (cid, f"connector:{cid}"),
        )
        storage.execute("DELETE FROM connectors WHERE id = ?", (cid,))
    app.audit.log(
        "connector.orphan_removed",
        details={"id": cid, "name": orphan.get("name") or cid},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remove orphan connector_registry rows."
    )
    parser.add_argument(
        "--data-dir",
        default="~/.mycelos",
        help="Mycelos data directory (default: ~/.mycelos)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete (default is --dry-run, prints only).",
    )
    args = parser.parse_args(argv)

    data_dir = _resolve_data_dir(args.data_dir)
    db_path = data_dir / "mycelos.db"
    if not db_path.exists():
        print(f"error: mycelos.db not found at {db_path}", file=sys.stderr)
        return 1

    if not os.environ.get("MYCELOS_MASTER_KEY"):
        key_file = data_dir / ".master_key"
        if key_file.exists():
            os.environ["MYCELOS_MASTER_KEY"] = key_file.read_text().strip()

    from mycelos.app import App
    app = App(data_dir)

    orphans = _find_orphans(app)
    if not orphans:
        print("No orphans found. 0 orphans removed.")
        return 0

    if not args.apply:
        print("DRY RUN. Would remove:")
        for o in orphans:
            caps = len(o.get("capabilities") or [])
            print(
                f"  - {o['id']} ({o.get('name') or '?'}, "
                f"type={o.get('connector_type') or '?'}, {caps} capabilities)"
            )
        print(f"\n{len(orphans)} orphan(s) would be removed. "
              f"Re-run with --apply to delete.")
        return 0

    backup = _backup_db(db_path)
    print(f"Backup: {backup}")
    try:
        for o in orphans:
            _remove_orphan(app, o)
            caps = len(o.get("capabilities") or [])
            print(f"Removed {o['id']} ({caps} capabilities).")
    except Exception as exc:
        print(
            f"\nFAILED mid-cleanup: {exc}\n"
            f"Restore from {backup} if you want to retry from the original state.",
            file=sys.stderr,
        )
        return 2

    app.config.apply_from_state(
        state_manager=app.state_manager,
        description=f"Removed {len(orphans)} orphan connector(s)",
        trigger="orphan_cleanup",
    )

    names = ", ".join(o["id"] for o in orphans)
    print(f"\n{len(orphans)} orphan(s) removed: {names}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
