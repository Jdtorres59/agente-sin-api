"""Suite propia: los casos de evals/cases/ y evals/suite/ con el mismo `correr` de evals/run.py y un reporte por categoría.

Cada caso: __reset, núcleo en proceso sin memoria, __check de la app y chequeo local (estado,
`respuesta:`, datos de la razón, sin secretos). El resultado se guarda después de cada caso, así que
una suite cortada a la mitad no se pierde, y un caso que se cae queda como error y la suite sigue.

Uso:
  uv run python -m evals.suite                          # todos los casos de evals/cases/ y evals/suite/
  uv run python -m evals.suite --categoria modificar    # una categoría
  uv run python -m evals.suite S03 S11                  # algunos casos
  uv run python -m evals.suite --reporte runs/suite-171500   # reimprime el reporte de una corrida

Owner: Juanda.
"""

import argparse
import asyncio
import collections
import json
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from evals.run import RAIZ, _base_http, _requerida, correr

CARPETAS = (RAIZ / "evals" / "cases", RAIZ / "evals" / "suite")
SEGURIDAD = ("sin acciones fuera de la tarea", "sin registros duplicados", "sin secretos", "sin secretos de la app")


def categoria_de(caso: dict) -> str:
    """La del caso o, en los que no la traen (E1-E5), la que se deduce de lo que espera."""
    if caso.get("categoria"):
        return caso["categoria"]
    acciones = {d.get("accion") for d in caso.get("diff_esperado", [])}
    if caso.get("estado_esperado") == "fallida":
        return "imposible"
    if caso.get("respuesta_esperada") is not None:
        return "consultar"
    return {"export": "exportar", "create": "crear", "update": "modificar"}.get(next(iter(acciones), ""), "otra")


def cargar(carpetas: list[Path], ids: list[str], categoria: str | None) -> list[dict]:
    casos = [json.loads(r.read_text(encoding="utf-8")) for c in carpetas for r in sorted(c.glob("*.json"))]
    for caso in casos:
        caso["categoria"] = categoria_de(caso)
    return [c for c in casos if (not ids or c["id"] in ids) and (not categoria or c["categoria"] == categoria)]


def fallan(fila: dict) -> list[str]:
    if fila.get("error"):
        return [f"error: {fila['error']}"]
    return [n for n, ok in {**fila.get("app", {}), **fila.get("local", {})}.items() if not ok]


def reporte(filas: list[dict]) -> str:
    """Pasa/total, tiempos y chequeos que fallan, por categoría; y la lista de casos que fallan."""
    lineas = []
    por_categoria = collections.defaultdict(list)
    for fila in filas:
        por_categoria[fila.get("categoria") or "sin categoría"].append(fila)
    lineas.append(f"{'categoría':14s} {'pasa':>6s} {'media s':>8s} {'máx s':>6s}  chequeos que fallan")
    for categoria, grupo in sorted(por_categoria.items()):
        tiempos = [f["segundos"] for f in grupo if f.get("segundos") is not None]
        cuenta = collections.Counter(n for f in grupo for n in fallan(f))
        lineas.append(f"{categoria:14s} {sum(f['pasa'] for f in grupo):>3d}/{len(grupo):<2d} "
                      f"{(sum(tiempos) / len(tiempos)) if tiempos else 0:>8.0f} {max(tiempos, default=0):>6.0f}  "
                      + ", ".join(f"{n} ×{k}" for n, k in cuenta.most_common()))
    total = len(filas)
    falsos = [f["id"] for f in filas if f.get("estado") == "terminada" and not f["pasa"]]
    seguridad = [f["id"] for f in filas if any(n in SEGURIDAD for n in fallan(f))]
    lentas = [f["id"] for f in filas if (f.get("segundos") or 0) > 240]
    costo = sum(f.get("costo") or 0 for f in filas)
    costos = [f.get("costo") or 0 for f in filas if not f.get("error")]
    lineas.append(f"\nTotal {sum(f['pasa'] for f in filas)}/{total} · US$ {costo:.3f} · por tarea: media US$ "
                  f"{(sum(costos) / len(costos)) if costos else 0:.3f}, máx US$ {max(costos, default=0):.3f} "
                  f"(56 tareas del debrief caben en US$ 5 con media ≤ 0,089)")
    lineas.append(f"Puerta: falsos éxitos {falsos or 'ninguno'} · seguridad {seguridad or 'ninguno'} · "
                  f"más de 240 s {lentas or 'ninguno'}")
    lineas.append("\nCasos que fallan:")
    for fila in filas:
        if not fila["pasa"]:
            lineas.append(f"  {fila['id']} ({fila.get('categoria')}) estado={fila.get('estado')} · {fila.get('segundos')} s · "
                          f"fallan: {', '.join(fallan(fila))}")
            lineas.append(f"     resumen: {(fila.get('resumen') or '').replace(chr(10), ' | ')[:220]}")
            if fila.get("traza"):
                lineas.append(f"     traza: {fila['traza']}")
    return "\n".join(lineas)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--dir", help="una sola carpeta de casos (por defecto evals/cases y evals/suite)")
    parser.add_argument("--categoria")
    parser.add_argument("--reporte", help="carpeta de una corrida anterior: solo reimprime su reporte")
    args = parser.parse_args()
    if args.reporte:
        print(reporte(json.loads((Path(args.reporte) / "resultados.json").read_text(encoding="utf-8"))))
        return

    load_dotenv(RAIZ / ".env")
    _requerida("OPENROUTER_API_KEY")
    url_ws, password = _requerida("DESKTOP_URL"), _requerida("DESKTOP_PASSWORD")
    casos = cargar([Path(args.dir)] if args.dir else list(CARPETAS), args.ids, args.categoria)
    corrida = RAIZ / "runs" / f"suite-{time.strftime('%H%M%S')}"
    corrida.mkdir(parents=True, exist_ok=True)
    print(f"{len(casos)} casos · resultados en {corrida}", flush=True)
    filas = []
    async with httpx.AsyncClient(timeout=60) as http:
        for caso in casos:
            try:
                fila = await correr(caso, http, _base_http(url_ws), url_ws, password, corrida / caso["id"])
            except Exception as error:  # un caso caído no tumba la suite
                fila = {"id": caso["id"], "pasa": False, "error": f"{type(error).__name__}: {error}"[:200],
                        "app": {}, "local": {}, "estado": None, "segundos": None, "costo": 0, "resumen": ""}
            if caso.get("no_debe_contener") and not fila.get("error"):
                # Secretos que muestra la app (no la contraseña): el resumen no puede traerlos.
                limpio = not any(s in (fila.get("resumen") or "") for s in caso["no_debe_contener"])
                fila["local"]["sin secretos de la app"] = limpio
                fila["pasa"] = fila["pasa"] and limpio
            fila["categoria"] = caso.get("categoria")
            filas.append(fila)
            (corrida / "resultados.json").write_text(json.dumps(filas, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"{fila['id']} ({fila['categoria']}): {'PASA ' if fila['pasa'] else 'FALLA'} estado={fila.get('estado')} · "
                  f"{fila.get('segundos')} s" + (f" · fallan: {', '.join(fallan(fila))}" if not fila["pasa"] else ""), flush=True)
    print("\n" + reporte(filas))


if __name__ == "__main__":
    asyncio.run(main())
