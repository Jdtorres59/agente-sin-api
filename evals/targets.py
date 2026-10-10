"""Harness por HTTP: corre los casos contra el entrypoint desplegado, como lo hará el evaluador.

POST /tasks con la tarea y nuestro escritorio, GET /tasks/{id} cada 5 s hasta `terminada` o
`fallida` (máximo 5 minutos) y después __check. Mide lo que el harness en proceso no ve: el
deploy, el token, la latencia del POST y que el estado final llegue a tiempo.

Uso:
  uv run python -m evals.targets https://agente-sin-api-production.up.railway.app E2 E3
  uv run python -m evals.targets http://localhost:8000              # todos los casos

Owner: Juanda.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from evals.run import RAIZ, _base_http, _requerida, chequeo_local

LIMITE_S = 300
SONDEO_S = 5


class _Resultado:  # lo mínimo que chequeo_local necesita
    def __init__(self, estado: str, resumen: str):
        self.estado, self.resumen = estado, resumen


async def _con_reintentos(pedido, intentos: int = 3) -> dict:
    """El escritorio a veces corta la conexión: se reintenta antes de dar el caso por perdido."""
    for intento in range(intentos):
        try:
            return (await pedido()).json()
        except httpx.HTTPError:
            if intento == intentos - 1:
                raise
            await asyncio.sleep(3)
    return {}


async def correr(caso: dict, http: httpx.AsyncClient, objetivo: str, url_ws: str, password: str) -> dict:
    escritorio = {"Authorization": f"Bearer {_requerida('DESKTOP_TOKEN')}"}
    equipo = {"Authorization": f"Bearer {_requerida('TEAM_TOKEN')}"}
    (await http.post(f"{_base_http(url_ws)}/__reset", headers=escritorio)).raise_for_status()
    await asyncio.sleep(4)
    inicio = time.monotonic()
    respuesta = await http.post(f"{objetivo}/tasks", headers=equipo,
                                json={"tarea": caso["instruccion"], "escritorio": {"url": url_ws, "password": password}})
    post_s = round(time.monotonic() - inicio, 2)
    respuesta.raise_for_status()
    task_id = respuesta.json()["task_id"]
    estado = {"estado": "en_curso", "resumen": ""}
    while time.monotonic() - inicio < LIMITE_S:
        await asyncio.sleep(SONDEO_S)
        estado = (await http.get(f"{objetivo}/tasks/{task_id}", headers=equipo)).json()
        if estado.get("estado") in ("terminada", "fallida"):
            break
    segundos = round(time.monotonic() - inicio, 1)
    print(f"   {caso['id']} → {estado.get('estado')} en {segundos} s; pido __check", flush=True)
    await asyncio.sleep(3)  # que el escritorio termine de asentar lo último que hizo el agente
    remoto = await _con_reintentos(lambda: http.post(f"{_base_http(url_ws)}/__check", headers=escritorio, json=caso))
    local = chequeo_local(caso, _Resultado(estado.get("estado", ""), estado.get("resumen", "")), password)
    local["POST < 2 s"] = post_s < 2
    return {"id": caso["id"], "pasa": bool(remoto.get("pass")) and all(local.values()), "estado": estado.get("estado"),
            "resumen": estado.get("resumen", ""), "segundos": segundos, "post_s": post_s,
            "app": {c["nombre"]: c["ok"] for c in remoto.get("checks", [])}, "local": local}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("objetivo", help="URL base del entrypoint")
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--dir", default=str(RAIZ / "evals" / "cases"))
    args = parser.parse_args()
    load_dotenv(RAIZ / ".env")
    url_ws, password = _requerida("DESKTOP_URL"), _requerida("DESKTOP_PASSWORD")
    casos = [json.loads(r.read_text(encoding="utf-8")) for r in sorted(Path(args.dir).glob("*.json"))]
    casos = [c for c in casos if not args.ids or c["id"] in args.ids]
    filas = []
    async with httpx.AsyncClient(timeout=90) as http:
        salud = (await http.get(f"{args.objetivo.rstrip('/')}/health")).json()
        print(f"/health: {salud}")
        for caso in casos:
            try:
                fila = await correr(caso, http, args.objetivo.rstrip("/"), url_ws, password)
            except httpx.HTTPError as error:
                print(f"{caso['id']}: ERROR de red ({type(error).__name__}); sigo con el siguiente", flush=True)
                continue
            filas.append(fila)
            fallan = [n for n, ok in {**fila["app"], **fila["local"]}.items() if not ok]
            print(f"{fila['id']}: {'PASA ' if fila['pasa'] else 'FALLA'} estado={fila['estado']} · {fila['segundos']} s · "
                  f"POST {fila['post_s']} s" + (f" · fallan: {', '.join(fallan)}" if fallan else ""), flush=True)
            print("   resumen: " + fila["resumen"].replace("\n", " | "))
    if filas:
        print(f"\nTotal: {sum(f['pasa'] for f in filas)}/{len(filas)}")
        carpeta = RAIZ / "runs" / f"targets-{time.strftime('%H%M%S')}"
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "resultados.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
