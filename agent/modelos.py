"""Cliente de OpenRouter (chat completions) con conteo de tokens y costo."""

import base64
import io
import os
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


class Modelos:
    def __init__(self, uso: Uso | None = None):
        self.uso = uso or Uso()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0))

    async def cerrar(self) -> None:
        await self._http.aclose()

    async def chat(self, modelo: str, mensajes: list, herramientas: list | None = None, **extra) -> dict:
        """Devuelve el mensaje del asistente (con `content` y, si hay, `tool_calls`)."""
        cuerpo = {"model": modelo, "messages": mensajes, "usage": {"include": True}, **extra}
        if herramientas:
            cuerpo["tools"] = herramientas
        ultimo_error = ""
        for _ in range(3):
            try:
                respuesta = await self._http.post(
                    URL,
                    json=cuerpo,
                    headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                )
            except httpx.HTTPError as error:
                ultimo_error = type(error).__name__
                continue
            if respuesta.status_code in (429, 500, 502, 503, 529):
                ultimo_error = f"HTTP {respuesta.status_code}"
                continue
            datos = respuesta.json()
            if respuesta.status_code != 200 or "choices" not in datos:
                detalle = (datos.get("error") or {}).get("message", "")[:300]
                raise ErrorModelo(f"OpenRouter respondió {respuesta.status_code}: {detalle}")
            self.uso.sumar(modelo, datos.get("usage") or {})
            return datos["choices"][0]["message"]
        raise ErrorModelo(f"OpenRouter no respondió tras 3 intentos ({ultimo_error})")
