"""La comprobación del punto: un clic no se ejecuta si bajo él hay un control distinto al anunciado."""

import asyncio
import json
import time

from PIL import Image

from agent import prompts
from agent.operador import Operador


class FakeVnc:
    def captura(self) -> Image.Image:
        return Image.new("RGB", (640, 480), "white")


class FakeEsc:
    tamano = (640, 480)

    def __init__(self):
        self.vnc = FakeVnc()
        self.clics: list[tuple[int, int]] = []

    async def captura(self) -> Image.Image:
        return self.vnc.captura()

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
    def __init__(self, guion: list[list[dict]], bajo_el_punto: str):
        self.guion, self.bajo_el_punto = list(guion), bajo_el_punto
        self.revisiones = 0

    async def chat(self, modelo, mensajes, herramientas=None, **extra):
        if herramientas:
            return {"content": "", "tool_calls": self.guion.pop(0)}
        sistema = mensajes[0]["content"]
        if sistema == prompts.BAJO_EL_PUNTO:
            return {"content": self.bajo_el_punto}
        if sistema == prompts.REVISOR_GUARDADO:
            self.revisiones += 1
            return {"content": '{"aprobar": true, "es_cambio": true, "motivo": "ok"}'}
        return {"content": '{"conforme": true, "problema": ""}'}


def _correr(guion, bajo_el_punto):
    esc, modelos = FakeEsc(), FakeModelos(guion, bajo_el_punto)
    operador = Operador("Abre el formulario de alta.", esc, modelos, FakeTraza(), limite=time.monotonic() + 200)
    asyncio.run(operador.correr())
    return esc, modelos


FIN = [_llamada("terminar", estado="fallida", hechos="No hice cambios.", razon="No encontré el control.")]


def test_harmless_click_that_lands_on_an_action_control_is_not_executed():
    guion = [[_llamada("clic", x=152, y=185, objetivo="Nuevo registro", cambia_datos=False)], FIN]
    esc, _ = _correr(guion, bajo_el_punto="Exportar")
    assert esc.clics == []


def test_harmless_click_on_the_announced_control_is_executed():
    guion = [[_llamada("clic", x=72, y=185, objetivo="Nuevo registro", cambia_datos=False)], FIN]
    esc, modelos = _correr(guion, bajo_el_punto="Nuevo registro")
    assert esc.clics == [(72, 185)] and modelos.revisiones == 0


def test_gated_click_needs_the_announced_control_under_the_point():
    guion = [[_llamada("clic", x=66, y=376, objetivo="Guardar", cambia_datos=True)], FIN]
    esc, _ = _correr(guion, bajo_el_punto="Eliminar")
    assert esc.clics == []
    esc, modelos = _correr(guion, bajo_el_punto="Guardar cambios")
    assert esc.clics == [(66, 376)] and modelos.revisiones == 1


def test_search_flagged_as_a_change_skips_the_expensive_review():
    guion = [[_llamada("clic", x=300, y=130, objetivo="botón Buscar", cambia_datos=True)], FIN]
    esc, modelos = _correr(guion, bajo_el_punto="Buscar")
    assert esc.clics == [(300, 130)] and modelos.revisiones == 0
