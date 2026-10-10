"""`leer_opciones`: abre la lista, devuelve sus opciones como texto y la cierra sin elegir."""

import asyncio
import json
import time

from PIL import Image, ImageDraw

from agent import prompts
from agent.operador import Operador


class FakeVnc:
    def __init__(self, esc):
        self.esc = esc

    def captura(self) -> Image.Image:
        return self.esc.pantalla()


class FakeEsc:
    """Un formulario con una lista en (300, 200) que se abre con un clic y se cierra con Escape."""

    tamano = (640, 480)

    def __init__(self):
        self.vnc = FakeVnc(self)
        self.abierta = False
        self.teclas: list[str] = []

    def pantalla(self) -> Image.Image:
        imagen = Image.new("RGB", (640, 480), "white")
        if self.abierta:
            ImageDraw.Draw(imagen).rectangle((220, 212, 380, 330), fill=(250, 250, 250), outline=(90, 90, 90))
        return imagen

    async def captura(self) -> Image.Image:
        return self.pantalla()

    async def clic(self, x, y, veces=1):
        self.abierta = (x, y) == (300, 200)
        return True

    async def tecla(self, combinacion):
        self.teclas.append(combinacion)
        self.abierta = False
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
    def __init__(self, guion):
        self.guion = list(guion)
        self.resultados: list[str] = []
        self.al_verificador: list = []

    async def chat(self, modelo, mensajes, herramientas=None, **extra):
        if herramientas:
            self.resultados += [m["content"] for m in mensajes if m["role"] == "tool"][len(self.resultados):]
            return {"content": "", "tool_calls": self.guion.pop(0)}
        sistema = mensajes[0]["content"]
        if sistema == prompts.OPCIONES_DE_LISTA:
            return {"content": "(seleccione)\nNorte\nSur\n"}
        if sistema == prompts.VERIFICADOR:
            self.al_verificador = mensajes[1]["content"]
        return {"content": '{"conforme": true, "problema": ""}'}


def _correr(x, y):
    fin = [_llamada("terminar", estado="fallida", hechos="No guardé.", razon="La lista no trae «Oriente».")]
    esc, modelos = FakeEsc(), FakeModelos([[_llamada("leer_opciones", x=x, y=y, lista="Zona")], fin])
    operador = Operador("Registra la zona Oriente.", esc, modelos, FakeTraza(), limite=time.monotonic() + 200)
    asyncio.run(operador.correr())
    return esc, modelos


def test_options_come_back_as_text_and_the_list_is_closed_without_choosing():
    esc, modelos = _correr(300, 200)
    assert "(seleccione) | Norte | Sur" in modelos.resultados[0]
    assert esc.teclas == ["escape"] and not esc.abierta


def test_the_verifier_sees_the_open_list():
    _, modelos = _correr(300, 200)
    texto = modelos.al_verificador[0]["text"]
    assert "IMAGEN 1: última lista desplegable leída con leer_opciones, abierta" in texto
    assert len(modelos.al_verificador) == 3  # texto, lista abierta y pantalla actual


def test_a_point_that_opens_nothing_is_reported():
    esc, modelos = _correr(500, 400)
    assert "la lista no se abrió" in modelos.resultados[0] and esc.teclas == []


def test_it_does_not_click_on_a_control_that_executes_something():
    class SobreUnBoton(FakeModelos):
        async def chat(self, modelo, mensajes, herramientas=None, **extra):
            if not herramientas and mensajes[0]["content"] == prompts.BAJO_EL_PUNTO:
                return {"content": '{"texto": "Guardar", "ejecuta": true}'}
            return await super().chat(modelo, mensajes, herramientas, **extra)

    fin = [_llamada("terminar", estado="fallida", hechos="No guardé.", razon="No encontré la lista.")]
    esc, modelos = FakeEsc(), SobreUnBoton([[_llamada("leer_opciones", x=300, y=200, lista="Zona")], fin])
    operador = Operador("Registra la zona Oriente.", esc, modelos, FakeTraza(), limite=time.monotonic() + 200)
    asyncio.run(operador.correr())
    assert not esc.abierta and esc.teclas == [] and "no es una lista" in modelos.resultados[0]
