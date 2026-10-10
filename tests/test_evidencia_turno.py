"""`evidencia_turno`: el verificador ve la captura que el operador señala, no solo la pantalla final."""

import asyncio
import json
import time

from PIL import Image

from agent import prompts
from agent.operador import Operador, _entero


class FakeVnc:
    def captura(self) -> Image.Image:
        return Image.new("RGB", (640, 480), "white")


class FakeEsc:
    """Cada captura es distinta, para poder saber cuál recibió el verificador."""

    tamano = (640, 480)

    def __init__(self):
        self.vnc = FakeVnc()
        self.tomadas = 0

    async def captura(self) -> Image.Image:
        self.tomadas += 1
        return Image.new("RGB", (640, 480), (self.tomadas, 0, 0))

    async def clic(self, x, y, veces=1):
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
    """Guarda la captura que vio el operador en cada turno y lo que recibió el verificador."""

    def __init__(self, guion: list[list[dict]]):
        self.guion = list(guion)
        self.vistas: list[str] = []
        self.al_verificador: list = []

    async def chat(self, modelo, mensajes, herramientas=None, **extra):
        if herramientas:
            self.vistas.append(mensajes[-1]["content"][1]["image_url"]["url"])
            return {"content": "", "tool_calls": self.guion.pop(0)}
        if mensajes[0]["content"] == prompts.VERIFICADOR:
            self.al_verificador = mensajes[1]["content"]
        return {"content": '{"conforme": true, "problema": ""}'}


def _correr(turno):
    cierre = _llamada(
        "terminar", estado="fallida", hechos="No guardé.", razon="La lista no trae la opción.", evidencia_turno=turno
    )
    guion = [[_llamada("clic", x=400, y=300, objetivo="lista de opciones", cambia_datos=False)], [cierre]]
    modelos = FakeModelos(guion)
    operador = Operador("Registra el valor.", FakeEsc(), modelos, FakeTraza(), limite=time.monotonic() + 200)
    asyncio.run(operador.correr())
    texto = modelos.al_verificador[0]["text"]
    imagenes = [parte["image_url"]["url"] for parte in modelos.al_verificador[1:]]
    return modelos, texto, imagenes


def test_verifier_gets_the_screenshot_of_the_turn_the_operator_points_to():
    modelos, texto, imagenes = _correr(turno=2)
    assert len(imagenes) == 2
    assert imagenes[0] == modelos.vistas[1]  # la captura del turno 2, tal como la vio el operador
    assert imagenes[1] not in modelos.vistas  # la pantalla actual es una captura nueva
    assert "IMAGEN 1: captura que el operador vio al empezar el turno 2" in texto
    assert "IMAGEN 2: pantalla actual" in texto


def test_turn_sent_as_text_also_works():
    _, _, imagenes = _correr(turno="2")
    assert len(imagenes) == 2


def test_unknown_turn_is_ignored():
    _, texto, imagenes = _correr(turno=99)
    assert len(imagenes) == 1 and "IMAGEN" not in texto


def test_turn_number_parsing():
    assert _entero(6) == 6 and _entero("6") == 6
    assert _entero(None) is None and _entero("la de la lista") is None
