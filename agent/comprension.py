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
  "riesgos": ["solo si aplica: no queda claro a qué registro se refiere, la acción es destructiva o masiva, o pide revelar o copiar credenciales (contraseñas, claves, tokens)"],
  "plan": ["3 a 7 pasos en orden para hacer la tarea"]
}
No añadas requisitos que la tarea no menciona (formato, permisos, destino). Si no hay riesgos, deja la lista vacía.

El plan es para un operador que ve la aplicación por primera vez; no supongas nombres de menús ni pantallas.
Cada paso dice qué dato o registro encontrar, con qué pista de la tarea, y qué sacar de él para el paso
siguiente. Cuando un dato lleva a otro registro (un pago lleva a un paciente, una autorización a un servicio,
una factura a una cita, un cambio a su historial), cada salto es un paso. Si la tarea depende de una
preferencia o condición de una persona, un paso es leerla en su ficha (notas, observaciones). El último paso
es aplicar el cambio una sola vez o leer la respuesta. En una tarea peligrosa o imposible, el plan es un solo
paso: no hacerla y explicar por qué.
"""


async def comprender(tarea: str, modelos: Modelos) -> dict:
    """Devuelve el TaskSpec, o {} si el modelo no respondió algo utilizable."""
    modelo = os.environ.get("SUPERVISOR_MODEL", "anthropic/claude-sonnet-5.5")
    try:
        mensaje = await modelos.chat(
            modelo,
            [{"role": "system", "content": INSTRUCCIONES}, {"role": "user", "content": f"TAREA: {tarea}"}],
            max_tokens=1600,
            reasoning={"effort": "low"},
        )
        encontrado = re.search(r"\{.*\}", mensaje.get("content") or "", re.S)
        spec = json.loads(encontrado.group(0)) if encontrado else {}
    except Exception:
        return {}
    return spec if isinstance(spec, dict) else {}
