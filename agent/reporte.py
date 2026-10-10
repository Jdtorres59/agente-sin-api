"""Arma el resumen final con formato fijo y sin secretos."""

import re


def _sin_linea_respuesta(texto: str) -> str:
    """El evaluador busca una línea `respuesta:`; solo el código puede escribirla."""
    return re.sub(r"(?im)^\s*respuesta\s*:", "resultado:", texto or "").strip()


def componer(estado: str, hechos: str, respuesta: str, razon: str, secretos: list[str]) -> str:
    lineas = [_sin_linea_respuesta(hechos) or "Sin acciones registradas."]
    if estado == "fallida" and razon:
        lineas.append("motivo: " + _sin_linea_respuesta(razon))
    if estado == "terminada" and respuesta.strip():
        lineas.append("respuesta: " + " ".join(respuesta.split()))
    resumen = "\n".join(lineas)
    for secreto in secretos:
        if secreto and len(secreto) >= 4:
            resumen = resumen.replace(secreto, "[oculto]")
    return resumen
