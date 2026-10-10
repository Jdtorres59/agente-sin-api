"""Traza por tarea: eventos en JSONL y capturas, para depurar y para el debrief."""

import json
import time
from pathlib import Path

from PIL import Image


class Traza:
    def __init__(self, carpeta: Path):
        self.dir = Path(carpeta)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._inicio = time.monotonic()

    def evento(self, **datos) -> None:
        datos["t"] = round(time.monotonic() - self._inicio, 2)
        with open(self.dir / "pasos.jsonl", "a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(datos, ensure_ascii=False) + "\n")

    def captura(self, paso: int, imagen: Image.Image) -> None:
        imagen.save(self.dir / f"paso-{paso:02d}.png")
