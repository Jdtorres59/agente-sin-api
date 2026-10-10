"""Learning memory (implements `agent.contracts.Memory`). Owner: Juanda.

`get_memory()` returns one shared SQLiteMemory per process, stored at $MEMORY_DB
(default /data/memory.db on the deploy volume, runs/memory.db locally). It is off unless
MEMORY=on, so turning it on in production is an explicit decision; without it the core runs
exactly as before. Evals that measure memory build `SQLiteMemory(path)` directly.
"""

import os

from .store import SQLiteMemory, default_path

_memory: SQLiteMemory | None = None


def get_memory() -> SQLiteMemory | None:
    global _memory
    if os.environ.get("MEMORY", "off").strip().lower() not in ("on", "1", "true", "yes"):
        return None
    if _memory is None:
        _memory = SQLiteMemory(os.environ.get("MEMORY_DB") or default_path())
    return _memory


__all__ = ["SQLiteMemory", "get_memory"]
