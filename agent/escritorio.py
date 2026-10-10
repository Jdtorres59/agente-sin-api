"""Operaciones de escritorio de alto nivel sobre el cliente RFB.

Aquí viven las garantías que no dependen del modelo: esperar el repintado, seleccionar todo
antes de escribir, releer lo escrito y distinguir un portapapeles vacío de uno viejo.
"""

import asyncio

import numpy as np
from PIL import Image, ImageChops

from .rfb import ClienteRFB
from .tablas import Tabla

PAUSA_TECLA = 0.08  # por debajo de esto el servidor pierde caracteres acentuados


def _racha(marcas: np.ndarray, centro: int, holgura: int = 2) -> int:
    """Longitud de la racha más larga de True que pasa a `holgura` posiciones o menos de `centro`."""
    mejor, inicio = 0, None
    for i, marca in enumerate(list(marcas) + [False]):
        if marca and inicio is None:
            inicio = i
        elif not marca and inicio is not None:
            if inicio - holgura <= centro <= i - 1 + holgura:
                mejor = max(mejor, i - inicio)
            inicio = None
    return mejor


def cerca_de_borde(imagen: Image.Image, x: int, y: int, margen: int = 5, largo: int = 18, contraste: int = 28) -> bool:
    """True si (x, y) está pegado al borde de un control.

    Un borde es una línea recta de al menos `largo` píxeles con salto de brillo, a `margen` píxeles
    o menos del punto y que pasa a su lado. Los trazos de las letras y el cursor de texto son más
    cortos y no cuentan.
    Un clic ahí puede caer en el control vecino, así que es mejor pedirle al modelo que apunte al centro.
    """
    if not (0 <= x < imagen.width and 0 <= y < imagen.height):
        return False
    radio = 26
    izquierda, arriba = max(0, x - radio), max(0, y - radio)
    recorte = imagen.crop((izquierda, arriba, min(imagen.width, x + radio + 1), min(imagen.height, y + radio + 1)))
    gris = np.asarray(recorte.convert("L"), dtype=np.int16)
    cx, cy = x - izquierda, y - arriba
    saltos_h = np.abs(np.diff(gris, axis=1)) > contraste  # bordes verticales: entre la columna j y la j+1
    saltos_v = np.abs(np.diff(gris, axis=0)) > contraste  # bordes horizontales: entre la fila i y la i+1
    for j in range(max(0, cx - margen), min(saltos_h.shape[1], cx + margen)):
        if _racha(saltos_h[:, j], cy) >= largo:
            return True
    for i in range(max(0, cy - margen), min(saltos_v.shape[0], cy + margen)):
        if _racha(saltos_v[i, :], cx) >= largo:
            return True
    return False


