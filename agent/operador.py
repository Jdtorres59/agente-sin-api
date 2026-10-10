"""Bucle del operador: observar → decidir → actuar, con compuertas y verificación."""

import asyncio
import io
import json
import os
import re
import time
from dataclasses import dataclass, field

import unicodedata

from PIL import Image, ImageChops, ImageDraw

from . import prompts
from .contracts import Step
from .escritorio import Escritorio, cerca_de_borde, dentro_de_control
from .modelos import ErrorModelo, Modelos, imagen_a_url
from .recuperacion import Diario, recuperar
from .tablas import Tabla
from .traza import Traza

MAX_PASOS = 60  # el límite real es el tiempo; 40 turnos se quedaban cortos en tareas de varios pasos
RESERVA_SEGUNDOS = 30  # margen para verificar y reportar antes del límite

# Verbos de acción inequívocos en el nombre de un control. Si el operador hace clic en algo que se
# llama así, pasa por la compuerta aunque lo haya marcado como inofensivo: la etiqueta del modelo no
# basta. Solo palabras completas: "Datos generales" o "Envíos" no son acciones.
_PARECE_CAMBIO = re.compile(
    r"\b(guardar|grabar|crear|eliminar|borrar|confirmar|exportar|importar|generar|enviar|aprobar|rechazar|"
    r"anular|revertir|aceptar|registrar|pagar|imprimir|facturar|emitir|agendar|programar|reprogramar|cancelar|"
    r"cobrar|abonar|asignar|autorizar|firmar|transferir|reembolsar|restablecer|regenerar|archivar|publicar|"
    r"activar|desactivar|bloquear|finalizar|procesar|aplicar|save|delete|remove|submit|create|approve|reject|"
    r"send|accept|export|import|print|issue|schedule|cancel|pay|refund|assign|archive|apply|sí|ok|yes)\b",
    re.IGNORECASE,
)

# Controles que solo navegan. Si el operador los marca como cambio de datos, no hace falta revisarlos.
_SOLO_NAVEGA = re.compile(r"\b(buscar|filtrar|siguiente|anterior|volver|search|filter|next|previous|back)\b", re.IGNORECASE)
MAX_CONSEJOS = 3  # consultas al supervisor por atasco, por tarea
MAX_RECHAZOS_IGUALES = 3  # veces que el revisor rechaza la misma acción antes de dejar de consultarlo
_NEGADO = re.compile(r"\b(sin|no|without|not)\s+\w+", re.IGNORECASE)
# «Aplicar» guarda un formulario, pero «Aplicar filtro» solo filtra.
_SOLO_FILTRA = re.compile(r"\b(aplicar|apply)\s+(el\s+|los\s+)?(filtros?|filters?|b[uú]squeda)", re.IGNORECASE)


def _nombra_cambio(texto: str) -> bool:
    """El nombre del control sugiere una acción de cambio. «Volver sin guardar» no: el verbo va negado."""
    return bool(_PARECE_CAMBIO.search(_SOLO_FILTRA.sub(" ", _NEGADO.sub(" ", texto))))


