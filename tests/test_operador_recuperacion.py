"""Los ganchos del diario dentro del operador: un valor mal guardado se restaura al anterior exacto."""

import asyncio
import json
import time

from PIL import Image

from agent import prompts
from agent.escritorio import Escritorio
from agent.operador import Operador

CAMPO = (400, 200)
GUARDAR = (66, 376)
ANTERIOR = "Carrera 15 # 85-40"
PEDIDO = "Calle 80 # 24-15"


class FakeVnc:
    def __init__(self, pantalla: Image.Image):
        self.pantalla = pantalla

    def captura(self) -> Image.Image:
        return self.pantalla


class FakeEsc:
    """Un formulario con un campo. La primera escritura pierde el último carácter, como pasa por VNC."""

    def __init__(self):
        self.vnc = FakeVnc(Image.new("RGB", (640, 480), "white"))
        self.form = {CAMPO: ANTERIOR}
        self.guardado = dict(self.form)
        self.escrituras = 0

    tamano = (640, 480)

    async def captura(self) -> Image.Image:
        return self.vnc.pantalla

    async def rellenar_con_anterior(self, x, y, texto):
        anterior = self.form.get((x, y))
        self.escrituras += 1
        self.form[(x, y)] = texto[:-1] if self.escrituras == 1 else texto
        return anterior, self.form[(x, y)], True

    async def rellenar(self, x, y, texto):
        return (await self.rellenar_con_anterior(x, y, texto))[1]

    async def clic(self, x, y, veces=1):
        if (x, y) == GUARDAR:
            self.guardado = dict(self.form)
        return True

    async def leer_campo(self, x, y):
        return self.guardado.get((x, y))


class FakeTraza:
    def __init__(self):
        self.eventos = []

    def evento(self, **datos):
        self.eventos.append(datos)

    def captura(self, paso, imagen):
        pass


def _llamada(nombre: str, **args) -> dict:
    return {"id": f"c{time.monotonic_ns()}", "type": "function",
            "function": {"name": nombre, "arguments": json.dumps(args, ensure_ascii=False)}}  # fmt: skip


class FakeModelos:
    """El operador sigue un guion; el revisor aprueba y el verificador nunca queda conforme."""

    def __init__(self, guion: list[list[dict]]):
        self.guion = list(guion)

    async def chat(self, modelo, mensajes, herramientas=None, **extra):
        if herramientas:
            return {"content": "", "tool_calls": self.guion.pop(0)}
        if mensajes[0]["content"] == prompts.REVISOR_GUARDADO:
            return {"content": '{"aprobar": true, "motivo": "es lo que pide la tarea"}'}
        if mensajes[0]["content"] == prompts.BAJO_EL_PUNTO:
            return {"content": "Guardar"}
        return {"content": '{"conforme": false, "problema": "el campo no quedó con el valor pedido"}'}


def test_a_wrongly_saved_value_is_restored_to_the_exact_previous_one():
    esc = FakeEsc()
    traza = FakeTraza()
    guion = [
        [
            _llamada("rellenar", x=CAMPO[0], y=CAMPO[1], texto=PEDIDO, campo="Dirección"),
            _llamada("clic", x=GUARDAR[0], y=GUARDAR[1], objetivo="Guardar", cambia_datos=True),
        ],
        *[[_llamada("terminar", estado="terminada", hechos="Cambié la dirección y guardé.")] for _ in range(6)],
    ]
    operador = Operador(
        f"Corrige la dirección: ahora es {PEDIDO}.", esc, FakeModelos(guion), traza, limite=time.monotonic() + 200
    )

    cierre = asyncio.run(operador.correr())

    assert esc.guardado[CAMPO] == ANTERIOR  # la app quedó como estaba
    assert cierre.estado == "fallida" and not cierre.verificado
    assert "restaur" in cierre.razon.lower()
    reversion = [e for e in traza.eventos if e.get("accion") == "reversion"]
    assert reversion and reversion[0]["revertidas"] == 1 and reversion[0]["pendientes"] == 0


def test_a_task_that_closes_verified_is_not_touched_by_recovery():
    class Conforme(FakeModelos):
        async def chat(self, modelo, mensajes, herramientas=None, **extra):
            if not herramientas and mensajes[0]["content"] == prompts.VERIFICADOR:
                return {"content": '{"conforme": true, "problema": ""}'}
            return await super().chat(modelo, mensajes, herramientas, **extra)

    esc = FakeEsc()
    esc.escrituras = 1  # esta vez la escritura sale bien
    traza = FakeTraza()
    guion = [
        [
            _llamada("rellenar", x=CAMPO[0], y=CAMPO[1], texto=PEDIDO, campo="Dirección"),
            _llamada("clic", x=GUARDAR[0], y=GUARDAR[1], objetivo="Guardar", cambia_datos=True),
        ],
        [_llamada("terminar", estado="terminada", hechos="Cambié la dirección y guardé.")],
    ]
    operador = Operador(f"Corrige la dirección: ahora es {PEDIDO}.", esc, Conforme(guion), traza, time.monotonic() + 200)

    cierre = asyncio.run(operador.correr())

    assert cierre.estado == "terminada" and cierre.verificado
    assert esc.guardado[CAMPO] == PEDIDO
    assert not [e for e in traza.eventos if e.get("accion") in ("diagnostico", "reversion")]


def test_text_read_back_that_cannot_be_a_field_is_flagged():
    assert Escritorio.fuera_de_campo("Inicio\nListado\nAyuda", "901234567")
    assert Escritorio.fuera_de_campo("x" * 80, "Pereira")
    assert not Escritorio.fuera_de_campo("Calle 80 # 24-15", "Calle 80 # 24-15")
    assert not Escritorio.fuera_de_campo(None, "abc")
