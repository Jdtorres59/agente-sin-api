"""Núcleo del agente: `run_task` según `agent/contracts.py`.

Flujo: conectar → Comprensión (TaskSpec) → memoria (huella, notas, receta) → Operador con
compuertas y verificación → Reporte. `memory` puede ser None y el núcleo funciona igual.
"""

import asyncio
import os
from pathlib import Path

from . import reporte
from .comprension import comprender
from .contracts import Memory, TaskResult
from .driver import DriverRFB
from .modelos import Modelos
from .operador import Operador
from .traza import Traza


async def run_task(
    tarea: str, url: str, password: str, deadline: float, memory: Memory | None, trace_dir: Path
) -> TaskResult:
    traza = Traza(trace_dir)
    traza.evento(accion="inicio", tarea=tarea)
    modelos = Modelos(limite=deadline)  # los reintentos respetan el reloj de la tarea
    driver = DriverRFB()
    secretos = [password] + [
        os.environ.get(nombre, "")
        for nombre in ("TEAM_TOKEN", "OPENROUTER_API_KEY", "DESKTOP_TOKEN", "DESKTOP_PASSWORD")
    ]
    resultado = TaskResult("fallida", "No se alcanzó a operar el escritorio.")
    operador: Operador | None = None
    try:
        for intento in range(3):
            try:
                await driver.connect(url, password)
                # Un escritorio recién reiniciado puede seguir cargando: se espera a que la pantalla se aquiete.
                await driver.vnc.asentar(quieto=0.7, esperar_cambio=1.5, maximo=6.0)
                break
            except Exception as error:
                # Suite 18:27: el escritorio recién reiniciado cortó la primera conexión a los 5 s y la tarea
                # se perdió sin empezar. Se reintenta dos veces antes de darla por perdida.
                if intento == 2:
                    raise
                traza.evento(accion="reconexion_inicial", tipo=type(error).__name__, intento=intento + 1)
                try:
                    await driver.close()
                except Exception:
                    pass
                driver = DriverRFB()
                await asyncio.sleep(2)
        resultado.spec = await comprender(tarea, modelos)
        traza.evento(accion="spec", spec=resultado.spec)

        notas, aviso = "", ""
        if memory is not None:
            try:  # una memoria rota no puede tumbar la tarea
                resultado.app_id = memory.fingerprint(await driver.screenshot())
                notas = memory.notes(resultado.app_id) or ""
                receta = memory.find_recipe(resultado.app_id, resultado.spec)
                if receta is not None:
                    repeticion = await memory.replay(receta, resultado.spec, driver)
                    traza.evento(accion="receta", ok=repeticion.ok, pasos=repeticion.steps_done, motivo=repeticion.reason)
                    aviso = (
                        f"Una receta aprendida ya ejecutó {repeticion.steps_done} paso(s) de esta tarea"
                        + ("" if repeticion.ok else " y se detuvo antes de terminar")
                        + (f". Detalle: {repeticion.reason}" if repeticion.reason else "")
                        + ". Mira la pantalla, no repitas lo que ya esté hecho, completa lo que falte y verifica."
                    )
            except Exception as error:
                traza.evento(accion="memoria_error", tipo=type(error).__name__)

        operador = Operador(
            tarea, driver.esc, modelos, traza, limite=deadline, spec=resultado.spec, notas=notas, aviso_inicial=aviso
        )
        cierre = await operador.correr()
        resultado.estado = cierre.estado if cierre.estado in ("terminada", "fallida") else "fallida"
        resultado.resumen = reporte.componer(resultado.estado, cierre.hechos, cierre.respuesta, cierre.razon, secretos)
        resultado.verified = cierre.verificado
    except Exception as error:  # el resumen nunca lleva el detalle crudo: puede contener secretos
        traza.evento(accion="error", tipo=type(error).__name__, detalle=str(error)[:500])
        resultado.estado = "fallida"
        resultado.resumen = f"No se pudo completar la operación del escritorio ({type(error).__name__})."
    finally:
        # Se cierra antes de devolver el estado final: después de reportar no se toca nada más.
        await driver.close()
        await modelos.cerrar()
    if operador is not None:
        resultado.steps = operador.steps
        resultado.feedback = operador.objeciones
    resultado.cost_usd = round(modelos.uso.costo, 5)
    traza.evento(
        accion="fin",
        estado=resultado.estado,
        resumen=resultado.resumen,
        verificado=resultado.verified,
        costo=resultado.cost_usd,
        llamadas=modelos.uso.llamadas,
        costo_por_modelo={m: round(c, 5) for m, c in modelos.uso.por_modelo.items()},
        pasos=len(resultado.steps),
    )
    return resultado
