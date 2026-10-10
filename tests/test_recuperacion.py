"""Tests for the deterministic fallback (journal, diagnosis rules, reversal) with a fake desktop."""

import asyncio
import time
from types import SimpleNamespace

from PIL import Image

from agent.recuperacion import Diario, diagnosticar_por_reglas, misma_pantalla, recuperar, restaurar_rapido


class FakeEsc:
    """A form with fields keyed by (x, y). Saving copies the form values into the stored record."""

    def __init__(self, campos: dict, pantalla: Image.Image):
        self.form = dict(campos)
        self.guardado = dict(campos)
        self.pantalla = pantalla
        self.acciones: list[str] = []

    async def captura(self):
        return self.pantalla

    async def rellenar(self, x, y, texto):
        self.acciones.append(f"rellenar {x},{y}={texto}")
        self.form[(x, y)] = texto
        return texto or None

    async def clic(self, x, y, veces=1):
        self.acciones.append(f"clic {x},{y}")
        self.guardado = dict(self.form)
        return True

    async def leer_campo(self, x, y):
        return self.guardado.get((x, y))


class FakeTraza:
    def __init__(self):
        self.eventos = []

    def evento(self, **kw):
        self.eventos.append(kw)


def _diario_con_edicion(anterior="3112223344", nuevo="3014445566", quedo=None):
    d = Diario()
    d.antes_de_rellenar(400, 200, "Teléfono", anterior, nuevo)
    d.despues_de_rellenar(quedo if quedo is not None else nuevo)
    d.cambio_confirmado("clic en «Guardar»", 66, 376, Image.new("RGB", (100, 100), "white"), 7)
    return d


def test_pending_edits_only_count_when_confirmed():
    d = Diario()
    d.antes_de_rellenar(1, 1, "Nombre", "A", "B")
    assert not d.hay_cambios and d.reversibles() == []
    d.cambio_confirmado("Guardar", 5, 5, None, 3)
    assert d.hay_cambios and len(d.reversibles()) == 1


def test_filling_empty_field_is_not_reversible():
    d = Diario()
    d.antes_de_rellenar(1, 1, "NIT", None, "901234567")  # creation form: field was empty
    d.cambio_confirmado("Guardar", 5, 5, None, 3)
    assert d.hay_cambios and d.reversibles() == []


def test_rules():
    assert diagnosticar_por_reglas(Diario()).causa == "sin_cambios"
    mal = _diario_con_edicion(quedo="30144455")  # read-back differs from what was asked
    diag = diagnosticar_por_reglas(mal)
    assert diag.causa == "valor_incorrecto" and diag.restaurar
    assert diagnosticar_por_reglas(_diario_con_edicion()) is None  # ambiguous: needs the supervisor


def test_same_screen_gate():
    blanca = Image.new("RGB", (100, 100), "white")
    casi = blanca.copy()
    casi.putpixel((1, 1), (0, 0, 0))
    assert misma_pantalla(blanca, casi)
    assert not misma_pantalla(blanca, Image.new("RGB", (100, 100), "black"))
    assert not misma_pantalla(blanca, None)


def test_fast_revert_restores_exact_previous_value():
    d = _diario_con_edicion()
    esc = FakeEsc({(400, 200): "3014445566"}, Image.new("RGB", (100, 100), "white"))
    revertidas, problemas = asyncio.run(restaurar_rapido(d, esc))
    assert revertidas == 1 and problemas == []
    assert esc.guardado[(400, 200)] == "3112223344"
    assert d.reversibles() == []


def test_fast_revert_refuses_when_screen_changed():
    d = _diario_con_edicion()
    esc = FakeEsc({(400, 200): "3014445566"}, Image.new("RGB", (100, 100), "black"))
    revertidas, problemas = asyncio.run(restaurar_rapido(d, esc))
    assert revertidas == 0 and esc.acciones == [] and "ya no es" in problemas[0]


def test_recuperar_reverts_on_rule_and_reports_fallida():
    d = _diario_con_edicion(quedo="30144455")
    esc = FakeEsc({(400, 200): "30144455"}, Image.new("RGB", (100, 100), "white"))
    cierre = SimpleNamespace(estado="terminada", razon="", hechos="cambié el teléfono")
    out = asyncio.run(recuperar(cierre, d, esc, modelos=None, traza=FakeTraza(), limite=time.monotonic() + 120))
    assert out.estado == "fallida" and "restauraron 1" in out.razon
    assert esc.guardado[(400, 200)] == "3112223344"


def test_recuperar_does_not_revert_without_time():
    d = _diario_con_edicion(quedo="30144455")
    esc = FakeEsc({(400, 200): "30144455"}, Image.new("RGB", (100, 100), "white"))
    cierre = SimpleNamespace(estado="terminada", razon="", hechos="")
    out = asyncio.run(recuperar(cierre, d, esc, modelos=None, traza=FakeTraza(), limite=time.monotonic() + 10))
    assert out.estado == "fallida" and "No hubo tiempo" in out.razon and esc.acciones == []
