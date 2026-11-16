"""Reuse parsed publication ledgers only while one site build is running.

Only JSON parsing is reused: publication decisions and media-byte checks remain
live. A changed file identity invalidates the parsed input even within a build.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path


_inputs = ContextVar("publication_json_inputs", default=None)


@contextmanager
def publication_json_snapshot():
    """Start fresh for every build; restore the caller even after a failure."""
    token = _inputs.set({})
    try:
        yield
    finally:
        _inputs.reset(token)


def _identity(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


def read_publication_json(path):
    """Read live outside a build; callers must not mutate a shared build input."""
    inputs = _inputs.get()
    # Remote git snapshots implement read_text without filesystem identity.
    # Preserve that reader contract and keep virtual inputs live in a build too.
    if inputs is None or not isinstance(path, Path):
        return json.loads(path.read_text(encoding="utf-8"))
    path = path.absolute()
    identity = _identity(path)
    cached = inputs.get(path)
    if cached is not None and cached[0] == identity:
        return cached[1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _identity(path) != identity:
        raise ValueError("Publication input changed while reading: " + str(path))
    inputs[path] = (identity, payload)
    return payload
