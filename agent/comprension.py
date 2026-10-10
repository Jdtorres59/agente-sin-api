"""Comprensión: convierte la instrucción en un TaskSpec antes de tocar la pantalla."""

import json
import os
import re

from .modelos import Modelos

INSTRUCCIONES = """\
Analiza la tarea que recibirá el operador de una aplicación de gestión. No la ejecutes. Responde SOLO con JSON:
{
  "tipo": "crear" | "modificar" | "consultar" | "exportar" | "eliminar" | "otro",
  "entidad": "tipo de registro, en singular y minúsculas",
  "referente": "cómo identifica la tarea al registro existente, o \\"\\" si no aplica",
  "valores": {"nombre del campo": "valor literal, con los mismos caracteres que en la tarea"},
  "es_pregunta": true | false,
  "fechas_relativas": ["expresiones como hoy, ayer o el sábado, si las hay"],
  "riesgos": ["ambigüedades o peligros que el operador debe comprobar antes de cambiar datos"]
}
"""


async def comprender(tarea: str, modelos: Modelos) -> dict:
    """Devuelve el TaskSpec, o {} si el modelo no respondió algo utilizable."""
    modelo = os.environ.get("SUPERVISOR_MODEL", "anthropic/claude-sonnet-5.5")
    try:
        mensaje = await modelos.chat(
            modelo,
            [{"role": "system", "content": INSTRUCCIONES}, {"role": "user", "content": f"TAREA: {tarea}"}],
            max_tokens=1200,
            reasoning={"effort": "low"},
        )
        encontrado = re.search(r"\{.*\}", mensaje.get("content") or "", re.S)
        spec = json.loads(encontrado.group(0)) if encontrado else {}
    except Exception:
        return {}
    return spec if isinstance(spec, dict) else {}
