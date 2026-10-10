"""Cliente RFB (VNC) mínimo sobre WebSocket: framebuffer, ratón, teclado y portapapeles.

No sabe nada de la app que hay detrás. Los tiempos de pulsación y de espera de repintado
son parámetros, no constantes del protocolo.
"""

import asyncio
import struct
import zlib

import websockets
from Crypto.Cipher import DES
from PIL import Image

RAW, ZLIB, DESKTOP_SIZE, CURSOR = 0, 6, -223, -239

TECLAS = {
    "enter": 0xFF0D, "tab": 0xFF09, "escape": 0xFF1B, "backspace": 0xFF08, "delete": 0xFFFF,
    "home": 0xFF50, "end": 0xFF57, "left": 0xFF51, "up": 0xFF52, "right": 0xFF53, "down": 0xFF54,
    "pageup": 0xFF55, "pagedown": 0xFF56, "space": 0x20,
    "shift": 0xFFE1, "ctrl": 0xFFE3, "alt": 0xFFE9,
    **{f"f{i}": 0xFFBD + i for i in range(1, 13)},
}  # fmt: skip


class ErrorRFB(Exception):
    pass


def keysym(caracter: str) -> int:
    """Keysym X11 de un carácter: Latin-1 directo, el resto por Unicode."""
    if caracter in ("\n", "\r"):
        return TECLAS["enter"]
    if caracter == "\t":
        return TECLAS["tab"]
    codigo = ord(caracter)
    return codigo if 0x20 <= codigo <= 0xFF else 0x01000000 | codigo


def _respuesta_des(password: str, reto: bytes) -> bytes:
    """Autenticación VNC: DES del reto con la contraseña, con los bits de cada byte invertidos."""
    clave = (password.encode("latin-1", "replace") + b"\0" * 8)[:8]
    clave = bytes(int(f"{b:08b}"[::-1], 2) for b in clave)
    return DES.new(clave, DES.MODE_ECB).encrypt(reto)


class _Flujo:
    """Convierte los mensajes binarios del WebSocket en un flujo de bytes."""

    def __init__(self, ws):
        self.ws = ws
        self.buf = bytearray()

    async def leer(self, n: int) -> bytes:
        while len(self.buf) < n:
            mensaje = await self.ws.recv()
            self.buf += mensaje.encode("latin-1") if isinstance(mensaje, str) else mensaje
        datos = bytes(self.buf[:n])
        del self.buf[:n]
        return datos


