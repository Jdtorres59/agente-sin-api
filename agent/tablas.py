"""Tablas leídas del texto de la pantalla: el modelo las pide, el código las cuenta.

El texto copiado de una pantalla trae las filas de una tabla como líneas con tabuladores.
"""

import re
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Tabla:
    columnas: list[str] = field(default_factory=list)
    filas: list[list[str]] = field(default_factory=list)
    paginas: int = 0

    def agregar_pagina(self, texto: str) -> int:
        """Suma las filas de una página. Devuelve cuántas filas nuevas trajo."""
        lineas = [linea for linea in texto.splitlines() if "\t" in linea]
        if not lineas:
            return 0
        celdas = [[c.strip() for c in linea.split("\t")] for linea in lineas]
        if not self.columnas:
            self.columnas = celdas[0]
        nuevas = [fila for fila in celdas if fila != self.columnas]
        self.filas.extend(nuevas)
        self.paginas += 1
        return len(nuevas)

    def _indice(self, columna: str) -> int:
        normal = [c.lower() for c in self.columnas]
        if columna.lower() in normal:
            return normal.index(columna.lower())
        parecidas = [i for i, c in enumerate(normal) if columna.lower() in c]
        if len(parecidas) == 1:
            return parecidas[0]
        raise ValueError(f"La columna «{columna}» no existe. Columnas: {self.columnas}")

    def filtrar(self, condiciones: list[dict]) -> list[list[str]]:
        filas = self.filas
        for condicion in condiciones:
            i = self._indice(condicion["columna"])
            operador, valor = condicion.get("operador", "igual"), str(condicion["valor"])
            filas = [f for f in filas if i < len(f) and _cumple(f[i], operador, valor)]
        return filas

    def sumar(self, columna: str, filas: list[list[str]]) -> float:
        i = self._indice(columna)
        return sum(_numero(f[i]) or 0.0 for f in filas if i < len(f))

    def como_texto(self, maximo: int = 120) -> str:
        lineas = ["\t".join(self.columnas)] + ["\t".join(f) for f in self.filas[:maximo]]
        if len(self.filas) > maximo:
            lineas.append(f"… ({len(self.filas) - maximo} filas más, no mostradas)")
        return "\n".join(lineas)


def _fecha(texto: str) -> date | None:
    for patron, orden in ((r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", (3, 2, 1)), (r"(\d{4})-(\d{1,2})-(\d{1,2})", (1, 2, 3))):
        m = re.fullmatch(patron + r"(?:\s.*)?", texto.strip())
        if m:
            try:
                return date(*(int(m.group(g)) for g in orden))
            except ValueError:
                return None
    return None


def _numero(texto: str) -> float | None:
    limpio = re.sub(r"[^\d,.\-]", "", texto)
    if not re.search(r"\d", limpio):
        return None
    if "," in limpio and "." in limpio:  # el último separador es el decimal
        decimal = "," if limpio.rfind(",") > limpio.rfind(".") else "."
        limpio = limpio.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif re.fullmatch(r"-?\d{1,3}([.,]\d{3})+", limpio):  # solo separadores de miles
        limpio = re.sub(r"[.,]", "", limpio)
    else:
        limpio = limpio.replace(",", ".")
    try:
        return float(limpio)
    except ValueError:
        return None


def _cumple(celda: str, operador: str, valor: str) -> bool:
    if operador == "igual":
        return celda.strip() == valor.strip()
    if operador == "distinto":
        return celda.strip() != valor.strip()
    if operador == "contiene":
        return valor.strip().lower() in celda.lower()
    if operador in ("menor", "mayor"):
        a, b = _fecha(celda), _fecha(valor)
        if a is None or b is None:
            a, b = _numero(celda), _numero(valor)
        if a is None or b is None:
            return False
        return a < b if operador == "menor" else a > b
    raise ValueError(f"Operador desconocido: {operador}")
