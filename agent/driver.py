"""Driver de pantalla que cumple el contrato `Driver` sobre el cliente RFB propio."""

from PIL import Image

from .escritorio import PAUSA_TECLA, Escritorio
from .rfb import ClienteRFB


class DriverRFB:
    def __init__(self):
        self.vnc: ClienteRFB | None = None
        self.esc: Escritorio | None = None

    async def connect(self, url: str, password: str) -> None:
        self.vnc = ClienteRFB(url, password)
        await self.vnc.conectar()
        self.esc = Escritorio(self.vnc)

    async def screenshot(self) -> Image.Image:
        return await self.esc.captura()

    async def click(self, x: int, y: int, double: bool = False) -> None:
        await self.vnc.clic(x, y, veces=2 if double else 1)

    async def type_text(self, text: str) -> None:
        await self.vnc.escribir(text, pausa=PAUSA_TECLA)

    async def key(self, combo: str) -> None:
        await self.vnc.pulsar(combo)

    async def scroll(self, x: int, y: int, direction: str, clicks: int = 3) -> None:
        await self.vnc.rueda(x, y, clicks if direction == "down" else -clicks)

    async def settle(self) -> bool:
        return await self.vnc.asentar()

    async def close(self) -> None:
        if self.vnc is not None:
            await self.vnc.cerrar()
