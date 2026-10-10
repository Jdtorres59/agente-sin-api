"""Bucle del operador: observar → decidir → actuar, con compuertas y verificación."""

import io
import json
import os
import re
import time
from dataclasses import dataclass, field

from PIL import Image, ImageChops

from . import prompts
from .contracts import Step
from .escritorio import Escritorio
from .modelos import Modelos, imagen_a_url
from .tablas import Tabla
from .traza import Traza

MAX_PASOS = 40
RESERVA_SEGUNDOS = 30  # margen para verificar y reportar antes del límite


def _herramienta(nombre: str, descripcion: str, propiedades: dict, requeridas: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": nombre,
            "description": descripcion,
            "parameters": {"type": "object", "properties": propiedades, "required": requeridas},
        },
    }


_XY = {"x": {"type": "integer"}, "y": {"type": "integer"}}
_CAMBIA = {
    "type": "boolean",
    "description": "true si esta acción guarda, crea, elimina, confirma, exporta o revierte algo.",
}
HERRAMIENTAS = [
    _herramienta(
        "clic",
        "Clic izquierdo en un punto.",
        {**_XY, "veces": {"type": "integer", "enum": [1, 2]}, "objetivo": {"type": "string"}, "cambia_datos": _CAMBIA},
        ["x", "y", "objetivo", "cambia_datos"],
    ),
    _herramienta(
        "rellenar",
        "Reemplaza el contenido de un campo de texto y devuelve lo que quedó escrito. No sirve para listas desplegables.",
        {**_XY, "texto": {"type": "string"}, "campo": {"type": "string"}},
        ["x", "y", "texto", "campo"],
    ),
    _herramienta(
        "escribir", "Escribe texto donde esté el foco, sin borrar antes.", {"texto": {"type": "string"}}, ["texto"]
    ),
    _herramienta(
        "tecla",
        'Pulsa una tecla o combinación: "enter", "tab", "escape", "down", "pagedown", "ctrl+a".',
        {"combinacion": {"type": "string"}, "cambia_datos": _CAMBIA},
        ["combinacion", "cambia_datos"],
    ),
    _herramienta(
        "rueda",
        "Gira la rueda del ratón sobre un punto. pasos > 0 baja, pasos < 0 sube.",
        {**_XY, "pasos": {"type": "integer"}},
        ["x", "y", "pasos"],
    ),
    _herramienta(
        "leer_pantalla",
        "Devuelve el texto exacto de toda la pantalla. Cierra menús abiertos y quita el foco; no cambia datos. "
        "No incluye el contenido de los campos de un formulario: para eso usa leer_campo.",
        {},
        [],
    ),
    _herramienta("leer_campo", "Devuelve el texto exacto de un campo de texto.", _XY, ["x", "y"]),
    _herramienta(
        "leer_lista",
        "Lee la tabla de la lista en pantalla recorriendo TODAS sus páginas y la guarda para `contar`. Llámala "
        "estando en la primera página, con los filtros ya aplicados. siguiente_x y siguiente_y son el centro, "
        "tal como se ve en la captura, del control que pasa a la página siguiente. Si la lista no tiene ese "
        "control, pon -1 en ambos.",
        {"siguiente_x": {"type": "integer"}, "siguiente_y": {"type": "integer"}},
        ["siguiente_x", "siguiente_y"],
    ),
    _herramienta(
        "contar",
        "Cuenta (y opcionalmente suma) filas de la última tabla leída con `leer_lista`. El conteo es exacto: "
        "úsalo siempre en vez de contar a mano. Sin condiciones cuenta todas las filas.",
        {
            "condiciones": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "columna": {"type": "string"},
                        "operador": {"type": "string", "enum": ["igual", "distinto", "contiene", "menor", "mayor"]},
                        "valor": {"type": "string", "description": "Texto, número o fecha (dd/mm/aaaa)."},
                    },
                    "required": ["columna", "operador", "valor"],
                },
            },
            "sumar": {"type": "string", "description": "Columna numérica a sumar (opcional)."},
        },
        ["condiciones"],
    ),
    _herramienta(
        "terminar",
        "Cierra la tarea. Úsala sola, después de verificar.",
        {
            "estado": {"type": "string", "enum": ["terminada", "fallida"]},
            "hechos": {"type": "string", "description": "Qué hiciste, con identificadores."},
            "respuesta": {"type": "string", "description": "Solo si la tarea es una pregunta: el valor."},
            "razon": {"type": "string", "description": "Solo si falla: los datos concretos que lo explican."},
        },
        ["estado", "hechos"],
    ),
]