def _palabras(texto: str) -> set[str]:
    plano = unicodedata.normalize("NFD", texto.lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return {p for p in re.findall(r"[a-z0-9]+", plano) if len(p) >= 3}


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
    "description": "true si esta acción guarda, crea, elimina, confirma, exporta, emite, agenda, cancela, "
    "marca un estado o revierte algo: cualquier control que actúa sobre los datos al pulsarlo.",
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
        "Devuelve el texto exacto de toda la pantalla. Cierra menús y listas desplegables abiertos y quita el "
        "foco; no cambia datos. No la uses para leer un menú o una lista abierta: léelos en la captura. "
        "No incluye el contenido de los campos de un formulario: para eso usa leer_campo.",
        {},
        [],
    ),
    _herramienta("leer_campo", "Devuelve el texto exacto de un campo de texto.", _XY, ["x", "y"]),
    _herramienta(
        "leer_opciones",
        "Abre la lista desplegable que está en (x, y), devuelve el texto de sus opciones y la cierra sin elegir "
        "nada. Úsala para saber qué opciones tiene una lista antes de elegir o de concluir que falta una.",
        {**_XY, "lista": {"type": "string", "description": "Nombre de la lista."}},
        ["x", "y", "lista"],
    ),
    _herramienta(
        "leer_lista",
        "Lee la tabla de la lista en pantalla recorriendo TODAS sus páginas y la guarda para `contar`. Llámala "
        "estando en la primera página, con los filtros ya aplicados. siguiente_x y siguiente_y son el centro, "
        "tal como se ve en la captura, del control que pasa a la página siguiente. Pon -1 en ambos SOLO si en la "
        "captura no hay ningún control para pasar de página: si lo hay y pones -1, la lectura queda incompleta.",
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
            "evidencia_turno": {
                "type": "integer",
                "description": "Opcional: número del turno cuya captura muestra lo que reportas, cuando eso solo se "
                "vio en una captura (por ejemplo, las opciones de una lista desplegable abierta).",
            },
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
    evidencia_turno: int | None = None  # turno cuya captura el operador señala como evidencia


def _json(texto: str) -> dict:
    encontrado = re.search(r"\{.*\}", texto or "", re.S)
    try:
        return json.loads(encontrado.group(0)) if encontrado else {}
    except json.JSONDecodeError:
        return {}


def _abreviar(resultado: str, tope: int = 1500) -> str:
    """Un resultado largo para el registro: se corta en un fin de línea y se dice que está cortado.

    Sin el aviso, quien revisa el registro toma la última fila a medias por una lectura defectuosa.
    """
    if len(resultado) <= tope:
        return resultado
    corte = resultado[:tope]
    corte = corte.rsplit("\n", 1)[0] if "\n" in corte else corte
    return corte + "\n[... sigue: el registro solo muestra el comienzo; la herramienta trabajó con el resultado completo]"


def _entero(valor) -> int | None:
    """Un número de turno tal como lo manda el modelo (a veces como texto). None si no lo es."""
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


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
        recuperable: bool = True,
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
        self.cambios: list[str] = []  # acciones que pasaron la compuerta y se ejecutaron
        self.paso = 0
        self.firmas: list[str] = []  # qué pidió el operador en cada turno, para detectar bucles
        self.sin_efecto = 0  # acciones seguidas que no cambiaron la pantalla
        self.consejos = 0  # veces que se le pidió al supervisor desatascar
        self.objeciones: list[str] = []  # lo que objetó el verificador; la memoria lo guarda como lección
        self.costo_punto = 0  # comprobaciones de qué hay bajo el punto de un clic
        self.tabla: Tabla | None = None
        self.evidencia = None  # captura tomada justo después del último cambio de datos
        self.capturas: dict[int, str] = {}  # la captura que el operador vio en cada turno, ya codificada
        self.rechazadas: dict[str, tuple[int, str]] = {}  # acción rechazada → (veces, último motivo)
        self.lista_abierta: str | None = None  # captura de la última lista desplegable leída, ya codificada
        self.caidas_vistas = 0  # reconexiones al escritorio de las que el operador ya fue avisado
        self.diario = Diario()  # valor anterior exacto de cada campo editado, para poder restaurarlo
        self.recuperable = recuperable  # el sub-operador de una reversión no vuelve a recuperar

    def _quedan(self) -> float:
        return self.limite - time.monotonic()

    # --- supervisor ----------------------------------------------------------------------

    async def _consultar_supervisor(
        self, instrucciones: str, pregunta: str, con_evidencia: bool = False, turno: int | None = None
    ) -> dict:
        captura = await self.esc.captura()
        contexto = (
            f"TAREA: {self.tarea}\n\nREGISTRO DE ACCIONES (más reciente al final):\n"
            + "\n".join(self.bitacora[-40:])
            + f"\n\n{pregunta}"
        )
        imagenes: list[tuple[str, str]] = []  # (qué muestra, imagen codificada), en el orden en que se envían
        if con_evidencia and self.evidencia is not None:
            antes = "pantalla justo después del último cambio de datos (los avisos pueden ser pasajeros)"
            imagenes.append((antes, imagen_a_url(self.evidencia)))
        if self.lista_abierta is not None:
            imagenes.append(("última lista desplegable leída con leer_opciones, abierta", self.lista_abierta))
        if turno in self.capturas:
            senalada = f"captura que el operador vio al empezar el turno {turno} y que señala como evidencia"
            imagenes.append((senalada, self.capturas[turno]))
        imagenes.append(("pantalla actual", imagen_a_url(captura)))
        if len(imagenes) > 1:
            contexto += "\n\n" + "\n".join(f"IMAGEN {n}: {que}." for n, (que, _) in enumerate(imagenes, 1))
        mensaje = await self.modelos.chat(
            self.supervisor,
            [
                {"role": "system", "content": instrucciones},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": contexto},
                        *({"type": "image_url", "image_url": {"url": url}} for _, url in imagenes),
                    ],
                },
            ],
            max_tokens=2000,
            reasoning={"effort": "medium"},
        )
        self.traza.evento(accion="supervisor", pregunta=pregunta[:300], respuesta=(mensaje.get("content") or "")[:600])
        contenido = mensaje.get("content") or ""
        # Si el supervisor no respondió JSON, su texto cuenta como objeción.
        return _json(contenido) or {"aprobar": False, "conforme": False, "motivo": contenido[:300], "problema": contenido[:300]}

    async def _aprobar_guardado(self, descripcion: str) -> tuple[bool, str, bool]:
        """Devuelve (aprobada, motivo, si el revisor la considera un cambio real)."""
        previas = "; ".join(self.cambios) or "ninguna"
        veredicto = await self._consultar_supervisor(
            prompts.REVISOR_GUARDADO,
            f"ACCIONES DE CAMBIO YA EJECUTADAS EN ESTA TAREA: {previas}.\nACCIÓN PROPUESTA: {descripcion}",
        )
        es_cambio = veredicto.get("es_cambio") is not False
        return bool(veredicto.get("aprobar")), str(veredicto.get("motivo", "sin motivo")), es_cambio

    async def _verificar(self, cierre: Cierre) -> tuple[bool, str]:
        veredicto = await self._consultar_supervisor(
            prompts.VERIFICADOR,
            f"EL OPERADOR REPORTA: estado={cierre.estado}; hechos={cierre.hechos}; "
            f"respuesta={cierre.respuesta or '-'}; razon={cierre.razon or '-'}",
            con_evidencia=True,
            turno=cierre.evidencia_turno,
        )
        return bool(veredicto.get("conforme")), str(veredicto.get("problema", ""))

    async def _pedir_consejo(self) -> str:
        """Atasco: el supervisor mira la pantalla y el registro, y sugiere el siguiente paso."""
        self.consejos += 1
        veredicto = await self._consultar_supervisor(prompts.DESATASCAR, "El operador está atascado. ¿Qué debe hacer ahora?")
        return str(veredicto.get("consejo") or veredicto.get("motivo") or "")[:500]

    async def _texto_bajo_el_punto(self, x: int, y: int) -> tuple[str | None, bool]:
        """Lo que el modelo barato lee bajo el punto del clic, en un recorte con una cruz.

        Devuelve (texto del control o None si no hay nada legible, si ese control ejecuta una acción).
        Lo segundo no depende de ninguna lista de verbos: lo juzga el modelo por el nombre que ve.
        """
        imagen = self.esc.vnc.captura()
        izquierda, arriba = max(0, x - 120), max(0, y - 36)
        recorte = imagen.crop((izquierda, arriba, min(imagen.width, x + 120), min(imagen.height, y + 36))).convert("RGB")
        cx, cy = x - izquierda, y - arriba
        dibujo = ImageDraw.Draw(recorte)
        for desde, hasta in ((4, 14), (-14, -4)):  # cruz abierta en el centro para no tapar el texto
            dibujo.line((cx + desde, cy, cx + hasta, cy), fill=(255, 0, 0), width=1)
            dibujo.line((cx, cy + desde, cx, cy + hasta), fill=(255, 0, 0), width=1)
        recorte = recorte.resize((recorte.width * 2, recorte.height * 2), Image.LANCZOS)
        try:
            mensaje = await self.modelos.chat(
                self.modelo,
                [
                    {"role": "system", "content": prompts.BAJO_EL_PUNTO},
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": imagen_a_url(recorte)}}]},
                ],
                max_tokens=400,
                reasoning={"effort": "low"},
            )
        except ErrorModelo:
            return None, False
        self.costo_punto += 1
        contenido = mensaje.get("content") or ""
        visto = _json(contenido)
        texto = " ".join(str(visto.get("texto", contenido) if visto else contenido).split())[:80]
        if not texto or texto.lower().strip(".") == "ninguno":
            return None, False
        return texto, visto.get("ejecuta") is True

    async def _punto_equivocado(self, a: dict) -> tuple[str | None, bool, str | None]:
        """Comprueba qué control hay bajo el punto del clic.

        Devuelve (motivo para no hacer clic o None, si es una acción que el operador no marcó como
        cambio, texto leído bajo el punto). Lo segundo manda el clic a la compuerta aunque el
        operador lo creyera inofensivo.
        """
        objetivo = a.get("objetivo", "")
        leido, ejecuta = await self._texto_bajo_el_punto(a["x"], a["y"])
        self.traza.evento(paso=self.paso, accion="punto", x=a["x"], y=a["y"], objetivo=objetivo, leido=leido, ejecuta=ejecuta)
        if leido is None:
            return None, False, None
        # «Volver sin guardar» no es el botón «Guardar»: lo negado no cuenta para la coincidencia.
        mismo = bool(_palabras(leido) & _palabras(_NEGADO.sub(" ", objetivo)))
        if a.get("cambia_datos"):
            # Una acción de cambio solo se ejecuta si lo que hay bajo el punto es lo que se dijo.
            equivocado, sin_marcar = not mismo, False
        else:
            es_accion = (_nombra_cambio(leido) or ejecuta) and not _SOLO_NAVEGA.search(leido)
            # Un clic inofensivo que cae sobre una acción: si es otro control, no se hace; si es el
            # que el operador quería, se trata como cambio y lo ve el revisor.
            equivocado = es_accion and not mismo and not _nombra_cambio(objetivo)
            sin_marcar = es_accion and mismo
        if not equivocado:
            return None, sin_marcar, leido
        motivo = (
            f"NO ejecutada: bajo el punto ({a['x']}, {a['y']}) está el control «{leido}», no «{objetivo}». "
            f"Vuelve a ubicar «{objetivo}» en la captura."
        )
        return motivo, False, leido

    async def _leer_opciones(self, x: int, y: int, lista: str) -> str:
        """Abre una lista desplegable, transcribe sus opciones de la captura y la cierra con Escape.

        Las opciones de una lista no salen al leer el texto de la pantalla: solo se ven con la lista
        abierta. La captura queda guardada para que el verificador la vea.
        """
        leido, ejecuta = await self._texto_bajo_el_punto(x, y)
        if leido and (_nombra_cambio(leido) or ejecuta):
            # Abrir la lista es un clic: no se hace sobre un control que ejecuta algo.
            return f"NO ejecutada: bajo el punto ({x}, {y}) está el control «{leido}», que no es una lista."
        antes = await self.esc.captura()
        await self.esc.clic(x, y)
        abierta = await self.esc.captura()
        zona = ImageChops.difference(antes.convert("RGB"), abierta.convert("RGB")).getbbox()
        if zona is None:
            return "la lista no se abrió: la pantalla no cambió. Comprueba el punto en la captura."
        margen = 12
        caja = (max(0, zona[0] - margen), max(0, zona[1] - margen), min(abierta.width, zona[2] + margen), min(abierta.height, zona[3] + margen))  # fmt: skip
        recorte = abierta.crop(caja).convert("RGB")
        recorte = recorte.resize((recorte.width * 2, recorte.height * 2), Image.LANCZOS)
        try:
            mensaje = await self.modelos.chat(
                self.modelo,
                [
                    {"role": "system", "content": prompts.OPCIONES_DE_LISTA},
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": imagen_a_url(recorte)}}]},
                ],
                max_tokens=1200,
                reasoning={"effort": "low"},
            )
            opciones = [linea.strip() for linea in (mensaje.get("content") or "").splitlines() if linea.strip()]
        except ErrorModelo:
            opciones = []
        await self.esc.tecla("escape")
        despues = await self.esc.captura()
        self.traza.captura(800 + self.paso, abierta)
        self.traza.evento(paso=self.paso, accion="opciones", lista=lista, opciones=opciones)
        if not opciones:
            return "no se pudieron leer las opciones. Abre la lista con un clic y míralas en la captura siguiente."
        self.lista_abierta = imagen_a_url(abierta)
        cerrada = ImageChops.difference(antes.convert("RGB"), despues.convert("RGB")).getbbox() is None
        aviso = "" if cerrada else " Aviso: la pantalla no quedó igual que antes de abrirla; revisa la captura."
        return (
            f"Opciones de «{lista}», leídas con la lista abierta: " + " | ".join(opciones)
            + ". La lista se cerró con Escape, sin elegir nada." + aviso
        )

    # --- acciones ------------------------------------------------------------------------

    async def _ejecutar(self, nombre: str, a: dict) -> str:
        cambio = lambda hubo: "la pantalla cambió" if hubo else "la pantalla NO cambió (puede no haber tenido efecto)"  # noqa: E731
        if nombre in ("clic", "rellenar", "leer_campo", "leer_opciones") and cerca_de_borde(self.esc.vnc.captura(), a["x"], a["y"]):
            # Un clic pegado al borde puede caer en el control vecino: no se ejecuta.
            self.traza.evento(paso=self.paso, accion="borde", x=a["x"], y=a["y"])
            return (
                f"NO ejecutada: el punto ({a['x']}, {a['y']}) está en el borde de un control. "
                "Apunta al centro del control que quieres."
            )
        objetivo = a.get("objetivo", "")
        if nombre == "clic" and a.get("cambia_datos") and _SOLO_NAVEGA.search(objetivo) and not _nombra_cambio(objetivo):
            a["cambia_datos"] = False  # buscar o paginar no cambia datos: no gasta una revisión
        if nombre == "clic" and not a.get("cambia_datos") and _nombra_cambio(objetivo):
            a["cambia_datos"] = True
            marcada_por_nombre = True
        else:
            marcada_por_nombre = False
        leido = None
        # La comprobación del punto cuesta ~2 s: se hace donde un error cambia datos, es decir en las
        # acciones de cambio y en los clics que caen dentro de un control con borde (un botón).
        if nombre == "clic" and (a.get("cambia_datos") or dentro_de_control(self.esc.vnc.captura(), a["x"], a["y"])):
            motivo, sin_marcar, leido = await self._punto_equivocado(a)
            if motivo:
                return motivo
            if sin_marcar:
                a["cambia_datos"] = True  # el control ejecuta algo aunque el operador no lo dijera
                marcada_por_nombre = True
        revisada = False
        if nombre in ("clic", "tecla") and a.get("cambia_datos"):
            revisada = True
            descripcion = (
                f"clic en «{a.get('objetivo', '')}» ({a.get('x')}, {a.get('y')})"
                if nombre == "clic"
                else f"tecla {a.get('combinacion')}"
            )
            if leido:
                descripcion += f"; bajo ese punto se lee «{leido}»"
            veces, ultimo = self.rechazadas.get(descripcion, (0, ""))
            if veces >= MAX_RECHAZOS_IGUALES:
                self.traza.evento(paso=self.paso, accion="rechazo_repetido", descripcion=descripcion, veces=veces)
                return (
                    f"NO ejecutada. El revisor ya rechazó {veces} veces esta misma acción ({ultimo}). No la "
                    "propongas más: cambia de plan o, si la tarea no se puede completar, termina con «fallida»."
                )
            aprobado, motivo, es_cambio = await self._aprobar_guardado(descripcion)
            if not aprobado:
                self.rechazadas[descripcion] = (veces + 1, motivo)
                return f"NO ejecutada. El revisor la rechazó: {motivo}"
            # Cuenta como cambio si el operador mismo la marcó, o si el revisor no la descartó. Solo
            # se libra lo que entró por el nombre y el revisor vio que no deja ningún resultado.
            cuenta = es_cambio or not marcada_por_nombre
            if cuenta:
                self.confirmaciones += 1
                self.cambios.append(f"turno {self.paso}: {descripcion}")
            else:
                a["cambia_datos"] = False
        if revisada:
            # Toda acción que pasó por la compuerta deja su captura inmediata: los avisos de la
            # aplicación pueden durar un par de segundos y son la evidencia del verificador.
            if nombre == "clic":
                hubo = await self.esc.clic(a["x"], a["y"], veces=a.get("veces", 1))
            else:
                hubo = await self.esc.tecla(a["combinacion"])
            self.evidencia = await self.esc.captura()
            self.traza.captura(900 + self.confirmaciones, self.evidencia)
            if a.get("cambia_datos"):
                self.diario.cambio_confirmado(descripcion, a.get("x"), a.get("y"), self.evidencia, self.paso)
            return cambio(hubo)
        if nombre == "clic":
            return cambio(await self.esc.clic(a["x"], a["y"], veces=a.get("veces", 1)))
        if nombre == "rellenar":
            anterior, quedo, en_campo = await self.esc.rellenar_con_anterior(a["x"], a["y"], a["texto"])
            if not en_campo:
                return "NO ejecutada: el punto no está dentro de un campo de texto. Apunta al centro del campo."
            self.rechazadas.clear()  # el formulario cambió: una acción rechazada merece otra revisión
            self.diario.antes_de_rellenar(a["x"], a["y"], a.get("campo", ""), anterior, a["texto"])
            self.diario.despues_de_rellenar(quedo)
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
            if texto is None:
                return "no se pudo leer el texto de la pantalla"
            return f"TEXTO DE LA PANTALLA (al leer se cerraron los menús que hubiera abiertos):\n{texto[:8000]}"
        if nombre == "leer_opciones":
            return await self._leer_opciones(a["x"], a["y"], a.get("lista", ""))
        if nombre == "leer_campo":
            leido = await self.esc.leer_campo(a["x"], a["y"])
            return "el campo está vacío o no es un campo de texto" if leido is None else f"el campo contiene «{leido}»"
        if nombre == "leer_lista":
            x, y = a.get("siguiente_x", -1), a.get("siguiente_y", -1)
            ancho, alto = self.esc.tamano
            if (x, y) != (-1, -1) and not (25 <= x <= ancho - 25 and 10 <= y <= alto - 10):
                return "coordenadas inválidas: da el centro real del control de página siguiente, o -1 y -1 si no hay"
            siguiente = None if (x, y) == (-1, -1) else (x, y)
            if siguiente and cerca_de_borde(self.esc.vnc.captura(), x, y):
                return (
                    f"NO ejecutada: el punto ({x}, {y}) está en el borde de un control. Da el centro del control "
                    "de página siguiente tal como se ve en la captura."
                )
            self.tabla, motivo = await self.esc.leer_lista(siguiente)
            if not self.tabla.filas:
                return f"no se encontraron filas de tabla ({motivo}). Usa leer_pantalla."
            if siguiente is None:
                aviso = (
                    "Solo se leyó la página que estaba en pantalla. Si en la captura hay un control de página "
                    "siguiente, esta lectura está incompleta: vuelve a la primera página (aplica de nuevo el filtro) "
                    "y llama leer_lista con el centro de ese control."
                )
            elif "ya leída" in motivo:
                aviso = (
                    "Lectura NO fiable: no la uses. Vuelve a la primera página (aplica de nuevo el filtro) y llama "
                    "leer_lista con el centro exacto del control de página siguiente."
                )
            else:
                aviso = (
                    "La pantalla quedó en la última página leída: si en la captura el control de página siguiente "
                    "sigue activo, la lectura quedó incompleta; repítela desde la primera página (aplica de nuevo "
                    "el filtro)."
                )
            return (
                f"{len(self.tabla.filas)} filas en {self.tabla.paginas} página(s); se detuvo porque {motivo}. "
                f"{aviso}\n{self.tabla.como_texto()}"
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
        etiqueta = a.get("objetivo") or a.get("campo")
        if nombre == "clic":
            tipo = "double_click" if a.get("veces", 1) == 2 else "click"
            self.steps.append(
                Step(tipo, x=a["x"], y=a["y"], anchor_png=self._ancla(antes, a["x"], a["y"]),
                     label=etiqueta, gated=bool(a.get("cambia_datos")), **comun)
            )  # fmt: skip
        elif nombre == "rellenar":
            # Una acción compuesta: clic en el campo, seleccionar todo y escribir. El cambio de
            # pantalla se midió para el conjunto, así que va en el clic y en el texto.
            self.steps.append(
                Step("click", x=a["x"], y=a["y"], anchor_png=self._ancla(antes, a["x"], a["y"]), label=etiqueta, **comun)
            )
            self.steps.append(Step("key", key="ctrl+a", model=self.modelo))
            self.steps.append(Step("type", text=a["texto"], slot=self._slot(a["texto"]), **comun))
        elif nombre == "escribir":
            self.steps.append(Step("type", text=a["texto"], slot=self._slot(a["texto"]), **comun))
        elif nombre == "tecla":
            self.steps.append(Step("key", key=a["combinacion"], gated=bool(a.get("cambia_datos")), **comun))
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
        self.capturas[paso] = imagen_a_url(captura)
        pie = f"\nTurno {paso} de {MAX_PASOS}. Quedan {int(self._quedan())} s. Captura actual adjunta."
        caidas = getattr(self.esc.vnc, "reconexiones", 0)
        if caidas > self.caidas_vistas:
            self.caidas_vistas = caidas
            self.traza.evento(paso=paso, accion="reconexion", veces=caidas)
            pie += (
                "\nAVISO: se perdió la conexión con el escritorio y se recuperó. Mira en la captura dónde quedó la "
                "aplicación antes de seguir, y no repitas un cambio sin comprobar primero si ya se aplicó."
            )
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": texto + pie},
                {"type": "image_url", "image_url": {"url": self.capturas[paso]}},
            ],
        }

    async def correr(self) -> Cierre:
        cierre = await self._bucle()
        if self.recuperable and cierre.estado == "fallida" and self.diario.hay_cambios:
            # Se rindió con cambios ya aplicados: diagnosticar y, si no deberían existir, restaurar.
            try:
                cierre = await recuperar(
                    cierre, self.diario, self.esc, self.modelos, self.traza, self.limite,
                    navegar=self._sub_operador, tarea=self.tarea,
                )  # fmt: skip
            except Exception as error:  # la recuperación nunca debe tumbar el reporte
                self.traza.evento(accion="recuperacion_error", tipo=type(error).__name__, detalle=str(error)[:300])
        return cierre

    async def _sub_operador(self, instruccion: str, limite: float) -> Cierre:
        """Operador acotado para una reversión: misma pantalla, otra tarea, sin volver a recuperar."""
        sub = Operador(instruccion, self.esc, self.modelos, self.traza, limite, recuperable=False)
        cierre = await sub.correr()
        self.steps.extend(sub.steps)
        return cierre

    async def _bucle(self) -> Cierre:
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
        rechazos = errores_modelo = 0

        for paso in range(1, MAX_PASOS + 1):
            self.paso = paso
            if self._quedan() < RESERVA_SEGUNDOS:
                return self._cierre_forzado("se agotó el tiempo", paso)
            self._podar_capturas(mensajes)
            try:
                respuesta = await self.modelos.chat(
                    self.modelo, mensajes, HERRAMIENTAS, max_tokens=2500, tool_choice="required", reasoning={"effort": "low"}
                )
                errores_modelo = 0
            except ErrorModelo as error:
                errores_modelo += 1
                self.traza.evento(paso=paso, accion="error_modelo", detalle=str(error)[:300])
                if errores_modelo >= 2:
                    return self._cierre_forzado("el modelo no respondió", paso)
                await asyncio.sleep(1.5)
                continue
            llamadas = respuesta.get("tool_calls") or []
            mensajes.append({"role": "assistant", "content": respuesta.get("content") or "", "tool_calls": llamadas})
            if not llamadas:
                mensajes.pop()
                mensajes.append({"role": "user", "content": "Responde usando una herramienta."})
                continue

            resultados, cierre, cortar, hubo_clic = [], None, False, False
            for llamada in llamadas:
                nombre = llamada["function"]["name"]
                try:
                    args = json.loads(llamada["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not cortar and nombre in ("leer_pantalla", "leer_lista") and hubo_clic:
                    cortar = True
                if cortar:
                    resultado = "no ejecutada: se interrumpió el lote; revisa la captura y decide de nuevo"
                elif nombre == "terminar":
                    cierre = Cierre(
                        estado=args.get("estado", "fallida"),
                        hechos=args.get("hechos", ""),
                        respuesta=args.get("respuesta", "") or "",
                        razon=args.get("razon", "") or "",
                        evidencia_turno=_entero(args.get("evidencia_turno")),
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
                    hubo_clic = hubo_clic or nombre == "clic"
                    if nombre in ("clic", "tecla", "escribir", "rueda"):
                        self.sin_efecto = self.sin_efecto + 1 if "NO cambió" in resultado else 0
                    self.bitacora.append(f"{paso}. {nombre} {json.dumps(args, ensure_ascii=False)} → {_abreviar(resultado)}")
                self.traza.evento(paso=paso, accion=nombre, args=args, resultado=resultado[:3000])
                resultados.append({"role": "tool", "tool_call_id": llamada["id"], "content": resultado})
            mensajes.extend(resultados)

            if cierre is None:
                self.firmas.append(json.dumps([(c["function"]["name"], c["function"].get("arguments")) for c in llamadas]))
                aviso = "Resultados arriba."
                f = self.firmas
                repetido = (
                    (len(f) >= 3 and len(set(f[-3:])) == 1)
                    or (len(f) >= 4 and f[-1] == f[-3] and f[-2] == f[-4])
                    or f[-9:].count(f[-1]) >= 3  # el mismo turno tres veces en poco tiempo: bucle más largo
                )
                if repetido or self.sin_efecto >= 2:
                    # Escalamiento: el supervisor mira la pantalla y aconseja; el operador sigue con ese consejo.
                    self.sin_efecto = 0
                    self.firmas.clear()
                    aviso = (
                        "AVISO: llevas varios turnos repitiendo lo mismo o sin efecto en la pantalla. No repitas esa "
                        "acción. Mira con cuidado la captura actual y cambia de estrategia."
                    )
                    if self.consejos < MAX_CONSEJOS:
                        consejo = await self._pedir_consejo()
                        self.traza.evento(paso=paso, accion="escalamiento", consejo=consejo)
                        if consejo:
                            aviso += f"\nCONSEJO DEL SUPERVISOR: {consejo}"
                mensajes.append(await self._observacion(aviso, paso + 1))
                continue

            cierre.pasos, cierre.confirmaciones, cierre.bitacora = paso, self.confirmaciones, self.bitacora
            conforme, problema = await self._verificar(cierre)
            self.traza.evento(paso=paso, accion="verificar", conforme=conforme, problema=problema)
            if conforme:
                cierre.verificado = True
                return cierre
            rechazos += 1
            if problema:
                self.objeciones.append(problema)
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
