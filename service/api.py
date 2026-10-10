"""HTTP entrypoint required by the challenge contract.

GET  /health           200 when ready
POST /tasks            {"tarea", "escritorio": {"url", "password"}} -> {"task_id"} in < 2 s
GET  /tasks/{task_id}  {"estado": "en_curso" | "terminada" | "fallida", "resumen": "..."}

Every call carries Authorization: Bearer <TEAM_TOKEN>. Owner: Juanda.
"""

import os
import secrets

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from service.runner import Runner

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
            "token_set": bool(os.environ.get("TEAM_TOKEN", "").strip())}


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