@dataclass
class Cierre:
    estado: str
    hechos: str = ""
    respuesta: str = ""
    razon: str = ""
    pasos: int = 0
    confirmaciones: int = 0
    verificado: bool = False
    bitacora: list[str] = field(default_factory=list)


def _json(texto: str) -> dict:
    encontrado = re.search(r"\{.*\}", texto or "", re.S)
    try:
        return json.loads(encontrado.group(0)) if encontrado else {}
    except json.JSONDecodeError:
        return {}


class Operador:
    def __init__(
        self,
        tarea: str,
        escritorio: Escritorio,
        modelos: Modelos,
        traza: Traza,
        limite: float,
        spec: dict | None = None,
        notas: str = "",
        aviso_inicial: str = "",
    ):
        self.tarea = tarea
        self.esc = escritorio
        self.modelos = modelos
        self.traza = traza
        self.limite = limite  # instante (time.monotonic) en que hay que haber reportado
        self.spec = spec or {}
        self.notas = notas
        self.aviso_inicial = aviso_inicial
        self.steps: list[Step] = []  # una entrada por acción ejecutada; de aquí aprende la memoria
        self.modelo = os.environ.get("AGENT_MODEL", "anthropic/claude-haiku-5.5")
        self.supervisor = os.environ.get("SUPERVISOR_MODEL", "anthropic/claude-sonnet-5.5")
        self.bitacora: list[str] = []
        self.confirmaciones = 0
        self.tabla: Tabla | None = None
        self.evidencia = None  # captura tomada justo después del último cambio de datos

    def _quedan(self) -> float:
        return self.limite - time.monotonic()

    # --- supervisor ----------------------------------------------------------------------

    async def _consultar_supervisor(self, instrucciones: str, pregunta: str, con_evidencia: bool = False) -> dict:
        captura = await self.esc.captura()
        contexto = (
            f"TAREA: {self.tarea}\n\nREGISTRO DE ACCIONES (más reciente al final):\n"
            + "\n".join(self.bitacora[-40:])
            + f"\n\n{pregunta}"
        )
        imagenes = [{"type": "image_url", "image_url": {"url": imagen_a_url(captura)}}]
        if con_evidencia and self.evidencia is not None:
            contexto += (
                "\n\nIMAGEN 1: pantalla justo después del último cambio de datos (los avisos pueden ser pasajeros)."
                "\nIMAGEN 2: pantalla actual."
            )
            imagenes.insert(0, {"type": "image_url", "image_url": {"url": imagen_a_url(self.evidencia)}})
        mensaje = await self.modelos.chat(
            self.supervisor,
            [
                {"role": "system", "content": instrucciones},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": contexto}, *imagenes],
                },
            ],
            max_tokens=2000,
            reasoning={"effort": "medium"},
        )
        self.traza.evento(accion="supervisor", pregunta=pregunta[:300], respuesta=(mensaje.get("content") or "")[:600])
        contenido = mensaje.get("content") or ""
        # Si el supervisor no respondió JSON, su texto cuenta como objeción.
        return _json(contenido) or {"aprobar": False, "conforme": False, "motivo": contenido[:300], "problema": contenido[:300]}

    async def _aprobar_guardado(self, descripcion: str) -> tuple[bool, str]:
        veredicto = await self._consultar_supervisor(
            prompts.REVISOR_GUARDADO,
            f"Cambios ya aplicados en esta tarea: {self.confirmaciones}.\nACCIÓN PROPUESTA: {descripcion}",
        )
        return bool(veredicto.get("aprobar")), str(veredicto.get("motivo", "sin motivo"))

    async def _verificar(self, cierre: Cierre) -> tuple[bool, str]:
        veredicto = await self._consultar_supervisor(
            prompts.VERIFICADOR,
            f"EL OPERADOR REPORTA: estado={cierre.estado}; hechos={cierre.hechos}; "
            f"respuesta={cierre.respuesta or '-'}; razon={cierre.razon or '-'}",
            con_evidencia=True,
        )
        return bool(veredicto.get("conforme")), str(veredicto.get("problema", ""))

    # --- acciones ------------------------------------------------------------------------

    async def _ejecutar(self, nombre: str, a: dict) -> str:
        cambio = lambda hubo: "la pantalla cambió" if hubo else "la pantalla NO cambió (puede no haber tenido efecto)"  # noqa: E731
        if nombre in ("clic", "tecla") and a.get("cambia_datos"):
            descripcion = (
                f"clic en «{a.get('objetivo', '')}» ({a.get('x')}, {a.get('y')})"
                if nombre == "clic"
                else f"tecla {a.get('combinacion')}"
            )
            aprobado, motivo = await self._aprobar_guardado(descripcion)
            if not aprobado:
                return f"NO ejecutada. El revisor la rechazó: {motivo}"
            self.confirmaciones += 1
        if nombre in ("clic", "tecla") and a.get("cambia_datos"):
            if nombre == "clic":
                hubo = await self.esc.clic(a["x"], a["y"], veces=a.get("veces", 1))
            else:
                hubo = await self.esc.tecla(a["combinacion"])
            self.evidencia = await self.esc.captura()
            self.traza.captura(900 + self.confirmaciones, self.evidencia)
            return cambio(hubo)
        if nombre == "clic":
            return cambio(await self.esc.clic(a["x"], a["y"], veces=a.get("veces", 1)))
        if nombre == "rellenar":
            quedo = await self.esc.rellenar(a["x"], a["y"], a["texto"])
            aviso = "" if a["texto"] in self.tarea else " Aviso: ese texto no aparece literal en la tarea."
            if quedo is None and a["texto"]:
                return "no se pudo releer el campo (¿el punto está dentro de un campo de texto?). Míralo en la captura." + aviso
            estado = "coincide" if (quedo or "") == a["texto"] else "NO coincide con lo pedido"
            return f"el campo quedó con «{quedo or ''}» ({estado}).{aviso}"
        if nombre == "escribir":
            return cambio(await self.esc.escribir(a["texto"]))
        if nombre == "tecla":
            return cambio(await self.esc.tecla(a["combinacion"]))
        if nombre == "rueda":
            return cambio(await self.esc.rueda(a["x"], a["y"], a["pasos"]))
        if nombre == "leer_pantalla":
            texto = await self.esc.leer_pantalla()
            return "no se pudo leer el texto de la pantalla" if texto is None else f"TEXTO DE LA PANTALLA:\n{texto[:8000]}"
        if nombre == "leer_campo":
            leido = await self.esc.leer_campo(a["x"], a["y"])
            return "el campo está vacío o no es un campo de texto" if leido is None else f"el campo contiene «{leido}»"
        if nombre == "leer_lista":
            x, y = a.get("siguiente_x", -1), a.get("siguiente_y", -1)
            ancho, alto = self.esc.tamano
            if (x, y) != (-1, -1) and not (25 <= x <= ancho - 25 and 10 <= y <= alto - 10):
                return "coordenadas inválidas: da el centro real del control de página siguiente, o -1 y -1 si no hay"
            siguiente = None if (x, y) == (-1, -1) else (x, y)
            self.tabla, motivo = await self.esc.leer_lista(siguiente)
            if not self.tabla.filas:
                return f"no se encontraron filas de tabla ({motivo}). Usa leer_pantalla."
            return (
                f"{len(self.tabla.filas)} filas en {self.tabla.paginas} página(s); se detuvo porque {motivo}. "
                f"La pantalla quedó en la última página.\n{self.tabla.como_texto()}"
            )
        if nombre == "contar":
            if self.tabla is None:
                return "primero lee la tabla con leer_lista"
            filas = self.tabla.filtrar(a.get("condiciones") or [])
            texto = f"{len(filas)} filas cumplen (de {len(self.tabla.filas)})."
            if a.get("sumar"):
                texto += f" Suma de «{a['sumar']}»: {self.tabla.sumar(a['sumar'], filas):.2f}."
            if filas:
                texto += " Primera columna de las que cumplen: " + ", ".join(f[0] for f in filas[:80])
            return texto
        return f"herramienta desconocida: {nombre}"

    # --- registro de pasos para la memoria -----------------------------------------------

    @staticmethod
    def _ancla(imagen: Image.Image, x: int, y: int) -> bytes:
        """Recorte de 48x48 alrededor del punto, tomado antes de actuar."""
        buffer = io.BytesIO()
        imagen.crop((x - 24, y - 24, x + 24, y + 24)).save(buffer, format="PNG")
        return buffer.getvalue()

    @staticmethod
    def _pixeles_cambiados(antes: Image.Image, despues: Image.Image) -> int:
        if antes.size != despues.size:
            return antes.width * antes.height
        diferencia = ImageChops.difference(antes, despues).convert("L").point(lambda v: 255 if v > 30 else 0)
        return diferencia.histogram()[255]

    def _slot(self, texto: str) -> str | None:
        for campo, valor in (self.spec.get("valores") or {}).items():
            if str(valor) == texto:
                return str(campo)
        return None

    async def _ejecutar_y_registrar(self, nombre: str, a: dict) -> str:
        if nombre not in ("clic", "rellenar", "escribir", "tecla", "rueda"):
            return await self._ejecutar(nombre, a)
        antes = self.esc.vnc.captura()
        resultado = await self._ejecutar(nombre, a)
        if resultado.startswith("NO ejecutada"):
            return resultado
        cambiados = self._pixeles_cambiados(antes, self.esc.vnc.captura())
        comun = {"screen_changed_px": cambiados, "model": self.modelo}
        if nombre == "clic":
            tipo = "double_click" if a.get("veces", 1) == 2 else "click"
            self.steps.append(Step(tipo, x=a["x"], y=a["y"], anchor_png=self._ancla(antes, a["x"], a["y"]), **comun))
        elif nombre == "rellenar":
            # Una acción compuesta: clic en el campo, seleccionar todo y escribir. El cambio de
            # pantalla se midió para el conjunto, así que va en el clic y en el texto.
            self.steps.append(Step("click", x=a["x"], y=a["y"], anchor_png=self._ancla(antes, a["x"], a["y"]), **comun))
            self.steps.append(Step("key", key="ctrl+a", model=self.modelo))
            self.steps.append(Step("type", text=a["texto"], slot=self._slot(a["texto"]), **comun))
        elif nombre == "escribir":
            self.steps.append(Step("type", text=a["texto"], slot=self._slot(a["texto"]), **comun))
        elif nombre == "tecla":
            self.steps.append(Step("key", key=a["combinacion"], **comun))
        elif nombre == "rueda":
            direccion = "down" if a["pasos"] > 0 else "up"
            self.steps.append(Step("scroll", x=a["x"], y=a["y"], direction=direccion, **comun))
        return resultado

    # --- bucle ---------------------------------------------------------------------------

    @staticmethod
    def _podar_capturas(mensajes: list, conservar: int = 2) -> None:
        con_imagen = [m for m in mensajes if m["role"] == "user" and isinstance(m["content"], list)]
        for mensaje in con_imagen[:-conservar]:
            textos = [p["text"] for p in mensaje["content"] if p["type"] == "text"]
            mensaje["content"] = "\n".join(textos) + "\n[captura anterior omitida]"

    async def _observacion(self, texto: str, paso: int) -> dict:
        captura = await self.esc.captura()
        self.traza.captura(paso, captura)
        pie = f"\nTurno {paso} de {MAX_PASOS}. Quedan {int(self._quedan())} s. Captura actual adjunta."
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": texto + pie},
                {"type": "image_url", "image_url": {"url": imagen_a_url(captura)}},
            ],
        }

    async def correr(self) -> Cierre:
        ancho, alto = self.esc.tamano
        sistema = prompts.OPERADOR.format(ancho=ancho, alto=alto, pasos=MAX_PASOS, segundos=int(self._quedan()))
        if self.notas:
            sistema += (
                "\nNOTAS APRENDIDAS EN TAREAS ANTERIORES SOBRE ESTA APLICACIÓN (son pistas; si la pantalla las "
                f"contradice, manda la pantalla):\n{self.notas}\n"
            )
        inicio = f"TAREA: {self.tarea}"
        if self.spec:
            inicio += f"\n\nANÁLISIS PREVIO DE LA TAREA (compruébalo contra el texto): {json.dumps(self.spec, ensure_ascii=False)}"
        if self.aviso_inicial:
            inicio += f"\n\n{self.aviso_inicial}"
        mensajes = [{"role": "system", "content": sistema}, await self._observacion(inicio, 1)]
        rechazos = 0

        for paso in range(1, MAX_PASOS + 1):
            if self._quedan() < RESERVA_SEGUNDOS:
                return self._cierre_forzado("se agotó el tiempo", paso)
            self._podar_capturas(mensajes)
            respuesta = await self.modelos.chat(
                self.modelo, mensajes, HERRAMIENTAS, max_tokens=2500, tool_choice="required", reasoning={"effort": "low"}
            )
            llamadas = respuesta.get("tool_calls") or []
            mensajes.append({"role": "assistant", "content": respuesta.get("content") or "", "tool_calls": llamadas})
            if not llamadas:
                mensajes.pop()
                mensajes.append({"role": "user", "content": "Responde usando una herramienta."})
                continue

            resultados, cierre, cortar = [], None, False
            for llamada in llamadas:
                nombre = llamada["function"]["name"]
                try:
                    args = json.loads(llamada["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if cortar:
                    resultado = "no ejecutada: se interrumpió el lote; revisa la captura y decide de nuevo"
                elif nombre == "terminar":
                    cierre = Cierre(
                        estado=args.get("estado", "fallida"),
                        hechos=args.get("hechos", ""),
                        respuesta=args.get("respuesta", "") or "",
                        razon=args.get("razon", "") or "",
                    )
                    resultado = "recibido"
                    cortar = True
                else:
                    try:
                        resultado = await self._ejecutar_y_registrar(nombre, args)
                    except (KeyError, TypeError, ValueError) as error:
                        resultado = f"argumentos inválidos ({type(error).__name__}: {error})"
                    # Tras un cambio de datos o un rechazo, el modelo debe mirar antes de seguir.
                    cortar = bool(args.get("cambia_datos")) or resultado.startswith("NO ejecutada")
                    self.bitacora.append(f"{paso}. {nombre} {json.dumps(args, ensure_ascii=False)} → {resultado[:1500]}")
                self.traza.evento(paso=paso, accion=nombre, args=args, resultado=resultado[:3000])
                resultados.append({"role": "tool", "tool_call_id": llamada["id"], "content": resultado})
            mensajes.extend(resultados)

            if cierre is None:
                mensajes.append(await self._observacion("Resultados arriba.", paso + 1))
                continue

            cierre.pasos, cierre.confirmaciones, cierre.bitacora = paso, self.confirmaciones, self.bitacora
            conforme, problema = await self._verificar(cierre)
            self.traza.evento(paso=paso, accion="verificar", conforme=conforme, problema=problema)
            if conforme:
                cierre.verificado = True
                return cierre
            rechazos += 1
            if rechazos >= 2 or self._quedan() < RESERVA_SEGUNDOS + 20:
                if cierre.estado == "terminada":
                    cierre.estado = "fallida"
                    cierre.razon = f"No se pudo verificar el resultado: {problema}"
                return cierre
            self.bitacora.append(f"{paso}. verificador: no conforme → {problema}")
            mensajes.append(
                await self._observacion(
                    f"El verificador NO aceptó el cierre: {problema}\nReúne la evidencia que falta o corrige, "
                    "sin repetir cambios ya aplicados, y vuelve a terminar.",
                    paso + 1,
                )
            )
        return self._cierre_forzado("se agotaron los turnos", MAX_PASOS)

    def _cierre_forzado(self, motivo: str, paso: int) -> Cierre:
        hechos = "No se alcanzó a completar ni a verificar la tarea."
        if self.confirmaciones:
            hechos += f" Se aplicaron {self.confirmaciones} cambio(s) antes de detenerse."
        return Cierre("fallida", hechos, razon=motivo, pasos=paso, confirmaciones=self.confirmaciones, bitacora=self.bitacora)
