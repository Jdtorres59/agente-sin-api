"""Harness principal: __reset → agente → __check, más chequeo local de estado y resumen.

Uso:
  uv run python -m evals.run                 # todos los casos de evals/cases/
  uv run python -m evals.run E1 E3 --reps 2  # algunos, con repeticiones
  uv run python -m evals.run --dir evals/cases

Lee DESKTOP_URL, DESKTOP_PASSWORD y DESKTOP_TOKEN de .env. Llama al núcleo en proceso
(`agent.core.run_task`), sin pasar por el servicio HTTP.
"""

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from agent.contracts import TaskResult
from agent.core import run_task

RAIZ = Path(__file__).resolve().parents[1]
PRESUPUESTO_S = 270


def _requerida(nombre: str) -> str:
    valor = os.environ.get(nombre, "")
    if not valor:
        raise SystemExit(f"Falta la variable {nombre}. Defínela en .env o en el entorno.")
    return valor


def _base_http(url_ws: str) -> str:
    return re.sub(r"^ws", "http", url_ws).removesuffix("/websockify")


def chequeo_local(caso: dict, resultado: TaskResult, password: str) -> dict:
    """Lo que __check no mira: estado reportado, respuesta, claves de la razón y secretos."""
    resumen = resultado.resumen
    chequeos = {"estado": resultado.estado == caso["estado_esperado"]}
    linea = re.search(r"(?im)^respuesta:\s*(.*)$", resumen)
    esperada = caso.get("respuesta_esperada")
    if esperada is not None:
        if linea is None:
            chequeos["respuesta"] = False
        elif re.fullmatch(r"-?\d+([.,]\d+)?", str(esperada)):
            primero = re.search(r"-?\d+(?:[.,]\d+)?", linea.group(1))
            chequeos["respuesta"] = bool(primero) and primero.group(0) == str(esperada)
        else:
            chequeos["respuesta"] = str(esperada).lower() in linea.group(1).lower()
    elif caso["estado_esperado"] == "fallida":
        chequeos["sin línea respuesta"] = linea is None
    if caso.get("claves_razon"):
        chequeos["claves de la razón"] = all(clave in resumen for clave in caso["claves_razon"])
    chequeos["sin secretos"] = password not in resumen
    return chequeos


def _llamadas(carpeta: Path) -> int | None:
    """Llamadas al modelo de la tarea, leídas del evento final de su traza."""
    try:
        ultima = (carpeta / "pasos.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
        return json.loads(ultima).get("llamadas")
    except (OSError, IndexError, json.JSONDecodeError):
        return None


async def correr(caso: dict, http: httpx.AsyncClient, base: str, url_ws: str, password: str, carpeta: Path) -> dict:
    token = {"Authorization": f"Bearer {_requerida('DESKTOP_TOKEN')}"}
    (await http.post(f"{base}/__reset", headers=token)).raise_for_status()
    await asyncio.sleep(4)  # el navegador del escritorio se reinicia
    inicio = time.monotonic()
    resultado = await run_task(caso["instruccion"], url_ws, password, inicio + PRESUPUESTO_S, None, carpeta)
    segundos = round(time.monotonic() - inicio, 1)
    remoto = (await http.post(f"{base}/__check", headers=token, json=caso)).json()
    local = chequeo_local(caso, resultado, password)
    pasa = bool(remoto.get("pass")) and all(local.values())
    return {
        "id": caso["id"],
        "pasa": pasa,
        "llamadas": _llamadas(carpeta),
        "app": {c["nombre"]: c["ok"] for c in remoto.get("checks", [])},
        "local": local,
        "estado": resultado.estado,
        "resumen": resultado.resumen,
        "verificado": resultado.verified,
        "segundos": segundos,
        "acciones": len(resultado.steps),
        "costo": resultado.cost_usd,
        "traza": str(carpeta),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--dir", default=str(RAIZ / "evals" / "cases"))
    parser.add_argument("--reps", type=int, default=1)
    args = parser.parse_args()

    load_dotenv(RAIZ / ".env")
    _requerida("OPENROUTER_API_KEY")
    url_ws, password = _requerida("DESKTOP_URL"), _requerida("DESKTOP_PASSWORD")
    base = _base_http(url_ws)
    casos = [json.loads(ruta.read_text(encoding="utf-8")) for ruta in sorted(Path(args.dir).glob("*.json"))]
    if args.ids:
        casos = [c for c in casos if c["id"] in args.ids]

    corrida = RAIZ / "runs" / f"evals-{time.strftime('%H%M%S')}"
    filas = []
    async with httpx.AsyncClient(timeout=60) as http:
        for caso in casos:
            for rep in range(args.reps):
                fila = await correr(caso, http, base, url_ws, password, corrida / f"{caso['id']}-{rep + 1}")
                filas.append(fila)
                fallas = [n for n, ok in {**fila["app"], **fila["local"]}.items() if not ok]
                print(
                    f"{fila['id']} rep {rep + 1}: {'PASA ' if fila['pasa'] else 'FALLA'} "
                    f"estado={fila['estado']} · {fila['segundos']} s · {fila['acciones']} acciones · "
                    f"{fila['llamadas']} llamadas · US$ {fila['costo']:.4f}"
                    + (f" · fallan: {', '.join(fallas)}" if fallas else "")
                )
                print("   resumen: " + fila["resumen"].replace("\n", " | "))
                print(f"   traza: {fila['traza']}")

    if filas:
        print(
            f"\nTotal: {sum(f['pasa'] for f in filas)}/{len(filas)} · "
            f"costo US$ {sum(f['costo'] for f in filas):.4f} · "
            f"tiempo medio {sum(f['segundos'] for f in filas) / len(filas):.0f} s"
        )
        corrida.mkdir(parents=True, exist_ok=True)
        (corrida / "resultados.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
