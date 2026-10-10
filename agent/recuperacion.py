"""Deterministic fallback: a journal of data changes, a diagnosis of what failed, and reversal.

The grader compares the app's final state with the initial one. If the agent saved a wrong
value or edited the wrong record, restoring the exact previous value removes the extra change,
and an honest `fallida` with its reason can still be a correct task. So:

1. Diario: before a field is overwritten, the operator reads its exact current value (clipboard)
   and notes it. Edits are pending until a data-changing action (e.g. "Guardar") confirms them.
2. diagnosticar: code rules first (nothing confirmed, read-back mismatch); the supervisor model
   only classifies what the rules cannot decide, from a closed list of causes.
3. restaurar: only edits in the journal with a known previous value can be reverted. Fast path is
   pure code (same form still on screen: refill, save, read back). Otherwise a constrained
   sub-operator navigates back, and code checks the read-back. Never deletes records.

Owner: Juanda. Hooks inside agent/operador.py: Juanes (see GANCHOS below).

GANCHOS (in Operador):
    self.diario = Diario()
    # in _ejecutar, before "rellenar":
    anterior = await self.esc.leer_campo(a["x"], a["y"])
    self.diario.antes_de_rellenar(a["x"], a["y"], a.get("campo", ""), anterior, a["texto"])
    # right after "rellenar" returns `quedo`:
    self.diario.despues_de_rellenar(quedo)
    # after a confirmed cambia_datos action (clic or tecla) is executed:
    self.diario.cambio_confirmado(descripcion, a.get("x"), a.get("y"), self.evidencia, paso)
    # where the operator gives up with confirmed changes (verifier rejected, time or turns out):
    if self.diario.hay_cambios:
        cierre = await recuperar(cierre, self.diario, self.esc, self.modelos, self.traza,
                                 self.limite, navegar=self._sub_operador)
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from PIL import Image

CAUSAS = (
    "sin_cambios",              # nothing was confirmed: nothing to undo
    "correcto_sin_evidencia",   # change looks right, only the proof was missing: keep it
    "valor_incorrecto",         # saved a value different from the task
    "registro_equivocado",      # edited a record that is not the one the task meant (or ambiguous)
    "duplicado",                # created a record that already existed
    "imposible_tarde",          # task turned out impossible/dangerous after a change was applied
    "otro",
)
REVERSIBLES = {"valor_incorrecto", "registro_equivocado", "imposible_tarde"}
MARGEN_RESTAURAR_S = 40  # below this, reverting risks running past the deadline: report instead


@dataclass
class Edicion:
    x: int
    y: int
    campo: str
    anterior: str | None   # exact value before overwriting (None: the field was empty)
    nuevo: str
    quedo: str | None = None
    confirmada: bool = False
    revertida: bool = False


@dataclass
class Cambio:
    descripcion: str        # e.g. 'clic en «Guardar» (812, 640)'
    x: int | None
    y: int | None
    paso: int
    ediciones: list[Edicion] = field(default_factory=list)
    evidencia: Image.Image | None = None


class Diario:
    def __init__(self):
        self.cambios: list[Cambio] = []
        self._pendientes: list[Edicion] = []

    def antes_de_rellenar(self, x: int, y: int, campo: str, anterior: str | None, nuevo: str) -> None:
        self._pendientes.append(Edicion(x, y, campo, anterior, nuevo))

    def despues_de_rellenar(self, quedo: str | None) -> None:
        if self._pendientes:
            self._pendientes[-1].quedo = quedo

    def cambio_confirmado(self, descripcion: str, x: int | None, y: int | None,
                          evidencia: Image.Image | None, paso: int) -> None:
        for e in self._pendientes:
            e.confirmada = True
        self.cambios.append(Cambio(descripcion, x, y, paso, self._pendientes, evidencia))
        self._pendientes = []

    @property
    def hay_cambios(self) -> bool:
        return bool(self.cambios)

    def ediciones_confirmadas(self) -> list[Edicion]:
        return [e for c in self.cambios for e in c.ediciones if e.confirmada and not e.revertida]

    def reversibles(self) -> list[Edicion]:
        """Edits that overwrote an existing value. Filling an empty field (a creation) is not reverted."""
        return [e for e in self.ediciones_confirmadas() if e.anterior is not None and e.anterior != e.nuevo]

    def resumen(self) -> str:
        if not self.cambios:
            return "sin cambios confirmados"
        partes = []
        for c in self.cambios:
            eds = "; ".join(f"«{e.campo}»: «{e.anterior or ''}» → «{e.nuevo}»" for e in c.ediciones) or "sin campos editados"
            partes.append(f"paso {c.paso}: {c.descripcion} ({eds})")
        return " | ".join(partes)


@dataclass
class Diagnostico:
    causa: str
    restaurar: bool
    explicacion: str
    origen: str  # reglas | supervisor


def diagnosticar_por_reglas(diario: Diario) -> Diagnostico | None:
    """Decide without a model when the evidence is unambiguous. None: ask the supervisor."""
    if not diario.hay_cambios:
        return Diagnostico("sin_cambios", False, "No se confirmó ningún cambio de datos.", "reglas")
    mal_escritas = [e for e in diario.ediciones_confirmadas() if e.quedo is not None and e.quedo != e.nuevo]
    if mal_escritas:
        e = mal_escritas[0]
        return Diagnostico("valor_incorrecto", True,
                           f"El campo «{e.campo}» quedó con «{e.quedo}» en vez de «{e.nuevo}».", "reglas")
    return None


INSTRUCCIONES_DIAGNOSTICO = """\
Eres el auditor de un operador que maneja una aplicación de gestión desconocida. La tarea NO se pudo \
verificar como terminada, pero el operador ya aplicó cambios de datos. Decide la causa y si hay que \
restaurar los cambios para dejar la aplicación como estaba.

