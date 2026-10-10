"""Cliente de OpenRouter (chat completions) con conteo de tokens y costo."""

import asyncio
import base64
import io
import os
import time
from dataclasses import dataclass, field

import httpx
from PIL import Image

URL = "https://openrouter.ai/api/v1/chat/completions"


class ErrorModelo(Exception):
    pass


@dataclass
class Uso:
    llamadas: int = 0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    costo: float = 0.0
    por_modelo: dict = field(default_factory=dict)

    def sumar(self, modelo: str, uso: dict) -> None:
        self.llamadas += 1
        self.tokens_entrada += uso.get("prompt_tokens", 0)
        self.tokens_salida += uso.get("completion_tokens", 0)
        costo = float(uso.get("cost") or 0.0)
        self.costo += costo
        self.por_modelo[modelo] = self.por_modelo.get(modelo, 0.0) + costo


def imagen_a_url(imagen: Image.Image) -> str:
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG", optimize=False)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


ESPERAS = (1, 3, 8)  # segundos entre intentos: un error pasajero del proveedor dura más que un instante
PASAJEROS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529}
TOPE_LLAMADA = 60  # segundos entre todos los intentos de una llamada con el mismo modelo
TIEMPO_PETICION = 45  # segundos por petición
MARGEN_TAREA = 20  # segundos que una llamada debe dejarle a la tarea para reportar


class _Pasajero(Exception):
    """Un fallo que vale la pena reintentar."""


class Modelos:
    def __init__(self, uso: Uso | None = None, limite: float | None = None):
        self.uso = uso or Uso()
        self.limite = limite  # instante (time.monotonic) en que la tarea tiene que haber reportado
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(TIEMPO_PETICION, connect=10.0))

    async def cerrar(self) -> None:
        await self._http.aclose()

    def _queda(self, tope: float) -> float:
        """Segundos disponibles hasta `tope`, sin pasarse del reloj de la tarea."""
        if self.limite is not None:
            tope = min(tope, self.limite - MARGEN_TAREA)
        return tope - time.monotonic()

    async def _pedir(self, cuerpo: dict, segundos: float = TIEMPO_PETICION) -> dict:
        """Una petición. Devuelve el mensaje del asistente, o lanza _Pasajero o ErrorModelo."""
        try:
            respuesta = await self._http.post(
                URL,
                json=cuerpo,
                headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                timeout=max(3.0, min(TIEMPO_PETICION, segundos)),
            )
        except httpx.HTTPError as error:
            raise _Pasajero(type(error).__name__) from error
        try:
            datos = respuesta.json()
        except ValueError:
            datos = {}
        detalle = ((datos.get("error") or {}).get("message") or "")[:300] if isinstance(datos, dict) else ""
        estado = respuesta.status_code
        if estado in PASAJEROS:
            raise _Pasajero(f"HTTP {estado} {detalle}".strip())
        if estado == 200 and (not isinstance(datos, dict) or not datos.get("choices")):
            # OpenRouter a veces responde 200 con el error del proveedor en el cuerpo.
            raise _Pasajero(f"HTTP 200 sin respuesta {detalle}".strip())
        if estado == 400 and "provider returned error" in detalle.lower():
            raise _Pasajero(f"HTTP 400 {detalle}")
        if estado != 200:
            raise ErrorModelo(f"OpenRouter respondió {estado}: {detalle}")
        self.uso.sumar(cuerpo["model"], datos.get("usage") or {})
        return datos["choices"][0]["message"]

    @staticmethod
    def _otro_modelo(modelo: str) -> str | None:
        operador = os.environ.get("AGENT_MODEL", "anthropic/claude-haiku-5.5")
        supervisor = os.environ.get("SUPERVISOR_MODEL", "anthropic/claude-sonnet-5.5")
        otro = operador if modelo == supervisor else supervisor
        return otro if otro != modelo else None

    async def chat(self, modelo: str, mensajes: list, herramientas: list | None = None, **extra) -> dict:
        """Devuelve el mensaje del asistente (con `content` y, si hay, `tool_calls`).

        Reintenta los fallos pasajeros con espera creciente, sin pasar de TOPE_LLAMADA segundos entre
        todos los intentos ni del reloj de la tarea. Si el modelo sigue sin responder y queda tiempo,
        hace un último intento con el otro modelo para que la caída de uno no tumbe la tarea.
        """
        cuerpo = {"model": modelo, "messages": mensajes, "usage": {"include": True}, **extra}
        if herramientas:
            cuerpo["tools"] = herramientas
        tope = time.monotonic() + TOPE_LLAMADA
        ultimo, intentos = "", 0
        for espera in (*ESPERAS, None):
            intentos += 1
            try:
                return await self._pedir(cuerpo, self._queda(tope))
            except _Pasajero as error:
                ultimo = str(error)
            if espera is None or self._queda(tope) <= espera + 3:
                break  # no alcanza el tiempo para esperar y volver a pedir
            await asyncio.sleep(espera)
        otro = self._otro_modelo(modelo)
        if otro is not None and self._queda(time.monotonic() + TIEMPO_PETICION) > 5:
            # El otro modelo puede no aceptar que se le fuerce una herramienta: se le deja elegir.
            respaldo = {k: v for k, v in cuerpo.items() if k != "tool_choice"} | {"model": otro}
            try:
                return await self._pedir(respaldo, self._queda(time.monotonic() + TIEMPO_PETICION))
            except (_Pasajero, ErrorModelo) as error:
                ultimo += f"; respaldo con {otro}: {error}"
        raise ErrorModelo(f"OpenRouter no respondió tras {intentos} intento(s) ({ultimo})")
