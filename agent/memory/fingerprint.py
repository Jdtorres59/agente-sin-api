"""Visual identity of an app and of the controls the agent clicked.

Two comparisons, both on pixels and without a model:
- App fingerprint: a 160x100 color thumbnail of the first screen plus a thumbnail of the top
  band (title and menu bars). When the band has structure it decides the app, so another page of
  the same app still matches (its band is identical, its full screen differs ~25%). After a reset
  the first screen of the same app is identical to the pixel, so a small difference there (drift)
  is a change in the app itself: the signal the incident detector looks for.
- Anchor: the 48x48 crop around a click, taken before acting. A recipe step is only replayed if
  the crop is still there (searching +-3 px). Two menu labels on the same bar differ ~9.5%, so
  3% separates "same control" from "neighbor control".
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

THUMB = (160, 100)
BAND_HEIGHT = 64
BAND_THUMB = (128, 8)
COLOR_TOLERANCE = 24     # channel levels that count as a changed thumbnail cell
SAME_APP_FULL = 0.06     # fraction of changed cells to still be the same first screen
SAME_APP_BAND = 0.05     # same, for the title/menu band (decides when the band has structure)
DRIFT = 0.002            # same first screen, but changed: above a moved cursor, below another page

ANCHOR_TOLERANCE = 32    # per-channel difference that counts as a changed pixel
ANCHOR_MAX_CHANGED = 0.03
ANCHOR_SEARCH_PX = 3


def thumbnails(img: Image.Image) -> tuple[bytes, bytes]:
    """Full screen and top band, in color: two colors can have the same gray."""
    rgb = img.convert("RGB")
    top = rgb.crop((0, 0, rgb.width, min(BAND_HEIGHT, rgb.height)))
    return rgb.resize(THUMB, Image.BILINEAR).tobytes(), top.resize(BAND_THUMB, Image.BILINEAR).tobytes()


def distance(a: bytes, b: bytes) -> float:
    """Fraction of thumbnail cells where some channel changed (0 = identical, 1 = everything)."""
    x = np.frombuffer(a, np.uint8).astype(np.int16).reshape(-1, 3)
    y = np.frombuffer(b, np.uint8).astype(np.int16).reshape(-1, 3)
    if x.shape != y.shape:
        return 1.0
    return float((np.abs(x - y).max(axis=1) > COLOR_TOLERANCE).mean())


def has_structure(band: bytes) -> bool:
    """A flat band (no title bar) cannot tell apps apart, so it is not used to match."""
    return float(np.frombuffer(band, np.uint8).std()) > 12


def png_bytes(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def anchor_score(screen: Image.Image, anchor: Image.Image, x: int, y: int) -> float:
    w, h = anchor.size
    crop = screen.crop((x - w // 2, y - h // 2, x - w // 2 + w, y - h // 2 + h)).convert("RGB")
    diff = np.abs(np.asarray(crop, np.int16) - np.asarray(anchor.convert("RGB"), np.int16)).max(axis=2)
    return float((diff > ANCHOR_TOLERANCE).mean())


def locate_anchor(screen: Image.Image, anchor_png: bytes, x: int, y: int) -> tuple[int, int, float] | None:
    """Where the recorded control is now (within +-3 px of where it was), or None if it is gone."""
    anchor = Image.open(io.BytesIO(anchor_png))
    best: tuple[int, int, float] | None = None
    for dy in range(-ANCHOR_SEARCH_PX, ANCHOR_SEARCH_PX + 1):
        for dx in range(-ANCHOR_SEARCH_PX, ANCHOR_SEARCH_PX + 1):
            score = anchor_score(screen, anchor, x + dx, y + dy)
            if best is None or score < best[2] or (score == best[2] and abs(dx) + abs(dy) < abs(best[0] - x) + abs(best[1] - y)):
                best = (x + dx, y + dy, score)
    return best if best is not None and best[2] <= ANCHOR_MAX_CHANGED else None