def dentro_de_control(imagen: Image.Image, x: int, y: int, alto: int = 24, ancho: int = 160, contraste: int = 28) -> bool:
    """True si (x, y) cae dentro de un control con borde: un botón, un campo o una lista cerrada.

    Busca un borde recto arriba, abajo, a la izquierda y a la derecha del punto, cerca de él. Los
    menús, los enlaces y las opciones de una lista no tienen esa caja alrededor.
    """
    if not (0 <= x < imagen.width and 0 <= y < imagen.height):
        return False
    izquierda, arriba = max(0, x - ancho), max(0, y - alto)
    recorte = imagen.crop((izquierda, arriba, min(imagen.width, x + ancho + 1), min(imagen.height, y + alto + 1)))
    gris = np.asarray(recorte.convert("L"), dtype=np.int16)
    cx, cy = x - izquierda, y - arriba
    filas = np.abs(np.diff(gris, axis=0)) > contraste  # bordes horizontales
    columnas = np.abs(np.diff(gris, axis=1)) > contraste  # bordes verticales
    return (
        any(_racha(filas[i, :], cx) >= 18 for i in range(0, max(0, cy - 2)))
        and any(_racha(filas[i, :], cx) >= 18 for i in range(cy + 2, filas.shape[0]))
        and any(_racha(columnas[:, j], cy) >= 14 for j in range(0, max(0, cx - 2)))
        and any(_racha(columnas[:, j], cy) >= 14 for j in range(cx + 2, columnas.shape[1]))
    )


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
        return await self._forzar_envio(espera)

    async def leer_campo(self, x: int, y: int) -> str | None:
        """Texto exacto del campo de texto en (x, y). None si está vacío o no se pudo leer."""
        await self.vnc.clic(x, y)
        await asyncio.sleep(0.25)
        texto = await self.leer_seleccion()
        await self.vnc.pulsar("end")  # quita la selección sin cambiar el contenido
        await self.vnc.asentar(esperar_cambio=0.3)
        return texto

    def _seleccion_de_pagina(self, antes: Image.Image) -> bool:
        """True si al seleccionar todo se resaltó media pantalla: el foco no estaba en un campo."""
        caja = ImageChops.difference(antes, self.vnc.captura()).getbbox()
        if caja is None:
            return False
        ancho, alto = self.tamano
        return (caja[2] - caja[0]) > 0.5 * ancho and (caja[3] - caja[1]) > 0.3 * alto

    def _hubo_resaltado(self, antes: Image.Image) -> bool:
        """True si al seleccionar apareció un resaltado más ancho que un cursor de texto."""
        caja = ImageChops.difference(antes, self.vnc.captura()).getbbox()
        return caja is not None and (caja[2] - caja[0]) >= 6

    async def _forzar_envio(self, espera: float = 1.5) -> str | None:
        """Con todo seleccionado: encoge la selección un carácter y la restaura para leerla completa."""
        self.vnc.portapapeles = None
        await self.vnc.pulsar("shift+left")
        recortado = await self._esperar_texto(espera)
        if recortado is None:
            return None
        self.vnc.portapapeles = None
        await self.vnc.pulsar("shift+right")
        completo = await self._esperar_texto(espera)
        return completo if completo is not None else recortado

    @staticmethod
    def fuera_de_campo(leido: str | None, texto: str) -> bool:
        """Lo releído no puede ser el contenido de un campo donde se acaba de escribir `texto`."""
        if not leido:
            return False
        return ("\n" in leido and "\n" not in texto) or "\t" in leido or len(leido) > 3 * len(texto) + 20

    async def rellenar_con_anterior(self, x: int, y: int, texto: str) -> tuple[str | None, str | None, bool]:
        """Reemplaza el contenido del campo en (x, y).

        Devuelve (lo que había, lo que quedó, si el punto era un campo de texto). Lo que había es
        None si el campo estaba vacío o no se pudo leer. Si el punto no era un campo, no se escribe.
        """
        anterior = quedo = None
        for intento in range(2):
            await self.vnc.clic(x, y)
            await asyncio.sleep(0.25)
            antes = self.vnc.captura()
            self.vnc.portapapeles = None
            await self.vnc.pulsar("ctrl+a")
            await self.vnc.asentar(quieto=0.25, esperar_cambio=0.6, maximo=1.5)
            if self._seleccion_de_pagina(antes):
                punto = self.punto_vacio()
                if punto is not None:
                    await self.vnc.clic(*punto)  # quita la selección de toda la página
                await self.vnc.asentar(esperar_cambio=0.4)
                return None, None, False
            if intento == 0:
                # Seleccionar ya hizo que el servidor mandara el texto que había en el campo.
                anterior = await self._esperar_texto(0.9)
                if anterior is None and self._hubo_resaltado(antes):
                    # Se resaltó texto pero no llegó: es igual al último que mandó el servidor.
                    # Encoger y restaurar la selección lo obliga a mandarlo (ver leer_seleccion).
                    anterior = await self._forzar_envio()
                    await self.vnc.pulsar("ctrl+a")  # deja todo seleccionado antes de escribir
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
        if self.fuera_de_campo(quedo, texto):
            return anterior, quedo, False
        return anterior, quedo, True

    async def rellenar(self, x: int, y: int, texto: str) -> str | None:
        """Reemplaza el contenido del campo en (x, y) y devuelve lo que quedó escrito (None: no se pudo releer)."""
        _, quedo, en_campo = await self.rellenar_con_anterior(x, y, texto)
        return quedo if en_campo else None

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

    async def _recorte_estable(self, caja: tuple[int, int, int, int], intentos: int = 4) -> Image.Image:
        """Recorte de la pantalla tomado dos veces seguidas con el mismo resultado.

        Si la plantilla se captura a medio dibujar (un resaltado que aún no se borra), no vuelve a
        coincidir con nada y la lectura se corta en la segunda página sin que nadie lo note.
        """
        recorte = self.vnc.captura().crop(caja)
        for _ in range(intentos):
            await self.vnc.asentar(quieto=0.35, esperar_cambio=0.6, maximo=2.0)
            otro = self.vnc.captura().crop(caja)
            if ImageChops.difference(recorte, otro).getbbox() is None:
                break
            recorte = otro
        return recorte

    async def _ubicar_sin_prisa(self, plantilla: Image.Image, x: int, medio_alto: int, intentos: int = 4) -> int | None:
        """Como `_ubicar`, pero no da el control por perdido con una sola captura.

        La pantalla puede estar a medio dibujar (un resaltado que todavía no se borra) y el control
        seguir ahí: concluir que no está cortaría la lectura y el conteo saldría corto sin avisar.
        """
        for _ in range(intentos):
            donde = self._ubicar(plantilla, x, medio_alto)
            if donde is not None:
                return donde
            await self.vnc.asentar(quieto=0.3, esperar_cambio=0.6, maximo=1.5)
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
        plantilla = await self._recorte_estable((x - medio_ancho, y - medio_alto, x + medio_ancho, y + medio_alto))
        motivo = f"se alcanzó el máximo de {max_paginas} páginas"
        vistas = {texto}  # una página que vuelve a salir significa que el control no avanza
        for _ in range(max_paginas - 1):
            donde = await self._ubicar_sin_prisa(plantilla, x, medio_alto)
            if donde is None:
                motivo = "el control de página siguiente ya no está activo"
                break
            await self.vnc.clic(x, donde)
            await self.vnc.asentar()
            nuevo = await self.leer_pantalla()
            if nuevo is None or nuevo == texto:
                motivo = "la página dejó de cambiar"
                break
            if nuevo in vistas:
                motivo = "volvió a salir una página ya leída: ese punto no es el control de página siguiente"
                break
            vistas.add(nuevo)
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
