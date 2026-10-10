"""La comprobación del punto: un clic no se ejecuta si bajo él hay un control distinto al anunciado."""

import asyncio
import json
import time

from PIL import Image, ImageDraw

from agent import prompts
from agent.operador import Operador


class FakeVnc:
    """Una pantalla con dos botones vecinos (x 14-130 y x 136-218, a la altura y 185) y nada más."""

    def captura(self) -> Image.Image:
        imagen = Image.new("RGB", (640, 480), "white")
        dibujo = ImageDraw.Draw(imagen)
        for izquierda, derecha in ((14, 130), (136, 218)):
            dibujo.rectangle((izquierda, 174, derecha, 196), fill=(240, 240, 240), outline=(120, 120, 120))
        return imagen


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
        self.comprobaciones = 0

    async def chat(self, modelo, mensajes, herramientas=None, **extra):
        if herramientas:
            self.ultimo_usuario = [m for m in mensajes if m["role"] == "user"][-1]["content"]
            return {"content": "", "tool_calls": self.guion.pop(0)}
        sistema = mensajes[0]["content"]
        if sistema == prompts.BAJO_EL_PUNTO:
            self.comprobaciones += 1
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


def test_click_outside_any_bordered_control_is_not_point_checked():
    guion = [[_llamada("clic", x=400, y=300, objetivo="opción de menú", cambia_datos=False)], FIN]
    esc, modelos = _correr(guion, bajo_el_punto="Exportar")
    assert esc.clics == [(400, 300)] and modelos.comprobaciones == 0


def test_a_reply_without_tool_calls_is_just_a_turn_that_gets_a_reminder():
    """Tras el respaldo con el otro modelo la respuesta puede venir en texto: el operador no se cae."""
    esc, modelos = FakeEsc(), FakeModelos([[], FIN], bajo_el_punto="ninguno")
    operador = Operador("Abre el formulario de alta.", esc, modelos, FakeTraza(), limite=time.monotonic() + 200)
    cierre = asyncio.run(operador.correr())
    assert modelos.ultimo_usuario == "Responde usando una herramienta."
    assert cierre.estado == "fallida" and cierre.hechos == "No hice cambios." and cierre.verificado


def test_leaving_without_saving_is_not_a_change_even_if_flagged():
    """«Volver sin guardar» nombra el verbo negado: no pasa por el revisor, que podría impedir salir."""
    guion = [[_llamada("clic", x=144, y=389, objetivo="Volver sin guardar", cambia_datos=True)], FIN]
    esc, modelos = _correr(guion, bajo_el_punto="Volver")
    assert esc.clics == [(144, 389)] and modelos.revisiones == 0


def test_a_harmless_looking_name_does_not_hide_a_save_button():
    guion = [[_llamada("clic", x=72, y=185, objetivo="Volver sin guardar", cambia_datos=False)], FIN]
    esc, _ = _correr(guion, bajo_el_punto="Guardar")
    assert esc.clics == []


def test_the_same_rejected_action_stops_being_reviewed():
    class Rechaza(FakeModelos):
        async def chat(self, modelo, mensajes, herramientas=None, **extra):
            if not herramientas and mensajes[0]["content"] == prompts.REVISOR_GUARDADO:
                self.revisiones += 1
                return {"content": '{"aprobar": false, "es_cambio": true, "motivo": "falta un dato"}'}
            return await super().chat(modelo, mensajes, herramientas, **extra)

    guardar = [_llamada("clic", x=66, y=376, objetivo="Guardar", cambia_datos=True)]
    esc, modelos = FakeEsc(), Rechaza([guardar] * 5 + [FIN], bajo_el_punto="Guardar")
    operador = Operador("Abre el formulario de alta.", esc, modelos, FakeTraza(), limite=time.monotonic() + 200)
    asyncio.run(operador.correr())
    assert esc.clics == [] and modelos.revisiones == 3
    assert "ya rechazó 3 veces" in modelos.ultimo_usuario or any("ya rechazó 3 veces" in linea for linea in operador.bitacora)


def test_an_action_button_the_operator_did_not_flag_goes_to_the_reviewer():
    """La compuerta no depende de una lista de verbos: el modelo dice si el control ejecuta algo."""
    guion = [[_llamada("clic", x=72, y=185, objetivo="Liquidar turno", cambia_datos=False)], FIN]
    esc, modelos = _correr(guion, bajo_el_punto='{"texto": "Liquidar turno", "ejecuta": true}')
    assert esc.clics == [(72, 185)] and modelos.revisiones == 1


def test_a_harmless_click_that_lands_on_another_action_is_not_executed():
    guion = [[_llamada("clic", x=72, y=185, objetivo="Ver detalle", cambia_datos=False)], FIN]
    esc, modelos = _correr(guion, bajo_el_punto='{"texto": "Liquidar turno", "ejecuta": true}')
    assert esc.clics == [] and modelos.revisiones == 0


def test_a_control_that_only_opens_something_is_not_reviewed():
    guion = [[_llamada("clic", x=72, y=185, objetivo="Nuevo registro", cambia_datos=False)], FIN]
    esc, modelos = _correr(guion, bajo_el_punto='{"texto": "Nuevo registro", "ejecuta": false}')
    assert esc.clics == [(72, 185)] and modelos.revisiones == 0


def test_a_search_button_is_never_treated_as_an_action():
    guion = [[_llamada("clic", x=72, y=185, objetivo="Buscar", cambia_datos=False)], FIN]
    esc, modelos = _correr(guion, bajo_el_punto='{"texto": "Buscar", "ejecuta": true}')
    assert esc.clics == [(72, 185)] and modelos.revisiones == 0


def test_more_action_verbs_are_gated_by_name():
    guion = [[_llamada("clic", x=400, y=300, objetivo="Emitir recibo", cambia_datos=False)], FIN]
    esc, modelos = _correr(guion, bajo_el_punto="Emitir recibo")
    assert esc.clics == [(400, 300)] and modelos.revisiones == 1
