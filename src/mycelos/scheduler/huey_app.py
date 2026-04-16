"""Huey task queue backed by SQLite — no external services needed.

Creates the Huey instance and provides utilities for starting
the consumer as a background thread in the Gateway process.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from huey import SqliteHuey


def create_huey(data_dir: Path) -> SqliteHuey:
    """Create the Huey task queue backed by SQLite.

    Args:
        data_dir: Mycelos data directory (e.g., ~/.mycelos).

    Returns:
        Configured SqliteHuey instance.
    """
    return SqliteHuey(
        name="mycelos",
        filename=str(data_dir / "huey.db"),
    )


def start_consumer_thread(
    huey: SqliteHuey,
    workers: int = 2,
    periodic: bool = True,
) -> threading.Thread:
    """Start the Huey consumer as a daemon thread.

    The consumer processes queued tasks and runs periodic (cron) tasks.
    Runs as a daemon thread so it shuts down with the main process.

    Args:
        huey: The Huey instance.
        workers: Number of worker threads.
        periodic: Whether to run periodic tasks.

    Returns:
        The started daemon thread.
    """
    from huey.consumer import Consumer

    consumer = Consumer(
        huey,
        workers=workers,
        periodic=periodic,
        initial_delay=0.1,
        max_delay=10.0,
    )

    # Monkey-patch signal handlers — they don't work in threads
    # but the consumer tries to set them. Silently skip.
    consumer._set_signal_handlers = lambda: None

    thread = threading.Thread(
        target=consumer.run,
        name="huey-consumer",
        daemon=True,
    )
    thread.start()
    return thread
