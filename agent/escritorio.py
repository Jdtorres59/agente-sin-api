"""Operaciones de escritorio de alto nivel sobre el cliente RFB.

Aquí viven las garantías que no dependen del modelo: esperar el repintado, seleccionar todo
antes de escribir, releer lo escrito y distinguir un portapapeles vacío de uno viejo.
"""

import asyncio

from PIL import Image, ImageChops

from .rfb import ClienteRFB
from .tablas import Tabla

PAUSA_TECLA = 0.08  # por debajo de esto el servidor pierde caracteres acentuados


class Escritorio:
    def __init__(self, vnc: ClienteRFB):
        self.vnc = vnc

    @property
    def tamano(self) -> tuple[int, int]:
        return self.vnc.ancho, self.vnc.alto

    async def captura(self) -> Image.Image:
        await self.vnc.asentar(esperar_cambio=0.25)
        return self.vnc.captura()

    async def _actuar(self, accion) -> bool:
        """Ejecuta la acción, espera el repintado y dice si la pantalla cambió."""
        antes = self.vnc.captura()
        await accion
        await self.vnc.asentar()
        return ImageChops.difference(antes, self.vnc.captura()).getbbox() is not None

    async def clic(self, x: int, y: int, veces: int = 1) -> bool:
        return await self._actuar(self.vnc.clic(x, y, veces=veces))

    async def tecla(self, combinacion: str) -> bool:
        return await self._actuar(self.vnc.pulsar(combinacion))

    async def rueda(self, x: int, y: int, pasos: int) -> bool:
        return await self._actuar(self.vnc.rueda(x, y, pasos))

    async def escribir(self, texto: str) -> bool:
        return await self._actuar(self.vnc.escribir(texto, pausa=PAUSA_TECLA))

    async def _esperar_texto(self, espera: float) -> str | None:
        reloj = asyncio.get_running_loop().time
        fin = reloj() + espera
        while reloj() < fin:
            if self.vnc.portapapeles is not None:
                return self.vnc.portapapeles
            await asyncio.sleep(0.03)
        return None

    async def leer_seleccion(self, espera: float = 1.5) -> str | None:
        """Selecciona todo donde esté el foco y devuelve el texto exacto. None si no hay nada que seleccionar.

        El servidor manda el texto en cuanto se selecciona, pero no reenvía uno igual al último que
        mandó. Si no llega nada, se encoge la selección un carácter y se restaura: eso lo obliga a
        mandar primero el texto recortado y luego el completo.
        """
        self.vnc.portapapeles = None
        await self.vnc.pulsar("ctrl+a")
        await asyncio.sleep(0.1)
        await self.vnc.pulsar("ctrl+c")
        texto = await self._esperar_texto(espera)
        if texto is not None:
            return texto
        self.vnc.portapapeles = None
        await self.vnc.pulsar("shift+left")
        recortado = await self._esperar_texto(espera)
        if recortado is None:
            return None
        self.vnc.portapapeles = None
        await self.vnc.pulsar("shift+right")
        completo = await self._esperar_texto(espera)
        return completo if completo is not None else recortado

    async def leer_campo(self, x: int, y: int) -> str | None:
        """Texto exacto del campo de texto en (x, y). None si está vacío o no se pudo leer."""
        await self.vnc.clic(x, y)
        await asyncio.sleep(0.25)
        texto = await self.leer_seleccion()
        await self.vnc.pulsar("end")  # quita la selección sin cambiar el contenido
        await self.vnc.asentar(esperar_cambio=0.3)
        return texto

    async def rellenar(self, x: int, y: int, texto: str) -> str | None:
        """Reemplaza el contenido del campo en (x, y) y devuelve lo que quedó escrito (None: no se pudo releer)."""
        quedo = None
        for _ in range(2):
            await self.vnc.clic(x, y)
            await asyncio.sleep(0.25)
            await self.vnc.pulsar("ctrl+a")
            await asyncio.sleep(0.1)
            if texto:
                await self.vnc.escribir(texto, pausa=PAUSA_TECLA)
            else:
                await self.vnc.pulsar("backspace")
            await asyncio.sleep(0.2)
            quedo = await self.leer_seleccion()
            await self.vnc.pulsar("end")
            if quedo == texto or (not texto and quedo is None):
                break
        await self.vnc.asentar(esperar_cambio=0.3)
        return quedo

    def punto_vacio(self) -> tuple[int, int] | None:
        """Centro de un bloque de color uniforme, lejos de los bordes: un sitio seguro para hacer clic."""
        imagen = self.vnc.captura()
        ancho, alto = imagen.size
        lado = 48
        for y in range(alto - lado - 20, alto // 3, -lado):
            for x in range(ancho - lado - 20, ancho // 3, -lado):
                extremos = imagen.crop((x, y, x + lado, y + lado)).getextrema()
                if all(minimo == maximo for minimo, maximo in extremos):
                    return x + lado // 2, y + lado // 2
        return None

    def _ubicar(self, plantilla: Image.Image, x: int, medio_alto: int) -> int | None:
        """Busca la plantilla en la misma columna de la pantalla. Devuelve la y de su centro."""
        imagen = self.vnc.captura()
        ancho, alto = plantilla.size
        izquierda = x - ancho // 2
        for arriba in range(0, imagen.height - alto):
            recorte = imagen.crop((izquierda, arriba, izquierda + ancho, arriba + alto))
            if ImageChops.difference(recorte, plantilla).getbbox() is None:
                return arriba + medio_alto
        return None

    async def leer_lista(self, siguiente: tuple[int, int] | None, max_paginas: int = 40) -> tuple[Tabla, str]:
        """Lee la tabla en pantalla recorriendo sus páginas con el control de página siguiente.

        El control se reconoce por su aspecto, así que puede moverse verticalmente entre páginas.
        Se detiene cuando el control ya no se ve igual (deshabilitado o ausente) o la página no cambia.
        """
        tabla = Tabla()
        texto = await self.leer_pantalla()
        if texto is None:
            return tabla, "no se pudo leer el texto de la pantalla"
        tabla.agregar_pagina(texto)
        if siguiente is None:
            return tabla, "sin paginación indicada"
        x, y = siguiente
        medio_ancho, medio_alto = 22, 8
        # leer_pantalla deja el ratón en una zona vacía: el control se captura sin resaltado.
        plantilla = self.vnc.captura().crop((x - medio_ancho, y - medio_alto, x + medio_ancho, y + medio_alto))
        motivo = f"se alcanzó el máximo de {max_paginas} páginas"
        for _ in range(max_paginas - 1):
            donde = self._ubicar(plantilla, x, medio_alto)
            if donde is None:
                motivo = "el control de página siguiente ya no está activo"
                break
            await self.vnc.clic(x, donde)
            await self.vnc.asentar()
            nuevo = await self.leer_pantalla()
            if nuevo is None or nuevo == texto:
                motivo = "la página dejó de cambiar"
                break
            texto = nuevo
            if tabla.agregar_pagina(texto) == 0:
                motivo = "la pantalla dejó de mostrar filas"
                break
        return tabla, motivo

    async def leer_pantalla(self) -> str | None:
        """Texto exacto de toda la pantalla (clic en zona vacía, seleccionar todo, copiar).

        Efectos: cierra menús abiertos y quita el foco del campo activo. No cambia datos.
        """
        for _ in range(2):
            punto = self.punto_vacio()
            if punto is None:
                return None
            await self.vnc.clic(*punto)
            await asyncio.sleep(0.3)
            texto = await self.leer_seleccion()
            await self.vnc.clic(*punto)  # quita la selección
            await self.vnc.asentar(esperar_cambio=0.4)
            if texto is not None:
                return texto
        return None
