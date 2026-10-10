"""Curva de aprendizaje: la misma clase de tarea con datos distintos, sin memoria y con memoria.

Cada tarea pasa por __reset → agente → __check. En el modo con memoria se usa una base nueva y el
agente aprende solo de las tareas que pasan __check; en las siguientes la receta hace la navegación y
el llenado sin modelo, y el operador termina con sus compuertas y su verificación.

Uso:
  uv run python -m evals.curve                          # familia crear, 4 tareas, ambos modos
  uv run python -m evals.curve --familia modificar --n 3
  uv run python -m evals.curve --modos con              # solo con memoria

Escribe runs/curve-HHMMSS/curva.json y curva.md. Owner: Juanda.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from agent.core import run_task
from agent.memory import SQLiteMemory
from evals.run import PRESUPUESTO_S, RAIZ, _base_http, _requerida, chequeo_local

# Ciudades que existen en la lista cerrada de la app de práctica (una que no existe es otra prueba:
# evals/cases/J1-ciudad-inexistente.json).
CREAR = [
    ("Registra al cliente {0}, NIT {1}, teléfono {2}, ciudad {3}.", "Papelería Los Andes", "901234567", "3157654321", "Pereira"),
    ("Crea el cliente {0} con NIT {1}, teléfono {2} y ciudad {3}.", "Ferretería El Tornillo", "900111222", "3001112233", "Medellín"),
    ("Registra al cliente {0}, NIT {1}, teléfono {2}, ciudad {3}.", "Droguería Santa Inés", "830555444", "3204445566", "Barranquilla"),
    ("Da de alta al cliente {0}: NIT {1}, teléfono {2}, ciudad {3}.", "Panadería La Espiga", "901777888", "3112223344", "Bogotá"),
    ("Registra al cliente {0}, NIT {1}, teléfono {2}, ciudad {3}.", "Textiles Río Claro", "900999000", "3185556677", "Bucaramanga"),
]
MODIFICAR = [
    "Calle 80 # 24-15", "Carrera 15 # 93-40", "Avenida 68 # 10-22", "Calle 26 # 59-41", "Transversal 5 # 7-80",
]


def familia(nombre: str, n: int) -> list[dict]:
    if nombre == "crear":
        casos = []
        for i, (plantilla, *valores) in enumerate(CREAR[:n], 1):
            campos = dict(zip(("nombre", "nit", "telefono", "ciudad"), valores))
            casos.append({
                "id": f"crear-{i}", "instruccion": plantilla.format(*valores), "estado_esperado": "terminada",
                "lista_blanca": [],
                "diff_esperado": [{"entidad": "clientes", "id": "nuevo", "campo": c, "accion": "create", "valor": v}
                                  for c, v in campos.items()],
            })
        return casos
    return [{
        "id": f"modificar-{i}",
        "instruccion": f"Corrige la dirección del cliente Distribuciones El Faro: ahora es {direccion}.",
        "estado_esperado": "terminada", "lista_blanca": [],
        "diff_esperado": [{"entidad": "clientes", "id": "CL-0007", "campo": "direccion", "accion": "update", "valor": direccion}],
    } for i, direccion in enumerate(MODIFICAR[:n], 1)]


def llamadas_del_modelo(carpeta: Path) -> int | None:
    try:
        for linea in reversed((carpeta / "pasos.jsonl").read_text(encoding="utf-8").splitlines()):
            evento = json.loads(linea)
            if evento.get("accion") == "fin":
                return evento.get("llamadas")
    except (OSError, ValueError):
        pass
    return None


async def correr(caso, memoria, http, base, url_ws, password, carpeta: Path) -> dict:
    token = {"Authorization": f"Bearer {_requerida('DESKTOP_TOKEN')}"}
    (await http.post(f"{base}/__reset", headers=token)).raise_for_status()
    await asyncio.sleep(4)
    inicio = time.monotonic()
    resultado = await run_task(caso["instruccion"], url_ws, password, inicio + PRESUPUESTO_S, memoria, carpeta)
    segundos = round(time.monotonic() - inicio, 1)
    remoto = (await http.post(f"{base}/__check", headers=token, json=caso)).json()
    pasa = bool(remoto.get("pass")) and all(chequeo_local(caso, resultado, password).values())
    fila = {"id": caso["id"], "pasa": pasa, "estado": resultado.estado, "segundos": segundos,
            "costo": resultado.cost_usd, "llamadas": llamadas_del_modelo(carpeta),
            "pasos_modelo": len(resultado.steps), "pasos_receta": 0, "receta_ok": None, "traza": str(carpeta)}
    if memoria is not None:
        # En desarrollo solo se aprende de lo que además pasó __check.
        resultado.verified = resultado.verified and pasa
        memoria.learn(resultado.app_id or "", resultado.spec, resultado)
        ultimo = memoria.episodes()[-1] if memoria.episodes() else {}
        fila["pasos_receta"] = ultimo.get("replayed_steps") or 0
        fila["receta_ok"] = ultimo.get("replay_ok")
    return fila


def tabla(filas: list[dict]) -> str:
    lineas = ["| Modo | Tarea | Pasa | Llamadas | Pasos modelo | Pasos receta | Segundos | Costo US$ |",
              "|---|---|---|---|---|---|---|---|"]
    for f in filas:
        lineas.append(f"| {f['modo']} | {f['id']} | {'sí' if f['pasa'] else 'no'} | {f['llamadas']} | {f['pasos_modelo']} "
                      f"| {f['pasos_receta']} | {f['segundos']} | {f['costo']:.4f} |")
    return "\n".join(lineas)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--familia", choices=["crear", "modificar"], default="crear")
    parser.add_argument("--n", type=int, default=4)
    parser.add_argument("--modos", nargs="+", choices=["sin", "con"], default=["sin", "con"])
    args = parser.parse_args()

    load_dotenv(RAIZ / ".env")
    _requerida("OPENROUTER_API_KEY")
    url_ws, password = _requerida("DESKTOP_URL"), _requerida("DESKTOP_PASSWORD")
    base = _base_http(url_ws)
    corrida = RAIZ / "runs" / f"curve-{time.strftime('%H%M%S')}"
    corrida.mkdir(parents=True, exist_ok=True)
    casos = familia(args.familia, args.n)
    filas = []
    async with httpx.AsyncClient(timeout=60) as http:
        for modo in args.modos:
            memoria = SQLiteMemory(corrida / "memory.db") if modo == "con" else None
            for caso in casos:
                fila = {"modo": modo, **await correr(caso, memoria, http, base, url_ws, password, corrida / f"{modo}-{caso['id']}")}
                filas.append(fila)
                print(f"{modo:3s} {fila['id']}: {'PASA ' if fila['pasa'] else 'FALLA'} · {fila['llamadas']} llamadas · "
                      f"{fila['pasos_modelo']} pasos del modelo · {fila['pasos_receta']} de receta · "
                      f"{fila['segundos']} s · US$ {fila['costo']:.4f}", flush=True)
    (corrida / "curva.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")
    (corrida / "curva.md").write_text(tabla(filas) + "\n", encoding="utf-8")
    print("\n" + tabla(filas) + f"\n\nGuardado en {corrida}")


if __name__ == "__main__":
    asyncio.run(main())
