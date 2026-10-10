"""Runs each task in the background and keeps its state for GET /tasks/{id}.

The core (agent.core.run_task) does the work. This module only guarantees the contract:
answer fast, never exceed the time budget, never leak secrets, and feed the memory with
what was verified. Owner: Juanda.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from agent.contracts import TaskResult

log = logging.getLogger("runner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# The grader stops polling at 300 s and resets the desktop. The core gets 270 s; the
# runner cuts at 285 s no matter what, so a result is always reported before 300 s.
CORE_BUDGET_S = 270
HARD_LIMIT_S = 285
TRACE_ROOT = Path(os.environ.get("TRACE_DIR", "runs"))


@dataclass
class TaskState:
    task_id: str
    estado: str = "en_curso"
    resumen: str = ""
    started: float = 0.0


# Secrets the agent did not receive but may read on screen (hidden set 15:30: a task made it copy an
# integration key into the summary). Code guarantees they never leave, whatever the model decided.
_SECRET_AFTER_KEYWORD = re.compile(
    # «clave» alone can mean an ordinary code in Spanish apps, so it needs a word that makes it a credential.
    r"(?i)\b(clave\s+(?:de\s+)?(?:integraci[oó]n|acceso|api|secreta|privada|usuario|administra\w*)"
    r"|contrase[ñn]a|password|passwd|token|api[ _-]?key|secreto|secret|pin)\b"
    r"([^:=\n]{0,40}?)(\s*[:=]\s*|\s+(?:es|era)\s+)[\"'«“]?([^\s\"'»”,;]{4,})"
)
_LONG_TOKEN = re.compile(r"\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{20,}\b")


def scrub(text: str, secrets: list[str]) -> str:
    """Remove every secret from text: the ones we received and anything that looks like a credential."""
    for s in secrets:
        if s and len(s) >= 4:
            text = text.replace(s, "[oculto]")
    found = [m.group(4).rstrip(".") for m in _SECRET_AFTER_KEYWORD.finditer(text)]
    for value in found:  # every occurrence, also the one in the `respuesta:` line
        if len(value) >= 4:
            text = text.replace(value, "[oculto]")
    return _LONG_TOKEN.sub("[oculto]", text)


def _load_core():
    try:
        from agent.core import run_task
        return run_task
    except ImportError as e:
        log.warning("agent.core.run_task not available yet: %s", e)
        return None


def _load_memory():
    try:
        from agent.memory import get_memory
        return get_memory()
    except ImportError:
        return None
    except Exception as e:  # a broken memory must never break task execution
        log.error("memory disabled: %s", e)
        return None


def _trace_errors(trace_dir: Path) -> list[dict]:
    try:
        lines = (trace_dir / "pasos.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    errors = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("accion") in ("error", "memoria_error"):
            errors.append({k: event.get(k) for k in ("accion", "tipo", "detalle") if event.get(k) is not None})
    return errors[-3:]


class Runner:
    def __init__(self):
        self.tasks: dict[str, TaskState] = {}
        self._jobs: dict[str, asyncio.Task] = {}
        self.run_task = _load_core()
        self.memory = _load_memory()

    def submit(self, tarea: str, url: str, password: str) -> str:
        task_id = uuid.uuid4().hex[:12]
        self.tasks[task_id] = TaskState(task_id, started=time.monotonic())
        self._jobs[task_id] = asyncio.create_task(self._run(task_id, tarea, url, password))
        return task_id

    def get(self, task_id: str) -> TaskState | None:
        return self.tasks.get(task_id)

    async def _run(self, task_id: str, tarea: str, url: str, password: str) -> None:
        state = self.tasks[task_id]
        secrets = [password] + [os.environ.get(k, "") for k in
                                ("TEAM_TOKEN", "OPENROUTER_API_KEY", "DESKTOP_TOKEN", "DESKTOP_PASSWORD")]
        trace_dir = TRACE_ROOT / task_id
        trace_dir.mkdir(parents=True, exist_ok=True)
        log.info(json.dumps({"event": "task_start", "task_id": task_id, "tarea": scrub(tarea, secrets)}))

        result: TaskResult | None = None
        try:
            if self.run_task is None:
                raise RuntimeError("core not deployed")
            deadline = state.started + CORE_BUDGET_S
            result = await asyncio.wait_for(
                self.run_task(tarea, url, password, deadline, self.memory, trace_dir),
                timeout=HARD_LIMIT_S - (time.monotonic() - state.started),
            )
            state.estado = result.estado if result.estado in ("terminada", "fallida") else "fallida"
            state.resumen = scrub(result.resumen, secrets)
            if state.resumen != scrub(result.resumen, []) or state.resumen != result.resumen:
                log.warning(json.dumps({"event": "secret_redacted", "task_id": task_id}))
        except asyncio.TimeoutError:
            state.estado, state.resumen = "fallida", "No alcancé a completar la tarea dentro del tiempo límite."
            log.error(json.dumps({"event": "task_timeout", "task_id": task_id}))
        except Exception as e:
            state.estado, state.resumen = "fallida", "La tarea no se pudo completar por un error interno del agente."
            log.exception(json.dumps({"event": "task_error", "task_id": task_id, "error": scrub(str(e), secrets)}))

        elapsed = round(time.monotonic() - state.started, 1)
        if state.estado == "fallida":
            # The core keeps error details only in its trace (on the volume); surface them in the deploy logs.
            for detail in _trace_errors(trace_dir):
                log.error(json.dumps({"event": "task_core_error", "task_id": task_id, **{
                    k: scrub(str(v), secrets)[:400] for k, v in detail.items()}}, ensure_ascii=False))
        log.info(json.dumps({"event": "task_end", "task_id": task_id, "estado": state.estado,
                             "elapsed_s": elapsed, "cost_usd": getattr(result, "cost_usd", None),
                             "steps": len(getattr(result, "steps", []) or [])}))
        if result is not None and self.memory is not None:
            try:
                self.memory.learn(result.app_id or "", result.spec, result)
            except Exception as e:
                log.error(json.dumps({"event": "memory_learn_error", "task_id": task_id, "error": str(e)}))
