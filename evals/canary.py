"""Canario: detecta si la app cambió (el incidente de las 15:30) comparando píxeles, sin modelo.

Cada ronda resetea NUESTRO escritorio de práctica, toma la pantalla inicial y la compara con la
base guardada. Tras un reset la pantalla inicial es idéntica al píxel (el cliente RFB pide el
puntero aparte, así que la flecha no sale en la captura), de modo que cualquier región que cambie es
un cambio de la app: un menú renombrado mueve ~230 píxeles. Cuando lo hay: alerta ruidosa (campana, banner y
notificación de macOS), imágenes con las regiones marcadas y una línea con hora en log.jsonl.

Uso:
  uv run python -m evals.canary --una-vez          # una ronda (la primera guarda la base)
  uv run python -m evals.canary --cada 20          # una ronda cada 20 minutos
  uv run python -m evals.canary --nueva-base       # aceptar la pantalla actual como base
  uv run python -m evals.canary --sin-reset        # no resetea: compara solo las barras de arriba

No lo corras mientras corren evals en el mismo escritorio: el reset las interrumpe. Owner: Juanda.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageDraw

from agent.driver import DriverRFB
from evals.run import RAIZ, _base_http, _requerida

CARPETA = RAIZ / "runs" / "canary"
PIXEL = 40           # diferencia por canal que cuenta como píxel cambiado
CELDA = 16           # las regiones se reportan en celdas de 16x16 px
MIN_PX_CELDA = 4     # píxeles cambiados para marcar una celda
RUIDO_PX = 50        # menos que esto es ruido de dibujo, no un cambio de la app
BANDA = 64           # barras de título y menú


def comparar(base: Image.Image, actual: Image.Image, solo_banda: bool) -> dict:
    a = np.asarray(base.convert("RGB"), np.int16)
    b = np.asarray(actual.convert("RGB"), np.int16)
    if a.shape != b.shape:
        return {"cambio": True, "px": int(a.shape[0] * a.shape[1]), "fraccion": 1.0, "regiones": [], "motivo": "cambió el tamaño de la pantalla"}
    if solo_banda:
        a, b = a[:BANDA], b[:BANDA]
    mascara = np.abs(a - b).max(axis=2) > PIXEL
    alto, ancho = mascara.shape
    regiones = []
    for y in range(0, alto, CELDA):
        for x in range(0, ancho, CELDA):
            if mascara[y : y + CELDA, x : x + CELDA].sum() >= MIN_PX_CELDA:
                regiones.append((x, y, min(x + CELDA, ancho), min(y + CELDA, alto)))
    px = int(mascara.sum())
    return {"cambio": px > RUIDO_PX, "px": px, "fraccion": round(px / mascara.size, 5), "regiones": regiones,
            "caja": [min(r[0] for r in regiones), min(r[1] for r in regiones), max(r[2] for r in regiones),
                     max(r[3] for r in regiones)] if regiones else None}


def marcar(imagen: Image.Image, regiones: list) -> Image.Image:
    salida = imagen.convert("RGB").copy()
    dibujo = ImageDraw.Draw(salida)
    for caja in regiones:
        dibujo.rectangle(caja, outline=(255, 0, 0), width=2)
    return salida


def alertar(texto: str) -> None:
    banner = "!" * 78
    print(f"\a\a\a\n{banner}\n{texto}\n{banner}\n", flush=True)
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f'display notification "{texto}" with title "Canario: la app cambió" sound name "Glass"'],
                       check=False, capture_output=True)


async def capturar(url_ws: str, password: str, resetear: bool) -> Image.Image:
    if resetear:
        async with httpx.AsyncClient(timeout=60) as http:
            token = {"Authorization": f"Bearer {_requerida('DESKTOP_TOKEN')}"}
            (await http.post(f"{_base_http(url_ws)}/__reset", headers=token)).raise_for_status()
        await asyncio.sleep(5)  # el navegador del escritorio se reinicia
    driver = DriverRFB()
    try:
        await driver.connect(url_ws, password)
        await driver.settle()
        return await driver.screenshot()
    finally:
        await driver.close()


async def ronda(url_ws: str, password: str, args) -> dict:
    hora = time.strftime("%H:%M:%S")
    sello = time.strftime("%H%M%S")
    actual = await capturar(url_ws, password, resetear=not args.sin_reset)
    base_png = CARPETA / "base.png"
    if args.nueva_base or not base_png.exists():
        actual.save(base_png)
        registro = {"hora": hora, "evento": "base_guardada"}
    else:
        resultado = comparar(Image.open(base_png), actual, solo_banda=args.sin_reset)
        registro = {"hora": hora, "evento": "cambio" if resultado["cambio"] else "igual",
                    **{k: v for k, v in resultado.items() if k != "regiones"}, "celdas": len(resultado["regiones"])}
        if resultado["cambio"]:
            actual.save(CARPETA / f"{sello}-actual.png")
            marcar(actual, resultado["regiones"]).save(CARPETA / f"{sello}-cambios.png")
            alertar(f"{hora}: cambió el {resultado['fraccion']:.2%} de la pantalla inicial, región {resultado['caja']}. "
                    f"Ver runs/canary/{sello}-cambios.png")
    with (CARPETA / "log.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(registro, ensure_ascii=False) + "\n")
    print(json.dumps(registro, ensure_ascii=False), flush=True)
    return registro


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cada", type=float, default=20, help="minutos entre rondas")
    parser.add_argument("--una-vez", action="store_true")
    parser.add_argument("--nueva-base", action="store_true")
    parser.add_argument("--sin-reset", action="store_true")
    args = parser.parse_args()
    load_dotenv(RAIZ / ".env")
    url_ws, password = _requerida("DESKTOP_URL"), _requerida("DESKTOP_PASSWORD")
    CARPETA.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            await ronda(url_ws, password, args)
        except Exception as error:  # una ronda caída no detiene el canario, pero se ve
            alertar(f"{time.strftime('%H:%M:%S')}: el canario no pudo leer el escritorio ({type(error).__name__}).")
        if args.una_vez:
            break
        args.nueva_base = False
        await asyncio.sleep(args.cada * 60)


if __name__ == "__main__":
    asyncio.run(main())