class ClienteRFB:
    def __init__(self, url: str, password: str, pulsacion: float = 0.18):
        self.url = url
        self._password = password
        self.pulsacion = pulsacion
        self.ancho = self.alto = 0
        self.nombre = ""
        self.imagen: Image.Image | None = None
        self.portapapeles: str | None = None
        self.actualizaciones = 0  # cuántas han llegado
        self.area_ultima = 0  # píxeles de la última actualización
        self.cerrado = False
        self._pendiente = False
        self._z = zlib.decompressobj()
        self._ws = None
        self._flujo: _Flujo | None = None
        self._lector: asyncio.Task | None = None

    # --- conexión -------------------------------------------------------------------------

    async def conectar(self, timeout: float = 20.0) -> None:
        self._ws = await websockets.connect(
            self.url, subprotocols=["binary"], max_size=None, open_timeout=timeout, ping_interval=None
        )
        self._flujo = f = _Flujo(self._ws)
        await asyncio.wait_for(self._saludo(f), timeout)
        self.imagen = Image.new("RGB", (self.ancho, self.alto))
        self._lector = asyncio.create_task(self._leer_mensajes())
        await self._pedir(incremental=False)
        await self._esperar_actualizacion(timeout)

    async def _saludo(self, f: _Flujo) -> None:
        version = await f.leer(12)
        if not version.startswith(b"RFB "):
            raise ErrorRFB(f"El servidor no habla RFB: {version!r}")
        menor = int(version[8:11])
        await self._ws.send(b"RFB 003.008\n" if menor >= 8 else version)

        if menor >= 7:
            n = (await f.leer(1))[0]
            if n == 0:
                raise ErrorRFB(await self._razon(f))
            tipos = await f.leer(n)
            tipo = 2 if 2 in tipos else 1 if 1 in tipos else None
            if tipo is None:
                raise ErrorRFB(f"Tipos de seguridad no soportados: {list(tipos)}")
            await self._ws.send(bytes([tipo]))
        else:
            (tipo,) = struct.unpack(">I", await f.leer(4))
            if tipo == 0:
                raise ErrorRFB(await self._razon(f))

        if tipo == 2:
            await self._ws.send(_respuesta_des(self._password, await f.leer(16)))
        if tipo == 2 or menor >= 8:
            (resultado,) = struct.unpack(">I", await f.leer(4))
            if resultado != 0:
                razon = await self._razon(f) if menor >= 8 else "autenticación rechazada"
                raise ErrorRFB(f"Autenticación VNC fallida: {razon}")

        await self._ws.send(b"\x01")  # sesión compartida
        self.ancho, self.alto = struct.unpack(">HH", await f.leer(4))
        await f.leer(16)  # formato de píxel del servidor; se reemplaza abajo
        (n,) = struct.unpack(">I", await f.leer(4))
        self.nombre = (await f.leer(n)).decode("utf-8", "replace")

        # 32 bpp, little-endian, rojo en el bit 16: en el cable llega como B, G, R, relleno.
        formato = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        await self._ws.send(struct.pack(">B3x", 0) + formato)
        # CURSOR: el servidor manda la forma del puntero aparte y deja de dibujarlo en la pantalla,
        # así las capturas, las anclas y la búsqueda de controles no llevan la flecha encima.
        codificaciones = [ZLIB, RAW, DESKTOP_SIZE, CURSOR]
        await self._ws.send(struct.pack(f">BxH{len(codificaciones)}i", 2, len(codificaciones), *codificaciones))

    @staticmethod
    async def _razon(f: _Flujo) -> str:
        (n,) = struct.unpack(">I", await f.leer(4))
        return (await f.leer(n)).decode("utf-8", "replace")

    async def cerrar(self) -> None:
        self.cerrado = True
        if self._lector:
            self._lector.cancel()
        if self._ws:
            await self._ws.close()

    # --- mensajes del servidor ------------------------------------------------------------

    async def _leer_mensajes(self) -> None:
        f = self._flujo
        try:
            while True:
                tipo = (await f.leer(1))[0]
                if tipo == 0:
                    await self._actualizacion(f)
                elif tipo == 1:  # mapa de colores: no aplica en color verdadero
                    _, n = struct.unpack(">xHH", await f.leer(5))
                    await f.leer(n * 6)
                elif tipo == 2:  # campana
                    pass
                elif tipo == 3:  # texto copiado en el servidor
                    (n,) = struct.unpack(">3xI", await f.leer(7))
                    crudo = await f.leer(n)
                    try:  # el estándar dice Latin-1, pero los servidores actuales mandan UTF-8
                        self.portapapeles = crudo.decode("utf-8")
                    except UnicodeDecodeError:
                        self.portapapeles = crudo.decode("latin-1")
                else:
                    raise ErrorRFB(f"Mensaje del servidor no soportado: {tipo}")
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            pass
        finally:
            self.cerrado = True

    async def _actualizacion(self, f: _Flujo) -> None:
        (n,) = struct.unpack(">xH", await f.leer(3))
        area = 0
        for _ in range(n):
            x, y, w, h, codificacion = struct.unpack(">HHHHi", await f.leer(12))
            if codificacion == DESKTOP_SIZE:
                self.ancho, self.alto = w, h
                self.imagen = Image.new("RGB", (w, h))
                area += w * h
                continue
            if codificacion == CURSOR:  # forma del puntero: píxeles y máscara; no se dibuja
                await f.leer(w * h * 4 + ((w + 7) // 8) * h)
                continue
            if codificacion == RAW:
                datos = await f.leer(w * h * 4)
            elif codificacion == ZLIB:
                (largo,) = struct.unpack(">I", await f.leer(4))
                datos = self._z.decompress(await f.leer(largo))
            else:
                raise ErrorRFB(f"Codificación no soportada: {codificacion}")
            if w and h:
                self.imagen.paste(Image.frombuffer("RGB", (w, h), datos, "raw", "BGRX", 0, 1), (x, y))
                area += w * h
        self.area_ultima = area
        self._pendiente = False
        self.actualizaciones += 1

    async def _pedir(self, incremental: bool) -> None:
        if incremental and self._pendiente:
            return
        self._pendiente = True
        await self._ws.send(struct.pack(">BBHHHH", 3, int(incremental), 0, 0, self.ancho, self.alto))

    async def _esperar_actualizacion(self, timeout: float) -> None:
        inicial = self.actualizaciones
        reloj = asyncio.get_running_loop().time
        fin = reloj() + timeout
        while self.actualizaciones == inicial:
            if self.cerrado:
                raise ErrorRFB("La conexión VNC se cerró")
            if reloj() > fin:
                raise ErrorRFB("El servidor VNC no envió la pantalla a tiempo")
            await asyncio.sleep(0.02)

    # --- pantalla -------------------------------------------------------------------------

    async def asentar(
        self, quieto: float = 0.35, esperar_cambio: float = 1.3, maximo: float = 3.0, area_minima: int = 1500
    ) -> bool:
        """Espera a que la pantalla deje de cambiar. Devuelve si hubo algún cambio visible.

        Los cambios de menos de `area_minima` píxeles (un cursor que parpadea) se aplican a la
        imagen pero no reinician la espera.
        """
        reloj = asyncio.get_running_loop().time
        inicio = ultimo = reloj()
        vistas = self.actualizaciones
        hubo = False
        while not self.cerrado:
            ahora = reloj()
            if ahora - inicio >= maximo:
                break
            if hubo and ahora - ultimo >= quieto:
                break
            if not hubo and ahora - inicio >= esperar_cambio:
                break
            await self._pedir(incremental=True)
            await asyncio.sleep(0.04)
            if self.actualizaciones != vistas:
                vistas = self.actualizaciones
                if self.area_ultima >= area_minima:
                    hubo = True
                    ultimo = reloj()
        return hubo

    def captura(self) -> Image.Image:
        return self.imagen.copy()

    # --- ratón ----------------------------------------------------------------------------

    async def _puntero(self, x: int, y: int, botones: int) -> None:
        x = max(0, min(self.ancho - 1, int(x)))
        y = max(0, min(self.alto - 1, int(y)))
        await self._ws.send(struct.pack(">BBHH", 5, botones, x, y))

    async def mover(self, x: int, y: int) -> None:
        await self._puntero(x, y, 0)

    async def clic(self, x: int, y: int, veces: int = 1, boton: int = 1) -> None:
        mascara = 1 << (boton - 1)
        await self._puntero(x, y, 0)
        await asyncio.sleep(0.05)
        for i in range(veces):
            if i:
                await asyncio.sleep(0.09)
            await self._puntero(x, y, mascara)
            await asyncio.sleep(self.pulsacion)
            await self._puntero(x, y, 0)

    async def rueda(self, x: int, y: int, pasos: int) -> None:
        """pasos > 0 baja, pasos < 0 sube."""
        mascara = 16 if pasos > 0 else 8
        await self._puntero(x, y, 0)
        for _ in range(abs(pasos)):
            await self._puntero(x, y, mascara)
            await asyncio.sleep(0.03)
            await self._puntero(x, y, 0)
            await asyncio.sleep(0.05)

    # --- teclado --------------------------------------------------------------------------

    async def _tecla(self, simbolo: int, abajo: bool) -> None:
        await self._ws.send(struct.pack(">BBxxI", 4, int(abajo), simbolo))

    async def pulsar(self, combinacion: str) -> None:
        """Pulsa una tecla o combinación: "enter", "ctrl+a", "shift+tab"."""
        partes = [p.strip().lower() for p in combinacion.split("+")]
        simbolos = []
        for parte in partes:
            if parte in TECLAS:
                simbolos.append(TECLAS[parte])
            elif len(parte) == 1:
                simbolos.append(keysym(parte))
            else:
                raise ValueError(f"Tecla desconocida: {parte}")
        for simbolo in simbolos:
            await self._tecla(simbolo, True)
            await asyncio.sleep(0.03)
        await asyncio.sleep(0.05)
        for simbolo in reversed(simbolos):
            await self._tecla(simbolo, False)
            await asyncio.sleep(0.03)

    async def escribir(self, texto: str, pausa: float = 0.03) -> None:
        for caracter in texto:
            simbolo = keysym(caracter)
            await self._tecla(simbolo, True)
            await asyncio.sleep(0.015)
            await self._tecla(simbolo, False)
            await asyncio.sleep(pausa)

    async def fijar_portapapeles(self, texto: str) -> None:
        datos = texto.encode("latin-1", "replace")
        await self._ws.send(struct.pack(">B3xI", 6, len(datos)) + datos)
