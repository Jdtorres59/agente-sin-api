"""El núcleo no puede saber nada de la app de práctica: se evalúa en otra.

Falla si el código o los prompts de `agent/` mencionan su nombre, sus menús o sus entidades.
"""

import re
from pathlib import Path

FUENTES = Path(__file__).resolve().parents[1] / "agent"
PROHIBIDO = [
    "sigecom", "andina", "cartera", "maestros", "bitácora",
    "clientes", "facturas", "nit", "vencida",
]  # fmt: skip  ("pedidos", "bitacora" y "revertir" quedan fuera: son palabras corrientes que usa el propio código)


def test_agent_has_no_practice_app_vocabulary() -> None:
    hallazgos = []
    for ruta in sorted(FUENTES.glob("*.py")):
        for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
            for palabra in PROHIBIDO:
                if re.search(rf"\b{palabra}\b", linea, re.IGNORECASE):
                    hallazgos.append(f"{ruta.name}:{numero}: «{palabra}»")
    assert not hallazgos, "Vocabulario de la app de práctica en el núcleo:\n" + "\n".join(hallazgos)
