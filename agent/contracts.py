"""Contracts between the agent core (Juanes) and the services around it (Juanda).

Frozen at 12:30. Changing anything here needs agreement from both owners, because
`service/`, `agent/memory/` and the core all depend on these shapes.

Owner: Juanda.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from PIL import Image


class Driver(Protocol):
    """Screen I/O over VNC. Implemented by the core (Juanes)."""

    async def connect(self, url: str, password: str) -> None: ...
    async def screenshot(self) -> Image.Image: ...
    async def click(self, x: int, y: int, double: bool = False) -> None: ...
    async def type_text(self, text: str) -> None: ...
    async def key(self, combo: str) -> None: ...
    async def scroll(self, x: int, y: int, direction: str, clicks: int = 3) -> None: ...
    async def settle(self) -> bool: ...
    async def close(self) -> None: ...


@dataclass
class Step:
    """One executed action. The operator appends one per action to TaskResult.steps."""

    kind: str                        # click | double_click | type | key | scroll
    x: int | None = None
    y: int | None = None
    text: str | None = None          # literal text typed
    slot: str | None = None          # TaskSpec field the text came from, to parametrize recipes
    key: str | None = None           # key combo, e.g. "ctrl+a"
    direction: str | None = None     # scroll direction
    anchor_png: bytes | None = None  # 48x48 PNG crop around (x, y), taken BEFORE acting
    screen_changed_px: int = 0       # changed pixels after settle()
    model: str | None = None         # model that chose the action; None if replayed from a recipe


@dataclass
class TaskResult:
    """What run_task returns. The service stores estado + resumen for GET /tasks/{id}."""

    estado: str                      # terminada | fallida
    resumen: str                     # already formatted and scrubbed of secrets
    steps: list[Step] = field(default_factory=list)
    cost_usd: float = 0.0
    verified: bool = False           # clean-context verification confirmed the outcome
    spec: dict = field(default_factory=dict)  # the TaskSpec used (tipo, referente, valores, ...)
    app_id: str | None = None        # Memory.fingerprint of the app, if computed


@dataclass
class Recipe:
    """A verified sequence of steps for a task type in one app. Owned by agent/memory."""

    app_id: str
    task_type: str
    version: int
    steps: list[Step]
    successes: int = 0
    failures: int = 0


@dataclass
class ReplayResult:
    ok: bool                         # every step matched its anchor and changed the screen as expected
    steps_done: int                  # how many steps were replayed before stopping
    failed_at: int | None = None     # index of the first step that did not match, if any
    reason: str = ""


class Memory(Protocol):
    """Learning memory per app. Implemented by agent/memory (Juanda)."""

    def fingerprint(self, img: Image.Image) -> str: ...
    def find_recipe(self, app_id: str, spec: dict) -> Recipe | None: ...
    async def replay(self, recipe: Recipe, spec: dict, driver: Driver) -> ReplayResult: ...
    def learn(self, app_id: str, spec: dict, result: TaskResult) -> None: ...
    def notes(self, app_id: str) -> str: ...


class RunTask(Protocol):
    """Signature of the core entry point the service calls (Juanes implements it)."""

    async def __call__(self, tarea: str, url: str, password: str, deadline: float,
                       memory: Memory | None, trace_dir: Path) -> TaskResult: ...
