"""HTTP entrypoint required by the challenge contract.

GET  /health           200 when ready
POST /tasks            {"tarea", "escritorio": {"url", "password"}} -> {"task_id"} in < 2 s
GET  /tasks/{task_id}  {"estado": "en_curso" | "terminada" | "fallida", "resumen": "..."}

Debugging (same token): GET /traces lists the traces kept on the volume, GET /traces/{id} returns
one trace's events and GET /traces/{id}/{paso-NN.png} one of its screenshots. They let us read what
happened in a task, including hidden-set runs, without shell access to the deploy.

Every call carries Authorization: Bearer <TEAM_TOKEN>. Owner: Juanda.
"""

import json
import os
import re
import secrets

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from service.runner import TRACE_ROOT, Runner, scrub

load_dotenv()

app = FastAPI(title="agente-sin-api")
runner = Runner()


def require_token(authorization: str = Header(default="")) -> None:
    # Tolerate stray spaces or quotes pasted into the deploy variables.
    expected = os.environ.get("TEAM_TOKEN", "").strip().strip("\"'")
    given = authorization.removeprefix("Bearer ").strip()
    if not expected or not secrets.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="invalid token")


class Escritorio(BaseModel):
    url: str
    password: str


class TaskIn(BaseModel):
    tarea: str
    escritorio: Escritorio


@app.get("/health")
async def health() -> dict:
    # Deploy diagnostics only: which commit runs and whether the token is configured (never its value).
    return {"ok": True, "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "local")[:7],
            "token_set": bool(os.environ.get("TEAM_TOKEN", "").strip()),
            "memory": getattr(runner.memory, "path", None) is not None}


@app.post("/tasks", dependencies=[Depends(require_token)])
async def create_task(body: TaskIn) -> dict:
    task_id = runner.submit(body.tarea, body.escritorio.url, body.escritorio.password)
    return {"task_id": task_id}


@app.get("/tasks/{task_id}", dependencies=[Depends(require_token)])
async def get_task(task_id: str) -> dict:
    task = runner.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    return {"estado": task.estado, "resumen": task.resumen}


_TRACE_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_CAPTURE = re.compile(r"^paso-\d{1,4}\.png$")


def _env_secrets() -> list[str]:
    return [os.environ.get(k, "") for k in ("TEAM_TOKEN", "OPENROUTER_API_KEY", "DESKTOP_TOKEN", "DESKTOP_PASSWORD")]


def _events(trace_id: str) -> list[dict]:
    if not _TRACE_ID.match(trace_id):
        raise HTTPException(status_code=404, detail="unknown trace")
    path = TRACE_ROOT / trace_id / "pasos.jsonl"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="unknown trace")
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(scrub(line, _env_secrets())))
        except ValueError:
            continue
    return events


@app.get("/traces", dependencies=[Depends(require_token)])
async def list_traces() -> list[dict]:
    rows = []
    dirs = [d for d in TRACE_ROOT.iterdir() if d.is_dir()] if TRACE_ROOT.is_dir() else []
    for d in sorted(dirs, key=lambda d: d.stat().st_mtime, reverse=True)[:60]:
        try:
            events = _events(d.name)
        except HTTPException:
            continue
        start = next((e for e in events if e.get("accion") == "inicio"), {})
        end = next((e for e in reversed(events) if e.get("accion") == "fin"), {})
        rows.append({"id": d.name, "modified": int(d.stat().st_mtime), "tarea": start.get("tarea"),
                     "estado": end.get("estado"), "segundos": end.get("t"), "costo": end.get("costo"),
                     "llamadas": end.get("llamadas"), "pasos": end.get("pasos")})
    return rows


@app.get("/traces/{trace_id}", dependencies=[Depends(require_token)])
async def get_trace(trace_id: str) -> dict:
    return {"id": trace_id, "events": _events(trace_id)}


@app.get("/traces/{trace_id}/{capture}", dependencies=[Depends(require_token)])
async def get_capture(trace_id: str, capture: str) -> FileResponse:
    path = TRACE_ROOT / trace_id / capture
    if not _TRACE_ID.match(trace_id) or not _CAPTURE.match(capture) or not path.is_file():
        raise HTTPException(status_code=404, detail="unknown capture")
    return FileResponse(path, media_type="image/png")
