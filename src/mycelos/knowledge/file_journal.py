"""Compensate file changes when a source import transaction fails."""
from contextlib import contextmanager
from contextvars import ContextVar

_files = ContextVar('source_article_files', default=None)


@contextmanager
def file_changes():
    """Undo source article file writes when the surrounding SQL transaction fails."""
    journal = {}
    token = _files.set(journal)
    try:
        yield
    except BaseException:
        for path, content in reversed(list(journal.items())):
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    finally:
        _files.reset(token)


def record_file(path):
    journal = _files.get()
    if journal is not None:
        journal.setdefault(path, path.read_bytes() if path.exists() else None)

