"""rellenar_con_anterior lee el valor previo aunque el servidor no reenvíe un texto repetido."""

import asyncio

from PIL import Image, ImageDraw

from agent.escritorio import Escritorio


class VncSinRepetir:
    """Un campo de texto y un portapapeles que, como el del servidor real, no reenvía el mismo texto."""

    ancho, alto = 640, 480

    def __init__(self, texto: str, ultimo_enviado: str | None):
        self.texto = texto
        self.seleccion: str | None = None  # la parte del texto que está seleccionada
        self.ultimo_enviado = ultimo_enviado
        self.portapapeles: str | None = None

    def _enviar(self) -> None:
        if self.seleccion and self.seleccion != self.ultimo_enviado:
            self.portapapeles = self.ultimo_enviado = self.seleccion

    def captura(self) -> Image.Image:
        imagen = Image.new("RGB", (self.ancho, self.alto), "white")
        if self.seleccion:  # resaltado dentro del campo, no de toda la pantalla
            ImageDraw.Draw(imagen).rectangle((300, 190, 300 + 8 * len(self.seleccion), 210), fill=(60, 110, 220))
        return imagen

    async def clic(self, x, y, veces=1):
        self.seleccion = None

    async def asentar(self, **_):
        return True

    async def pulsar(self, combinacion: str):
        if combinacion == "ctrl+a":
            self.seleccion = self.texto or None
        elif combinacion == "shift+left" and self.seleccion:
            self.seleccion = self.seleccion[:-1] or None
        elif combinacion == "shift+right" and self.seleccion is not None:
            self.seleccion = self.texto
        elif combinacion == "backspace" and self.seleccion:
            self.texto, self.seleccion = self.texto.replace(self.seleccion, "", 1), None
        elif combinacion == "end":
            self.seleccion = None
        self._enviar()

    async def escribir(self, texto: str, pausa: float = 0.0):
        resto = self.texto[len(self.seleccion):] if self.seleccion else self.texto
        self.texto, self.seleccion = texto + resto, None


def test_previous_value_is_read_even_when_the_server_would_not_resend_it():
    vnc = VncSinRepetir("Carrera 15 # 85-40", ultimo_enviado="Carrera 15 # 85-40")  # el operador lo acaba de leer
    anterior, quedo, en_campo = asyncio.run(Escritorio(vnc).rellenar_con_anterior(400, 200, "Calle 80 # 24-15"))
    assert anterior == "Carrera 15 # 85-40"
    assert (quedo, en_campo) == ("Calle 80 # 24-15", True)
    assert vnc.texto == "Calle 80 # 24-15"  # no quedó ningún carácter del valor viejo


def test_an_empty_field_still_reads_as_empty():
    vnc = VncSinRepetir("", ultimo_enviado="otra cosa")
    anterior, quedo, en_campo = asyncio.run(Escritorio(vnc).rellenar_con_anterior(400, 200, "Pereira"))
    assert anterior is None and (quedo, en_campo) == ("Pereira", True)
