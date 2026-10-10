"""`leer_lista` no corta la paginación porque una captura salió a medio dibujar."""

import asyncio

from PIL import Image, ImageDraw

from agent.escritorio import Escritorio

SIGUIENTE = (207, 684)
PAGINAS = 4


class FakeVnc:
    """Una lista de cuatro páginas. Tras cada clic, la primera captura sale con un resaltado sin borrar."""

    def __init__(self, sucias: int, al_inicio: int = 0):
        self.pagina = 1
        self.sucias = sucias  # capturas a medio dibujar después de cada cambio de página
        self.pendientes = al_inicio  # capturas a medio dibujar antes del primer clic

    def captura(self) -> Image.Image:
        imagen = Image.new("RGB", (640, 720), "white")
        dibujo = ImageDraw.Draw(imagen)
        activo = self.pagina < PAGINAS
        dibujo.rectangle((164, 674, 252, 695), fill=(240, 240, 240), outline=(120, 120, 120))
        dibujo.line((180, 684, 236, 684), fill=(0, 0, 0) if activo else (170, 170, 170), width=3)
        if self.pendientes:
            self.pendientes -= 1
            dibujo.rectangle((164, 674, 252, 695), fill=(50, 100, 220))
        return imagen

    async def clic(self, x, y, veces=1, boton=1):
        if self.pagina < PAGINAS:
            self.pagina += 1
            self.pendientes = self.sucias

    async def asentar(self, **_):
        return True


class EscritorioDePrueba(Escritorio):
    async def leer_pantalla(self) -> str | None:
        pagina = self.vnc.pagina
        return "Código\tEstado\n" + "\n".join(f"F-{pagina}{n}\tAbierta" for n in range(3))


def _leer(sucias: int, al_inicio: int = 0):
    esc = EscritorioDePrueba(FakeVnc(sucias, al_inicio))
    return asyncio.run(esc.leer_lista(SIGUIENTE))


def test_reads_every_page():
    tabla, motivo = _leer(sucias=0)
    assert tabla.paginas == PAGINAS and len(tabla.filas) == 12 and "ya no está activo" in motivo


def test_a_half_drawn_capture_does_not_cut_the_list_short():
    tabla, _ = _leer(sucias=2)
    assert tabla.paginas == PAGINAS and len(tabla.filas) == 12


def test_a_half_drawn_template_does_not_cut_the_list_short():
    """Si el control se captura a medio dibujar al empezar, la plantilla no coincide nunca más."""
    tabla, _ = _leer(sucias=0, al_inicio=1)
    assert tabla.paginas == PAGINAS and len(tabla.filas) == 12
