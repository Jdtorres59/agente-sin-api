"""Minimal VNC (RFB 3.8) client over a websocket.

Everything here is deterministic: connecting, keeping a local copy of the framebuffer,
and sending mouse and keyboard events with timings the remote desktop accepts.
"""

import asyncio
import struct
import time

import numpy as np
import websockets
from Crypto.Cipher import DES
from PIL import Image

# Timings learned from the desktop: clicks shorter than 100 ms are ignored and the
# screen can take up to 1 s to repaint after an action.
CLICK_HOLD_S = 0.15
KEY_GAP_S = 0.02

SPECIAL_KEYS = {
    "enter": 0xFF0D, "return": 0xFF0D, "tab": 0xFF09, "backspace": 0xFF08,
    "escape": 0xFF1B, "esc": 0xFF1B, "delete": 0xFFFF, "home": 0xFF50,
    "end": 0xFF57, "left": 0xFF51, "up": 0xFF52, "right": 0xFF53, "down": 0xFF54,
    "pageup": 0xFF55, "pagedown": 0xFF56, "space": 0x0020,
    "ctrl": 0xFFE3, "control": 0xFFE3, "shift": 0xFFE1, "alt": 0xFFE9,
    "meta": 0xFFE7, "f5": 0xFFC2,
}


class VNCError(Exception):
    pass


def _keysym(char: str) -> int:
    code = ord(char)
    if char == "\n":
        return SPECIAL_KEYS["enter"]
    if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF:
        return code
    return 0x01000000 + code


def changed_pixels(a: np.ndarray, b: np.ndarray, threshold: int = 30) -> int:
    """Count pixels whose color moved more than `threshold` between two frames."""
    diff = np.abs(a[:, :, :3].astype(np.int16) - b[:, :, :3].astype(np.int16)).sum(axis=2)
    return int((diff > threshold).sum())


def _vnc_auth_response(password: str, challenge: bytes) -> bytes:
    # VNC auth uses DES with the bits of each key byte reversed.
    key = password.encode("latin-1")[:8].ljust(8, b"\0")
    key = bytes(int(f"{b:08b}"[::-1], 2) for b in key)
    return DES.new(key, DES.MODE_ECB).encrypt(challenge)


