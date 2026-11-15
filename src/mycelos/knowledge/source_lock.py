"""Process-owned source lock. The operating system releases it after a crash."""
from contextlib import contextmanager
import os
from mycelos.i18n import t as _t


@contextmanager
def source_lock(data_dir, source_id):
    directory = data_dir / "source-locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{source_id}.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as error:
            raise ValueError(_t("sources.already_running")) from error
        try:
            yield
        finally:
            handle.seek(0)
            release()
