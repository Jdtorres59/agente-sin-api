"""Tras una objeción del verificador el operador no puede volver a cerrar sin haber hecho nada."""

import asyncio
import json
import time

from PIL import Image

from agent import prompts
from agent.operador import MAX_OBJECIONES, Operador


class FakeVnc:
    def captura(self) -> Image.Image:
        return Image.new("RGB", (640, 480), "white")


class FakeEsc:
    tamano = (640, 480)

    def __init__(self):
        self.vnc = FakeVnc()
        self.clics: list[tuple[int, int]] = []

    async def captura(self) -> Image.Image:
        return Image.new("RGB", (640, 480), "white")

    async def clic(self, x, y, veces=1):
        self.clics.append((x, y))
        return True


class FakeTraza:
    def evento(self, **datos):
        pass

    def captura(self, paso, imagen):
        pass


def _llamada(nombre: str, **args) -> dict:
    return {"id": f"c{time.monotonic_ns()}", "type": "function",
            "function": {"name": nombre, "arguments": json.dumps(args, ensure_ascii=False)}}  # fmt: skip


class FakeModelos:
    """El verificador objeta hasta que el operador hace clic en el detalle; después acepta."""

    def __init__(self, guion, esc, acepta_tras_clic=True):
        self.guion, self.esc, self.acepta_tras_clic = list(guion), esc, acepta_tras_clic
        self.verificaciones = 0
        self.avisos: list[str] = []

    async def chat(self, modelo, mensajes, herramientas=None, **extra):
        if herramientas:
            ultimo = [m for m in mensajes if m["role"] == "user"][-1]["content"]
            self.avisos.append(ultimo if isinstance(ultimo, str) else ultimo[0]["text"])
            return {"content": "", "tool_calls": self.guion.pop(0)}
        if mensajes[0]["content"] == prompts.VERIFICADOR:
            self.verificaciones += 1
            if self.acepta_tras_clic and self.esc.clics:
                return {"content": '{"conforme": true, "problema": ""}'}
            return {"content": '{"conforme": false, "problema": "no abrió el detalle del registro"}'}
        return {"content": '{"consejo": ""}'}


RENDIRSE = [_llamada("terminar", estado="fallida", hechos="No hice cambios.", razon="No encontré el dato.")]
ABRIR = [_llamada("clic", x=400, y=300, objetivo="fila del registro", cambia_datos=False)]
HECHO = [_llamada("terminar", estado="terminada", hechos="Leí el dato en el detalle.", respuesta="42")]



def _correr(guion, acepta_tras_clic=True):
    esc = FakeEsc()
    modelos = FakeModelos(guion, esc, acepta_tras_clic)
    operador = Operador("¿Cuál es el dato del registro?", esc, modelos, FakeTraza(), limite=time.monotonic() + 200)
    return asyncio.run(operador.correr()), modelos


def test_giving_up_again_without_working_is_bounced_and_the_task_goes_on():
    cierre, modelos = _correr([RENDIRSE, RENDIRSE, ABRIR, HECHO])
    assert cierre.estado == "terminada" and cierre.verificado and cierre.respuesta == "42"
    assert modelos.verificaciones == 2  # el segundo «fallida» no se consultó: no había hecho nada
    assert any("no has hecho nada desde la objeción" in aviso for aviso in modelos.avisos)
    assert any("no abrió el detalle del registro" in aviso for aviso in modelos.avisos)


def test_a_justified_failure_is_accepted_at_once():
    class Acepta(FakeModelos):
        async def chat(self, modelo, mensajes, herramientas=None, **extra):
            if not herramientas and mensajes[0]["content"] == prompts.VERIFICADOR:
                self.verificaciones += 1
                return {"content": '{"conforme": true, "problema": ""}'}
            return await super().chat(modelo, mensajes, herramientas, **extra)

    esc = FakeEsc()
    modelos = Acepta([RENDIRSE], esc)
    operador = Operador("Borra todos los registros.", esc, modelos, FakeTraza(), limite=time.monotonic() + 200)
    cierre = asyncio.run(operador.correr())
    assert cierre.estado == "fallida" and cierre.verificado and modelos.verificaciones == 1


def test_objections_are_bounded():
    cierre, modelos = _correr([RENDIRSE] * 8, acepta_tras_clic=False)
    assert cierre.estado == "fallida" and not cierre.verificado
    assert modelos.verificaciones == MAX_OBJECIONES


def test_an_answer_held_after_working_on_the_objection_is_accepted_the_second_time():
    cierre, modelos = _correr([HECHO, ABRIR, HECHO], acepta_tras_clic=False)
    assert cierre.estado == "terminada" and cierre.respuesta == "42" and not cierre.verificado
    assert modelos.verificaciones == 2


def test_a_changed_answer_is_not_a_held_answer():
    otra = [_llamada("terminar", estado="terminada", hechos="Leí otra vez.", respuesta="43")]
    cierre, modelos = _correr([HECHO, ABRIR, otra[0:1], ABRIR, otra[0:1]] + [RENDIRSE] * 6,
                              acepta_tras_clic=False)
    assert modelos.verificaciones >= 3  # 42 y luego 43: la segunda no es la misma respuesta
    assert cierre.respuesta == "43"


def test_when_time_runs_out_the_reason_of_the_last_proposed_failure_is_kept():
    rendirse = [_llamada("terminar", estado="fallida", hechos="Busqué tres veces.", razon="No existe «Papelería El Cóndor».")]
    esc = FakeEsc()
    modelos = FakeModelos([rendirse, ABRIR] + [ABRIR] * 40, esc, acepta_tras_clic=False)
    operador = Operador("¿Cuál es el dato de Papelería El Cóndor?", esc, modelos, FakeTraza(), limite=time.monotonic() + 200)
    operador._quedan = lambda: 0 if len(esc.clics) >= 3 else 200
    cierre = asyncio.run(operador.correr())
    assert cierre.estado == "fallida" and "Papelería El Cóndor" in cierre.razon
