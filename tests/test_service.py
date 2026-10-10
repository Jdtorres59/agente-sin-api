"""Contract tests for the HTTP service with a fake core. Run: uv run python -m pytest tests -q"""

import asyncio
import time

import httpx
import pytest
import uvicorn

import service.runner as runner_mod
from agent.contracts import TaskResult
from service import api

TOKEN = "test-team-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"tarea": "Registra al cliente X", "escritorio": {"url": "wss://x/websockify", "password": "s3cretpw"}}


async def fake_core(tarea, url, password, deadline, memory, trace_dir):
    await asyncio.sleep(0.3)
    return TaskResult(estado="terminada", resumen=f"Hecho. respuesta: 45 (clave {password})")


async def slow_core(tarea, url, password, deadline, memory, trace_dir):
    await asyncio.sleep(60)


async def _serve_and(fn, monkeypatch, core):
    monkeypatch.setenv("TEAM_TOKEN", TOKEN)
    api.runner.run_task = core
    api.runner.memory = None
    server = uvicorn.Server(uvicorn.Config(api.app, port=8765, log_level="warning"))
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8765") as c:
            await fn(c)
    finally:
        server.should_exit = True
        await serve


def test_contract(monkeypatch):
    async def check(c):
        assert (await c.get("/health")).status_code == 200
        assert (await c.post("/tasks", json=BODY)).status_code == 401
        assert (await c.post("/tasks", json=BODY, headers={"Authorization": "Bearer wrong"})).status_code == 401
        t = time.monotonic()
        r = await c.post("/tasks", json=BODY, headers=AUTH)
        assert r.status_code == 200 and time.monotonic() - t < 2
        tid = r.json()["task_id"]
        assert (await c.get(f"/tasks/{tid}", headers=AUTH)).json()["estado"] == "en_curso"
        await asyncio.sleep(0.6)
        done = (await c.get(f"/tasks/{tid}", headers=AUTH)).json()
        assert done["estado"] == "terminada"
        assert "s3cretpw" not in done["resumen"] and done["resumen"].startswith("Hecho. respuesta: 45")
        assert (await c.get("/tasks/nope", headers=AUTH)).status_code == 404
    asyncio.run(_serve_and(check, monkeypatch, fake_core))


def test_hard_time_limit(monkeypatch):
    monkeypatch.setattr(runner_mod, "HARD_LIMIT_S", 1)
    async def check(c):
        tid = (await c.post("/tasks", json=BODY, headers=AUTH)).json()["task_id"]
        await asyncio.sleep(1.4)
        assert (await c.get(f"/tasks/{tid}", headers=AUTH)).json()["estado"] == "fallida"
    asyncio.run(_serve_and(check, monkeypatch, slow_core))


def test_missing_core_reports_fallida(monkeypatch):
    async def check(c):
        tid = (await c.post("/tasks", json=BODY, headers=AUTH)).json()["task_id"]
        await asyncio.sleep(0.3)
        assert (await c.get(f"/tasks/{tid}", headers=AUTH)).json()["estado"] == "fallida"
    asyncio.run(_serve_and(check, monkeypatch, None))


def test_trace_errors_are_read_for_the_deploy_logs(tmp_path):
    from service.runner import _trace_errors
    (tmp_path / "pasos.jsonl").write_text(
        '{"accion": "inicio"}\n{"accion": "error", "tipo": "ErrorModelo", "detalle": "OpenRouter respondió 400"}\n',
        encoding="utf-8")
    assert _trace_errors(tmp_path) == [{"accion": "error", "tipo": "ErrorModelo", "detalle": "OpenRouter respondió 400"}]
    assert _trace_errors(tmp_path / "missing") == []


def test_scrub_hides_credentials_read_on_screen():
    from service.runner import scrub
    out = scrub("Encontré la clave de integración: K9x2-PLM44q-zz81. respuesta: K9x2-PLM44q-zz81", [])
    assert "K9x2" not in out and "clave de integración: [oculto]" in out
    assert "PLM44q" not in scrub("La contraseña es Hunter2024 según la ficha.", [])
    assert "[oculto]" in scrub("token=abcdef1234567890ABCDEF12", [])
    # Ordinary data stays: codes, NIT, phones, money, dates.
    normal = "Cliente CL-0141, NIT 901234567, teléfono 3157654321, factura FV-10159 por 1.810.000 el 14/10/2026. respuesta: 45"
    assert scrub(normal, []) == normal
    assert scrub("La clave del producto es AB-778.", []) == "La clave del producto es AB-778."
