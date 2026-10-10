"""Si el servidor VNC corta la conexión a mitad de una tarea, el cliente reconecta y sigue."""

import asyncio

import pytest
import websockets

from agent.rfb import ClienteRFB, ErrorRFB


class FakeWs:
    def __init__(self, caida: bool = False):
        self.caida = caida
        self.enviados: list[bytes] = []

    async def send(self, datos: bytes) -> None:
        if self.caida:
            raise websockets.ConnectionClosedError(None, None)
        self.enviados.append(datos)

    async def close(self) -> None:
        pass


def _cliente(nuevo: FakeWs | None):
    vnc = ClienteRFB("ws://prueba/websockify", "x")
    vnc._ws = FakeWs(caida=True)
    vnc.conexiones = 0

    async def conectar(timeout: float = 20.0) -> None:
        vnc.conexiones += 1
        if nuevo is None:
            raise OSError("el escritorio no responde")
        vnc._ws = nuevo

    vnc.conectar = conectar
    return vnc


def test_a_dropped_connection_is_reopened_and_the_message_is_sent_again():
    nuevo = FakeWs()
    vnc = _cliente(nuevo)
    asyncio.run(vnc._enviar(b"clic"))
    assert nuevo.enviados == [b"clic"] and vnc.reconexiones == 1 and not vnc.cerrado


def test_a_session_the_reader_saw_closed_is_reopened_before_sending():
    nuevo = FakeWs()
    vnc = _cliente(nuevo)
    vnc.cerrado = True  # lo marca el lector cuando el servidor corta
    asyncio.run(vnc._enviar(b"tecla"))
    assert nuevo.enviados == [b"tecla"] and vnc.conexiones == 1


def test_if_the_desktop_does_not_come_back_the_task_fails_as_before():
    vnc = _cliente(None)
    with pytest.raises(ErrorRFB):
        asyncio.run(vnc._reconectar(intentos=2, espera=0))


def test_a_session_closed_on_purpose_is_not_reopened():
    vnc = _cliente(FakeWs())
    asyncio.run(vnc.cerrar())
    with pytest.raises(websockets.ConnectionClosed):
        asyncio.run(vnc._enviar(b"clic"))
    assert vnc.conexiones == 0