class VNCClient:
    def __init__(self, url: str, password: str):
        self.url = url
        self.password = password
        self.width = 0
        self.height = 0
        self.fb: np.ndarray | None = None
        self.last_update = 0.0
        self._ws = None
        self._buf = bytearray()
        self._data = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._closed = False

    # ---- connection -------------------------------------------------------------

    async def connect(self, timeout: float = 20) -> None:
        self._ws = await asyncio.wait_for(
            websockets.connect(self.url, subprotocols=["binary"], max_size=None,
                               open_timeout=timeout, ping_interval=20),
            timeout,
        )
        self._tasks.append(asyncio.create_task(self._pump()))
        await asyncio.wait_for(self._handshake(), timeout)
        self._tasks.append(asyncio.create_task(self._updates()))
        await self.wait_for_frame(timeout)

    async def close(self) -> None:
        self._closed = True
        for t in self._tasks:
            t.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def _pump(self) -> None:
        async for msg in self._ws:
            if isinstance(msg, str):
                msg = msg.encode("latin-1")
            self._buf.extend(msg)
            self._data.set()

    async def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            if self._closed:
                raise VNCError("connection closed")
            self._data.clear()
            await self._data.wait()
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    async def _send(self, data: bytes) -> None:
        await self._ws.send(data)

    async def _handshake(self) -> None:
        version = await self._read(12)
        if not version.startswith(b"RFB "):
            raise VNCError(f"not an RFB server: {version!r}")
        await self._send(b"RFB 003.008\n")
        (count,) = struct.unpack("!B", await self._read(1))
        if count == 0:
            raise VNCError(await self._reason())
        types = await self._read(count)
        if 2 in types:
            await self._send(b"\x02")
            challenge = await self._read(16)
            await self._send(_vnc_auth_response(self.password, challenge))
        elif 1 in types:
            await self._send(b"\x01")
        else:
            raise VNCError(f"unsupported security types {list(types)}")
        (result,) = struct.unpack("!I", await self._read(4))
        if result != 0:
            raise VNCError(f"authentication failed: {await self._reason()}")

        await self._send(b"\x01")  # shared session
        self.width, self.height = struct.unpack("!HH", await self._read(4))
        await self._read(16)  # server pixel format, replaced below
        (name_len,) = struct.unpack("!I", await self._read(4))
        await self._read(name_len)
        self.fb = np.zeros((self.height, self.width, 4), dtype=np.uint8)

        # 32 bpp little endian true colour: bytes arrive as B, G, R, X.
        pixel_format = struct.pack("!BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
        await self._send(b"\x00\x00\x00\x00" + pixel_format)
        encodings = [1, 0]  # CopyRect, Raw
        await self._send(struct.pack("!BxH", 2, len(encodings))
                         + b"".join(struct.pack("!i", e) for e in encodings))
        await self._request(incremental=False)

    async def _reason(self) -> str:
        (n,) = struct.unpack("!I", await self._read(4))
        return (await self._read(n)).decode("latin-1", "replace")

    async def _request(self, incremental: bool) -> None:
        await self._send(struct.pack("!BBHHHH", 3, int(incremental), 0, 0, self.width, self.height))

    async def _updates(self) -> None:
        while not self._closed:
            (kind,) = struct.unpack("!B", await self._read(1))
            if kind == 0:
                await self._framebuffer_update()
                await self._request(incremental=True)
            elif kind == 1:
                _, _, n = struct.unpack("!BHH", await self._read(5))
                await self._read(n * 6)
            elif kind == 2:
                pass
            elif kind == 3:
                _, n = struct.unpack("!3sI", await self._read(7))
                await self._read(n)
            else:
                raise VNCError(f"unknown server message {kind}")

    async def _framebuffer_update(self) -> None:
        _, rects = struct.unpack("!BH", await self._read(3))
        for _ in range(rects):
            x, y, w, h, enc = struct.unpack("!HHHHi", await self._read(12))
            if enc == 0:
                data = await self._read(w * h * 4)
                self.fb[y:y + h, x:x + w] = np.frombuffer(data, np.uint8).reshape(h, w, 4)
            elif enc == 1:
                sx, sy = struct.unpack("!HH", await self._read(4))
                self.fb[y:y + h, x:x + w] = self.fb[sy:sy + h, sx:sx + w].copy()
            else:
                raise VNCError(f"unexpected encoding {enc}")
        self.last_update = time.monotonic()

    # ---- screen -----------------------------------------------------------------

    async def wait_for_frame(self, timeout: float = 10) -> None:
        start = time.monotonic()
        while self.last_update == 0.0:
            if time.monotonic() - start > timeout:
                raise VNCError("no framebuffer received")
            await asyncio.sleep(0.05)

    async def wait_stable(self, min_wait: float = 0.5, window: float = 0.6,
                          max_wait: float = 4.0, tolerance_px: int = 80) -> bool:
        """Wait until the screen stops changing, so a screenshot is not the old frame.

        A blinking text caret changes a few pixels forever, so "stable" means fewer than
        `tolerance_px` changed pixels over `window` seconds. Returns False on timeout.
        """
        start = time.monotonic()
        await asyncio.sleep(min_wait)
        ref, ref_t = self.snapshot(), time.monotonic()
        while time.monotonic() - start < max_wait:
            await asyncio.sleep(0.15)
            cur = self.snapshot()
            if changed_pixels(ref, cur) > tolerance_px:
                ref, ref_t = cur, time.monotonic()
            elif time.monotonic() - ref_t >= window:
                return True
        return False

    def screenshot(self) -> Image.Image:
        return Image.fromarray(self.fb[:, :, 2::-1].copy(), "RGB")

    def snapshot(self) -> np.ndarray:
        return self.fb.copy()

    # ---- input ------------------------------------------------------------------

    async def _pointer(self, mask: int, x: int, y: int) -> None:
        x = max(0, min(self.width - 1, int(x)))
        y = max(0, min(self.height - 1, int(y)))
        await self._send(struct.pack("!BBHH", 5, mask, x, y))

    async def move(self, x: int, y: int) -> None:
        await self._pointer(0, x, y)

    async def click(self, x: int, y: int, button: int = 1, double: bool = False) -> None:
        mask = {1: 1, 2: 2, 3: 4}[button]
        await self._pointer(0, x, y)
        await asyncio.sleep(0.05)
        for _ in range(2 if double else 1):
            await self._pointer(mask, x, y)
            await asyncio.sleep(CLICK_HOLD_S)
            await self._pointer(0, x, y)
            await asyncio.sleep(0.08)

    async def scroll(self, x: int, y: int, direction: str = "down", clicks: int = 3) -> None:
        mask = 8 if direction == "up" else 16
        await self._pointer(0, x, y)
        for _ in range(clicks):
            await self._pointer(mask, x, y)
            await asyncio.sleep(0.03)
            await self._pointer(0, x, y)
            await asyncio.sleep(0.05)

    async def _key_event(self, keysym: int, down: bool) -> None:
        await self._send(struct.pack("!BBxxI", 4, int(down), keysym))

    async def key(self, combo: str) -> None:
        """Press a key or a combination like 'ctrl+a', 'enter', 'shift+tab'."""
        parts = [p.strip().lower() for p in combo.split("+")]
        syms = [SPECIAL_KEYS[p] if p in SPECIAL_KEYS else _keysym(p) for p in parts]
        for s in syms:
            await self._key_event(s, True)
            await asyncio.sleep(KEY_GAP_S)
        for s in reversed(syms):
            await self._key_event(s, False)
            await asyncio.sleep(KEY_GAP_S)

    async def type_text(self, text: str) -> None:
        for ch in text:
            s = _keysym(ch)
            await self._key_event(s, True)
            await asyncio.sleep(KEY_GAP_S)
            await self._key_event(s, False)
            await asyncio.sleep(KEY_GAP_S)