Causas posibles (elige una): sin_cambios, correcto_sin_evidencia, valor_incorrecto, registro_equivocado, \
duplicado, imposible_tarde, otro.
- registro_equivocado: se cambió un registro distinto al que pide la tarea, o la tarea era ambigua \
(varios candidatos) y aun así se cambió uno.
- imposible_tarde: después de cambiar algo se descubrió que la tarea era imposible o peligrosa.
- correcto_sin_evidencia: el cambio es el pedido y está bien; solo faltó la prueba. No se revierte.
Restaurar solo si el cambio aplicado no debería existir. Responde SOLO con JSON:
{"causa": "...", "restaurar": true|false, "explicacion": "una frase con los datos concretos"}"""


async def diagnosticar(tarea: str, problema: str, diario: Diario, esc, modelos, supervisor: str) -> Diagnostico:
    por_reglas = diagnosticar_por_reglas(diario)
    if por_reglas is not None:
        return por_reglas
    from .modelos import imagen_a_url

    captura = await esc.captura()
    texto = (f"TAREA: {tarea}\nPOR QUÉ NO SE VERIFICÓ: {problema}\n"
             f"CAMBIOS CONFIRMADOS (diario exacto): {diario.resumen()}")
    mensaje = await modelos.chat(
        supervisor,
        [{"role": "system", "content": INSTRUCCIONES_DIAGNOSTICO},
         {"role": "user", "content": [{"type": "text", "text": texto},
                                      {"type": "image_url", "image_url": {"url": imagen_a_url(captura)}}]}],
        max_tokens=600,
    )
    datos = _json(mensaje.get("content") or "")
    causa = datos.get("causa") if datos.get("causa") in CAUSAS else "otro"
    # The model proposes; code decides: only causes that mean "this change should not exist" revert.
    restaurar = bool(datos.get("restaurar")) and causa in REVERSIBLES
    return Diagnostico(causa, restaurar, str(datos.get("explicacion", ""))[:400], "supervisor")


def _json(texto: str) -> dict:
    encontrado = re.search(r"\{.*\}", texto, re.S)
    try:
        return json.loads(encontrado.group(0)) if encontrado else {}
    except json.JSONDecodeError:
        return {}


MISMA_PANTALLA_MAX = 0.02  # at most 2% of pixels may differ from the post-save evidence


def misma_pantalla(a: Image.Image | None, b: Image.Image | None) -> bool:
    """True if two screenshots show the same screen (small notices and the caret tolerated)."""
    if a is None or b is None or a.size != b.size:
        return False
    from PIL import ImageChops

    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L").point(lambda v: 255 if v > 30 else 0)
    return diff.histogram()[255] <= MISMA_PANTALLA_MAX * a.width * a.height


async def restaurar_rapido(diario: Diario, esc) -> tuple[int, list[str]]:
    """Pure-code path while the edited form is still on screen: refill exact previous values,
    repeat the confirming action, and read every field back. Returns (reverted, problems).

    Safety gate: it only types if the current screen matches the evidence captured right after
    the save. Otherwise the old coordinates could land on a different control."""
    revertidas, problemas = 0, []
    for cambio in reversed(diario.cambios):
        eds = [e for e in cambio.ediciones if e in diario.reversibles()]
        if not eds:
            continue
        if not misma_pantalla(await esc.captura(), cambio.evidencia):
            problemas.append("la pantalla ya no es la del formulario guardado")
            return revertidas, problemas
        for e in eds:
            quedo = await esc.rellenar(e.x, e.y, e.anterior or "")
            if quedo != e.anterior:
                problemas.append(f"no se pudo restaurar «{e.campo}» en pantalla (quedó «{quedo}»)")
                return revertidas, problemas
        if cambio.x is None or cambio.y is None:
            problemas.append("no se sabe dónde confirmar el cambio")
            return revertidas, problemas
        await esc.clic(cambio.x, cambio.y)
        for e in eds:
            leido = await esc.leer_campo(e.x, e.y)
            if leido == e.anterior:
                e.revertida = True
                revertidas += 1
            else:
                problemas.append(f"«{e.campo}» no quedó con el valor anterior tras confirmar (se leyó «{leido}»)")
    return revertidas, problemas


async def recuperar(cierre, diario: Diario, esc, modelos, traza, limite: float,
                    navegar: Callable[[str, float], Awaitable] | None = None, tarea: str = ""):
    """Called when the operator gives up with confirmed changes. Returns the (possibly updated) Cierre."""
    supervisor = os.environ.get("SUPERVISOR_MODEL", "anthropic/claude-sonnet-5.5")
    problema = getattr(cierre, "razon", "") or "sin verificar"
    diag = await diagnosticar(tarea or getattr(cierre, "hechos", ""), problema, diario, esc, modelos, supervisor)
    traza.evento(accion="diagnostico", causa=diag.causa, restaurar=diag.restaurar,
                 explicacion=diag.explicacion, origen=diag.origen, diario=diario.resumen())
    cierre.estado = "fallida"
    if not diag.restaurar or not diario.reversibles():
        cierre.razon = _unir(cierre.razon, diag.explicacion)
        return cierre
    if limite - time.monotonic() < MARGEN_RESTAURAR_S:
        cierre.razon = _unir(cierre.razon, f"{diag.explicacion} No hubo tiempo de restaurar: {diario.resumen()}")
        return cierre

    revertidas, problemas = await restaurar_rapido(diario, esc)
    pendientes = diario.reversibles()
    if pendientes and navegar is not None and limite - time.monotonic() >= MARGEN_RESTAURAR_S:
        # Navigation is model-assisted; what to restore and the final check stay in code.
        instruccion = ("REVERSIÓN. No hagas nada más que esto: " + "; ".join(
            f"deja el campo «{e.campo}» con el valor exacto «{e.anterior}» (hoy dice «{e.nuevo}»)" for e in pendientes)
            + ". Guarda una sola vez y relee el campo para confirmar.")
        sub = await navegar(instruccion, limite - 15)
        # Counts only if the sub-operator closed as done AND its independent verifier confirmed it.
        if getattr(sub, "estado", "") == "terminada" and getattr(sub, "verificado", False):
            for e in pendientes:
                e.revertida = True
                revertidas += 1
        else:
            problemas.append(f"la reversión asistida no se pudo verificar: {getattr(sub, 'razon', '')}")
    traza.evento(accion="reversion", revertidas=revertidas, pendientes=len(diario.reversibles()), problemas=problemas)
    nota = f"Se restauraron {revertidas} cambio(s) al valor anterior exacto." if revertidas else "No se pudo restaurar."
    cierre.razon = _unir(cierre.razon, f"{diag.explicacion} {nota}")
    return cierre


def _unir(*partes: str) -> str:
    return " ".join(p.strip() for p in partes if p and p.strip())
