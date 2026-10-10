"""From a verified trace to a recipe that is safe to replay without a model.

A recipe is only the part of a task that cannot change data: getting to the right screen and, in
creation and query tasks, filling fields whose value comes from the TaskSpec. It always stops
before the action that saves, confirms or exports, so that action still goes through the
operator's gates, review and verification. The rules are structural, not about this app:

- Steps are grouped into actions. A fill is click + ctrl+a + type, which is what the operator emits.
- A typed text must map to a TaskSpec slot (a value or the referent); otherwise the text was
  specific to that task and the recipe stops there.
- Once fields were filled, the next lone click may be the save, so the recipe stops there. If that
  click came after a single fill, the fill was a search waiting for its button: it is dropped too,
  so the operator never lands on a half-done search it did not start (measured 13:48: it skipped
  reading the results and the verifier sent it back).
- Only Tab and Shift+Tab are replayed among keys. Enter can submit a form.
- In modifications, only the search by referent is filled: editing a field of an existing record
  must go through the operator, so the recovery journal records its previous value.
- Steps flagged `gated` (they went through the change gate) are never replayed.
- Delete, export and "other" tasks get no recipe, unless the trace marks its gated steps.
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from dataclasses import asdict, replace

from agent.contracts import Step

REPLAYABLE = {"crear", "consultar", "modificar"}
SAFE_KEYS = {"tab", "shift+tab"}
MAX_RECIPE_STEPS = 30
REFERENT = "referente"


def normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", plain.lower())


def task_key(spec: dict) -> tuple[str, str]:
    return normalize(spec.get("tipo") or "otro") or "otro", normalize(spec.get("entidad") or "") or "registro"


def slot_values(spec: dict) -> dict[str, str]:
    """Normalized slot name -> literal value, including the referent used to find a record."""
    values = {normalize(k): str(v) for k, v in (spec.get("valores") or {}).items() if v not in (None, "")}
    if spec.get("referente"):
        values.setdefault(REFERENT, str(spec["referente"]))
    return values


def resolve_slot(step: Step, spec: dict) -> str | None:
    """Which slot this typed text came from, judged against the spec of the task that produced it."""
    values = slot_values(spec)
    if step.slot and values.get(normalize(step.slot)) == step.text:
        return normalize(step.slot)
    for name, value in values.items():
        if value == step.text:
            return name
    return None


def is_fill(steps: list[Step], i: int) -> bool:
    return (
        i + 2 < len(steps)
        and steps[i].kind == "click"
        and steps[i + 1].kind == "key"
        and (steps[i + 1].key or "").lower() == "ctrl+a"
        and steps[i + 2].kind == "type"
    )


def group_actions(steps: list[Step]) -> list[list[Step]]:
    actions, i = [], 0
    while i < len(steps):
        size = 3 if is_fill(steps, i) else 1
        actions.append(steps[i : i + size])
        i += size
    return actions


def safe_prefix(trace: list[Step], spec: dict) -> list[Step]:
    """The longest start of the trace that is safe to replay for another task of the same kind."""
    tipo, _ = task_key(spec)
    gate_aware = any(getattr(s, "gated", False) for s in trace)
    if tipo not in REPLAYABLE and not (gate_aware and tipo == "exportar"):
        return []
    out: list[Step] = []
    filled = False
    block_start, block_fills = 0, 0  # the run of fills at the end of `out`
    previous: Step | None = None
    for action in group_actions(trace):
        if any(getattr(s, "gated", False) for s in action):
            break
        first = action[0]
        if len(action) == 3 or first.kind == "type":
            typed = action[-1]
            slot = resolve_slot(typed, spec)
            if slot is None or (tipo == "modificar" and slot != REFERENT):
                break
            if len(action) == 3 and first.anchor_png is None:
                break
            if block_fills == 0:
                block_start = len(out)
            out.extend(action[:-1] + [replace(typed, slot=slot)])
            filled = True
            block_fills += 1
        elif first.kind in ("click", "double_click"):
            if filled and block_fills == 1:
                del out[block_start:]  # a lone fill followed by a click is a search: leave it whole to the operator
            if filled or first.anchor_png is None:
                break
            # A click that changed nothing, right before the same click again, was a retry.
            if previous is not None and previous.kind == first.kind and previous.screen_changed_px == 0 \
                    and (previous.x, previous.y) == (first.x, first.y):
                out.pop()
            out.append(first)
        elif first.kind == "key" and (first.key or "").lower() in SAFE_KEYS:
            out.append(first)
        elif first.kind == "scroll" and not filled:
            out.append(first)
        else:
            break
        previous = first
        if len(out) >= MAX_RECIPE_STEPS:
            break
    # A recipe that types nothing and clicks nothing is not worth a replay.
    return out if any(s.kind in ("click", "double_click", "type") for s in out) else []


def dump_steps(steps: list[Step]) -> str:
    rows = []
    for step in steps:
        row = asdict(step)
        if row.get("anchor_png") is not None:
            row["anchor_png"] = base64.b64encode(row["anchor_png"]).decode()
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False)


def load_steps(text: str) -> list[Step]:
    fields = set(Step.__dataclass_fields__)
    steps = []
    for row in json.loads(text):
        if row.get("anchor_png"):
            row["anchor_png"] = base64.b64decode(row["anchor_png"])
        steps.append(Step(**{k: v for k, v in row.items() if k in fields}))
    return steps
