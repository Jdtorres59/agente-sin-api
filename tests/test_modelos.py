"""Reintentos del cliente de modelos: un error pasajero del proveedor no tumba la tarea."""

import asyncio

import httpx
import pytest

from agent import modelos
from agent.modelos import ErrorModelo, Modelos


class Respuesta:
    def __init__(self, estado: int, cuerpo: dict):
        self.status_code, self._cuerpo = estado, cuerpo

    def json(self) -> dict:
        return self._cuerpo


OK = Respuesta(200, {"choices": [{"message": {"content": "hola"}}], "usage": {"cost": 0.001}})


class ClienteFalso:
    """Devuelve, en orden, las respuestas del guion. Una excepción en el guion se lanza."""

    def __init__(self, guion: list):
        self.guion, self.pedidos, self.tiempos = list(guion), [], []

    async def post(self, url, json, headers, timeout=None):
        self.tiempos.append(timeout)
        self.pedidos.append(json)
        paso = self.guion.pop(0)
        if isinstance(paso, Exception):
            raise paso
        return paso

    async def aclose(self):
        pass


@pytest.fixture
def esperas(monkeypatch):
    """Sustituye las esperas reales y deja ver cuánto se habría esperado."""
    hechas: list[float] = []

    async def dormir(segundos):
        hechas.append(segundos)

    monkeypatch.setattr(modelos.asyncio, "sleep", dormir)
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("AGENT_MODEL", "barato")
    monkeypatch.setenv("SUPERVISOR_MODEL", "fuerte")
    return hechas


def _chat(guion, modelo="barato", limite=None, **extra):
    cliente = Modelos(limite=limite)
    cliente._http = ClienteFalso(guion)
    return cliente, asyncio.run(cliente.chat(modelo, [{"role": "user", "content": "x"}], **extra))


def test_two_transient_failures_then_an_answer(esperas):
    cliente, mensaje = _chat([Respuesta(502, {}), httpx.ReadTimeout("lento"), OK])
    assert mensaje["content"] == "hola"
    assert esperas == [1, 3]  # esperó entre intentos, cada vez más
    assert cliente.uso.llamadas == 1 and cliente.uso.costo == 0.001


def test_a_200_with_an_error_body_is_retried(esperas):
    _, mensaje = _chat([Respuesta(200, {"error": {"message": "Provider returned error"}}), OK])
    assert mensaje["content"] == "hola" and esperas == [1]


def test_falls_back_to_the_other_model_without_forcing_a_tool(esperas):
    caido = Respuesta(503, {"error": {"message": "overloaded"}})
    cliente, mensaje = _chat([caido, caido, caido, caido, OK], tool_choice="required")
    assert mensaje["content"] == "hola" and esperas == [1, 3, 8]
    ultimo = cliente._http.pedidos[-1]
    assert ultimo["model"] == "fuerte" and "tool_choice" not in ultimo
    assert all(p["model"] == "barato" and p["tool_choice"] == "required" for p in cliente._http.pedidos[:-1])


def test_a_permanent_error_is_not_retried(esperas):
    with pytest.raises(ErrorModelo, match="401"):
        _chat([Respuesta(401, {"error": {"message": "No auth"}})])
    assert esperas == []


def test_gives_up_with_the_detail_when_both_models_fail(esperas):
    caido = Respuesta(529, {})
    with pytest.raises(ErrorModelo, match="respaldo con fuerte"):
        _chat([caido] * 5)


def test_retries_stop_when_the_task_clock_is_about_to_run_out(esperas):
    """Con 30 s de tarea por delante no hay sitio para esperar 8 s y volver a pedir: falla limpio."""
    import time

    caido = Respuesta(503, {})
    cliente = Modelos(limite=time.monotonic() + 30)  # menos el margen de 20 s: quedan ~10 s útiles
    cliente._http = ClienteFalso([caido] * 6)
    with pytest.raises(ErrorModelo):
        asyncio.run(cliente.chat("barato", [{"role": "user", "content": "x"}]))
    assert esperas == [1, 3]  # no llegó a esperar 8 s
    assert all(t <= 10.5 for t in cliente._http.tiempos)  # ninguna petición pidió más tiempo del que quedaba


def test_one_call_never_spends_more_than_its_total_budget(esperas, monkeypatch):
    """Si cada intento se cuelga hasta su límite, la llamada entera no pasa de TOPE_LLAMADA + el respaldo."""
    reloj = {"t": 1000.0}
    monkeypatch.setattr(modelos.time, "monotonic", lambda: reloj["t"])

    class Colgado(ClienteFalso):
        async def post(self, url, json, headers, timeout=None):
            reloj["t"] += timeout  # la petición consume todo el tiempo que se le dio
            self.pedidos.append(json)
            raise httpx.ReadTimeout("colgado")

    async def dormir(segundos):
        esperas.append(segundos)
        reloj["t"] += segundos

    monkeypatch.setattr(modelos.asyncio, "sleep", dormir)
    cliente = Modelos()
    cliente._http = Colgado([])
    with pytest.raises(ErrorModelo):
        asyncio.run(cliente.chat("barato", [{"role": "user", "content": "x"}]))
    gastado = reloj["t"] - 1000.0
    assert gastado <= modelos.TOPE_LLAMADA + modelos.TIEMPO_PETICION + 1, gastado
    assert [p["model"] for p in cliente._http.pedidos].count("barato") == 2  # 45 s + 1 s + lo que quedaba
