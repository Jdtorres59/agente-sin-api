"""Resumen de las trazas de producción: en qué se le fue el tiempo al agente en cada tarea.

Lee GET /traces del entrypoint (con TEAM_TOKEN) y, por cada tarea, cuenta turnos, segundos por turno,
acciones por herramienta, revisiones de la compuerta (aprobadas y rechazadas), rechazos del verificador
y consejos del supervisor. Guarda todo en runs/ (que no se sube: las trazas del set oculto no van al
repo público).

Uso:
  uv run python -m evals.traces                                   # producción, últimas 20 tareas
  uv run python -m evals.traces --desde 15:28 --hasta 16:15       # una corrida del set oculto
  uv run python -m evals.traces --base http://localhost:8000 --detalle <id>

Owner: Juanda.
"""

import argparse
import collections
import json
import os
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from evals.run import RAIZ

PRODUCCION = "https://agente-sin-api-production.up.railway.app"
ACCIONES = ("clic", "rellenar", "escribir", "tecla", "rueda", "leer_pantalla", "leer_campo", "leer_lista",
            "leer_opciones", "contar")


def resumir(eventos: list[dict]) -> dict:
    """Números de una tarea a partir de sus eventos."""
    cuenta = collections.Counter(e.get("accion") for e in eventos)
    turnos = max((e.get("paso") or 0 for e in eventos if isinstance(e.get("paso"), int) and e["paso"] < 900), default=0)
    fin = next((e for e in reversed(eventos) if e.get("accion") == "fin"), {})
    inicio = next((e for e in eventos if e.get("accion") == "inicio"), {})
    spec = next((e.get("spec") for e in eventos if e.get("accion") == "spec"), {}) or {}
    supervisor = [e for e in eventos if e.get("accion") == "supervisor"]
    revisiones = [e for e in supervisor if "ACCIÓN PROPUESTA" in str(e.get("pregunta", ""))]
    rechazos_compuerta = [e for e in revisiones if '"aprobar": false' in str(e.get("respuesta", "")).replace(" ", " ")]
    verificaciones = [e for e in eventos if e.get("accion") == "verificar"]
    segundos = fin.get("t") or (eventos[-1].get("t") if eventos else 0) or 0
    return {
        "tarea": inicio.get("tarea", ""),
        "tipo": spec.get("tipo"), "riesgos": spec.get("riesgos"),
        "estado": fin.get("estado"), "verificado": fin.get("verificado"),
        "segundos": segundos, "turnos": turnos, "seg_por_turno": round(segundos / turnos, 1) if turnos else None,
        "llamadas": fin.get("llamadas"), "costo": fin.get("costo"),
        "acciones": {a: cuenta[a] for a in ACCIONES if cuenta[a]},
        "comprobaciones_punto": cuenta["punto"],
        "revisiones_compuerta": len(revisiones), "rechazos_compuerta": len(rechazos_compuerta),
        "consejos_supervisor": len(supervisor) - len(revisiones) - len(verificaciones),
        "verificador_no_conforme": [e.get("problema", "")[:200] for e in verificaciones if not e.get("conforme")],
        "receta": next(({"ok": e.get("ok"), "pasos": e.get("pasos")} for e in eventos if e.get("accion") == "receta"), None),
        "errores": [f"{e.get('tipo')}: {str(e.get('detalle', ''))[:120]}" for e in eventos if e.get("accion") == "error"],
        "resumen": (fin.get("resumen") or "")[:300],
    }


def _hora(texto: str | None) -> float | None:
    if not texto:
        return None
    hoy = datetime.now().strftime("%Y-%m-%d")
    return datetime.strptime(f"{hoy} {texto}", "%Y-%m-%d %H:%M").timestamp()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=PRODUCCION)
    parser.add_argument("--desde", help="HH:MM, hora local de la última escritura de la traza")
    parser.add_argument("--hasta", help="HH:MM")
    parser.add_argument("--limite", type=int, default=20)
    parser.add_argument("--detalle", help="id de una traza: imprime su línea de tiempo")
    args = parser.parse_args()
    load_dotenv(RAIZ / ".env")
    token = {"Authorization": f"Bearer {os.environ['TEAM_TOKEN']}"}
    with httpx.Client(timeout=60) as http:
        if args.detalle:
            eventos = http.get(f"{args.base}/traces/{args.detalle}", headers=token).raise_for_status().json()["events"]
            previo = 0.0
            for e in eventos:
                t = e.get("t", previo) or previo
                datos = e.get("args") or {}
                que = datos.get("objetivo") or datos.get("campo") or datos.get("texto") or e.get("problema") or e.get("resultado") or ""
                print(f"{t:6.1f} +{t - previo:4.1f}  {str(e.get('paso', '')):>3} {e.get('accion', ''):14s} {str(que)[:90]}")
                previo = t
            return
        filas = http.get(f"{args.base}/traces", headers=token).raise_for_status().json()
        desde, hasta = _hora(args.desde), _hora(args.hasta)
        filas = [f for f in filas if (desde is None or f["modified"] >= desde) and (hasta is None or f["modified"] <= hasta)]
        filas = sorted(filas[: args.limite], key=lambda f: f["modified"])
        salida = []
        for fila in filas:
            eventos = http.get(f"{args.base}/traces/{fila['id']}", headers=token).raise_for_status().json()["events"]
            r = {"id": fila["id"], "hora": time.strftime("%H:%M", time.localtime(fila["modified"])), **resumir(eventos)}
            salida.append(r)
            print(f"\n{r['hora']} {r['id']} · {r['estado']} · {r['segundos']} s · {r['turnos']} turnos "
                  f"({r['seg_por_turno']} s/turno) · {r['llamadas']} llamadas · US$ {r['costo']}")
            print(f"   tarea: {r['tarea'][:160]}")
            print(f"   acciones: {r['acciones']} · punto: {r['comprobaciones_punto']} · compuerta: "
                  f"{r['revisiones_compuerta']} ({r['rechazos_compuerta']} rechazos) · consejos: {r['consejos_supervisor']}")
            for problema in r["verificador_no_conforme"]:
                print(f"   verificador NO conforme: {problema}")
            if r["errores"]:
                print(f"   errores: {r['errores']}")
            print(f"   resumen: {r['resumen']}")
    carpeta = RAIZ / "runs" / f"traces-{time.strftime('%H%M%S')}"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "resumen.json").write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(salida)} tareas · guardado en {carpeta} (local, no se sube)")


if __name__ == "__main__":
    main()
