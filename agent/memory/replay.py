"""Replays a recipe without calling a model, checking the screen before every click.

Before each click the recorded 48x48 anchor has to be on screen within +-3 px; if not, the replay
stops and the operator continues from the current screen. Typed text is taken from the new task's
TaskSpec by slot, never from the old task. When the driver exposes the core's desktop helper, a
fill is done with it, so the field is read back and a mismatch stops the replay too.
"""

from __future__ import annotations

import time
from dataclasses import replace

from agent.contracts import Driver, Recipe, ReplayResult, Step

from .fingerprint import locate_anchor
from .recipes import group_actions, slot_values

REPLAY_BUDGET_S = 45


async def replay(recipe: Recipe, spec: dict, driver: Driver) -> tuple[ReplayResult, list[Step]]:
    """Returns the result and the steps actually executed (with the coordinates and text used)."""
    values = slot_values(spec)
    helper = getattr(driver, "esc", None)
    done, executed = 0, []
    limit = time.monotonic() + REPLAY_BUDGET_S

    def stop(reason: str) -> tuple[ReplayResult, list[Step]]:
        return ReplayResult(False, done, done, reason), executed

    for action in group_actions(recipe.steps):
        if time.monotonic() > limit:
            return stop("se acabó el tiempo reservado para la receta")
        first = action[0]
        x, y = first.x, first.y
        if first.kind in ("click", "double_click"):
            await driver.settle()
            found = locate_anchor(await driver.screenshot(), first.anchor_png, first.x, first.y) if first.anchor_png else None
            if found is None:
                return stop(f"el paso {done + 1} ya no coincide con la pantalla (la interfaz cambió o el estado es otro)")
            x, y = found[0], found[1]
        text = None
        if action[-1].kind == "type":
            text = values.get(action[-1].slot or "")
            if text is None:
                return stop(f"la tarea no trae el dato «{action[-1].slot}» que la receta escribe en el paso {done + 1}")

        if len(action) == 3:  # click + ctrl+a + type
            if helper is not None and hasattr(helper, "rellenar"):
                kept = await helper.rellenar(x, y, text)
                if kept is not None and kept != text:
                    return stop(f"el campo del paso {done + 1} no quedó con el valor pedido")
            else:
                await driver.click(x, y)
                await driver.key("ctrl+a")
                await driver.type_text(text)
            executed += [replace(action[0], x=x, y=y, model=None), replace(action[1], model=None),
                         replace(action[2], text=text, model=None)]
        elif first.kind in ("click", "double_click"):
            await driver.click(x, y, double=first.kind == "double_click")
            executed.append(replace(first, x=x, y=y, model=None))
        elif first.kind == "type":
            await driver.type_text(text)
            executed.append(replace(first, text=text, model=None))
        elif first.kind == "key":
            await driver.key(first.key)
            executed.append(replace(first, model=None))
        elif first.kind == "scroll":
            await driver.scroll(first.x, first.y, first.direction or "down")
            executed.append(replace(first, model=None))
        else:
            return stop(f"paso de tipo desconocido: {first.kind}")
        done += len(action)
        await driver.settle()
    return ReplayResult(True, done), executed
